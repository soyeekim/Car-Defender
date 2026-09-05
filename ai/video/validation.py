"""Video Result Validation / Completion Gate (가이드 45, 53, 63~71절).

- validate_video_result   : Fast Path 통과 여부 (collision pair 중심)
- evaluate_video_completion: 4개 gate + completion score
- build_focus_targets     : 재분석 focus 목록 생성
- should_use_cv_tracking  : CV tracking fallback 조건
- user_confirmation_question: 영상으로 확정 불가 시 사용자 객관적 확인 질문
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from common.timeutil import parse_timestamp
from video.schemas import AnalysisCompletion, VideoResult

_STATUS_RANK = {"CONFIRMED": 2, "INFERRED": 1, "UNKNOWN": 0}
_UNKNOWN_VALUES = {None, "", "unknown", "none", "null", "n/a", "불명", "미확인"}


@dataclass
class FocusTarget:
    kind: str  # collision_pair | signal | lane | identity | collision_parts | road_type | custom
    question: str
    prompt_name: str = "focus_analysis"
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None
    reasons: list[str] = field(default_factory=list)

    @property
    def cache_key(self) -> str:
        return f"{self.kind}:{self.start_sec}:{self.end_sec}:{self.question[:80]}"


def _known(value: Optional[str]) -> bool:
    return value is not None and str(value).strip().lower() not in _UNKNOWN_VALUES


def collision_window_seconds(result: VideoResult, padding: float = 1.0) -> tuple[Optional[float], Optional[float]]:
    window = result.collision_window
    start = parse_timestamp(window.start)
    end = parse_timestamp(window.end)
    center = parse_timestamp(window.most_likely_timestamp) or parse_timestamp(result.collision_pair.timestamp)
    if start is None and center is not None:
        start = center - padding
    if end is None and center is not None:
        end = center + padding
    if start is not None:
        start = max(0.0, start - padding * 0.5)
    if end is not None and result.duration_sec:
        end = min(float(result.duration_sec), end + padding * 0.5)
    elif end is not None:
        end = end + padding * 0.5
    return start, end


def participant_check(result: VideoResult, threshold: float) -> list[str]:
    reasons = []
    inventory = set(result.vehicle_ids())
    participants = result.collision_pair.participants
    if not inventory:
        reasons.append("vehicle_inventory_empty")
    if len(participants) != 2:
        reasons.append("collision_participants_not_identified")
    elif len(set(participants)) != 2:
        reasons.append("collision_participants_duplicate")
    if participants and not set(participants).issubset(inventory):
        reasons.append("collision_participants_not_in_inventory")
    if set(participants) & set(result.collision_pair.non_participants):
        reasons.append("participant_marked_as_non_participant")
    if result.collision_pair.confidence < threshold:
        reasons.append("collision_pair_low_confidence")
    if not result.vehicle_identity_consistent:
        reasons.append("vehicle_identity_inconsistent")
    return reasons


def structure_check(result: VideoResult) -> list[str]:
    reasons = []
    if not result.road_environment.road_type.is_known():
        reasons.append("road_type_unknown")
    participants = result.collision_pair.participants
    for vehicle_id in participants:
        vehicle = result.get_vehicle(vehicle_id)
        if vehicle is None or not _known(vehicle.movement):
            reasons.append(f"movement_unknown:{vehicle_id}")
    if not result.collision_window.detected() and not result.collision_pair.timestamp and not result.collision.timestamp:
        reasons.append("collision_window_missing")
    if not _known(result.collision.collision_type):
        reasons.append("collision_type_unknown")
    if not _known(result.collision.relative_direction):
        other = [
            vehicle for vehicle in result.vehicles if vehicle.id in participants and not vehicle.is_ego
        ]
        if not other or not _known(other[0].entry_direction):
            reasons.append("relative_direction_unknown")
    return reasons


def accident_profile(result: VideoResult) -> str:
    road = (result.road_environment.road_type.value or "").lower()
    intersection = (result.road_environment.intersection_type.value or "").lower()
    signal = (result.road_environment.signal_present.value or "").lower()
    lane_change = any(
        (vehicle.lane_change.value or "").lower() in {"true", "yes", "left", "right", "lane_change_left", "lane_change_right"}
        or (vehicle.movement or "").startswith("lane_change")
        for vehicle in result.vehicles
    )
    if "roundabout" in road or "roundabout" in intersection or "회전" in road:
        return "roundabout"
    if lane_change or "side_swipe" in (result.collision.collision_type or "").lower():
        return "lane_change"
    if "intersection" in road or intersection in {"four_way", "three_way"} or "교차로" in road:
        if signal in {"true", "yes", "present"} or result.road_environment.signal_observations:
            return "signalized_intersection"
        return "unsignalized_intersection"
    if "parking" in road or "주차" in road:
        return "parking_lot"
    return "straight_road"


FACTOR_CHECKLISTS: dict[str, list[tuple[str, list[str]]]] = {
    "signalized_intersection": [
        ("신호등 유무", ["신호등", "signal_present"]),
        ("신호 상태", ["신호 상태", "signal_state", "signal_observation", "green", "red", "yellow", "적색", "녹색", "황색", "신호 색"]),
        ("정지선", ["정지선", "stop_line", "stop line"]),
        ("진입 시점", ["진입", "entry", "entered"]),
        ("진행 방향", ["진행", "직진", "좌회전", "우회전", "movement", "straight", "turn"]),
        ("선진입 관계", ["선진입", "먼저 진입", "entered_first", "first"]),
    ],
    "unsignalized_intersection": [
        ("도로 폭", ["도로 폭", "폭", "width", "wide", "narrow", "차선 수", "lane_count"]),
        ("진입 방향", ["진입", "entry", "direction"]),
        ("선진입 관계", ["선진입", "먼저 진입", "entered_first", "first"]),
        ("진행 방향", ["직진", "좌회전", "우회전", "movement", "straight", "turn"]),
        ("일시정지", ["일시정지", "정지", "stop", "brak"]),
    ],
    "lane_change": [
        ("차선 변경 주체", ["차선 변경", "차로 변경", "lane_change", "lane change"]),
        ("변경 시작 시점", ["시작", "시점", "start", "timestamp", "lane_change"]),
        ("기존 차로 차량 위치", ["선행", "후행", "위치", "ahead", "behind", "position", "lane"]),
        ("방향지시등", ["방향지시등", "깜빡이", "turn_signal", "indicator", "blinker"]),
        ("차량 간 거리", ["거리", "distance", "gap", "간격"]),
    ],
    "roundabout": [
        ("회전 차량", ["회전", "circulating", "inside", "roundabout"]),
        ("진입 차량", ["진입", "entering", "entry"]),
        ("진입 시점", ["시점", "timestamp", "entered", "entry"]),
        ("우선 진행 관계", ["우선", "양보", "priority", "yield", "entered_first", "선진입"]),
        ("충돌 위치", ["충돌", "collision", "part", "부위"]),
    ],
    "parking_lot": [
        ("진행 방향", ["진행", "movement", "후진", "reversing", "straight"]),
        ("정지 여부", ["정지", "stopped", "stop"]),
        ("충돌 부위", ["충돌", "collision", "part", "부위"]),
    ],
    "straight_road": [
        ("진행 방향", ["진행", "movement", "straight", "직진"]),
        ("차간 거리·선후행", ["거리", "distance", "선행", "후행", "ahead", "behind"]),
        ("제동 여부", ["제동", "brak", "감속", "정지"]),
        ("충돌 부위", ["충돌", "collision", "part", "부위", "rear"]),
    ],
}


def _coverage_blob(result: VideoResult) -> str:
    parts: list[str] = []
    for factor in result.fault_relevant_factors:
        parts.append(f"{factor.factor} {factor.note}")
    parts.extend(result.confirmed_facts)
    parts.extend(result.inferred_facts)
    parts.extend(result.unknown_or_unobservable)
    parts.extend(result.uncertain_facts)
    env = result.road_environment
    if env.signal_present.status != "UNKNOWN":
        parts.append("신호등 signal_present")
    if env.signal_observations:
        parts.append("신호 상태 signal_observation")
    if env.stop_line.status != "UNKNOWN":
        parts.append("정지선 stop_line")
    if env.lane_count.status != "UNKNOWN":
        parts.append("차선 수 lane_count")
    for vehicle in result.vehicles:
        if vehicle.entered_intersection_first.status != "UNKNOWN":
            parts.append("선진입 entered_first")
        if vehicle.lane_change.status != "UNKNOWN":
            parts.append("차선 변경 lane_change")
        if vehicle.turn_signal.status != "UNKNOWN":
            parts.append("방향지시등 turn_signal")
        if vehicle.braking.status != "UNKNOWN":
            parts.append("제동 braking")
        if _known(vehicle.movement):
            parts.append(f"진행 방향 movement {vehicle.movement}")
        if _known(vehicle.entry_direction):
            parts.append(f"진입 방향 entry {vehicle.entry_direction}")
    if result.collision.participant_parts:
        parts.append("충돌 부위 collision part")
    if result.collision.timestamp or result.collision_window.detected():
        parts.append("충돌 시점 timestamp")
    return " ".join(parts).lower()


def _keyword_in(blob: str, keyword: str) -> bool:
    keyword = keyword.lower()
    if keyword.isascii():
        # 'red'가 'entered'에 걸리는 식의 부분 일치를 막기 위해 영문 키워드는 단어 경계로 본다
        import re

        return re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", blob) is not None
    return keyword in blob


def factor_coverage(result: VideoResult) -> tuple[float, list[str], str]:
    profile = accident_profile(result)
    checklist = FACTOR_CHECKLISTS.get(profile, FACTOR_CHECKLISTS["straight_road"])
    blob = _coverage_blob(result)
    missing = []
    matched = 0
    for name, keywords in checklist:
        if any(_keyword_in(blob, keyword) for keyword in keywords):
            matched += 1
        else:
            missing.append(name)
    coverage = matched / len(checklist) if checklist else 1.0
    return coverage, missing, profile


def validate_video_result(result: VideoResult, threshold: float = 0.80) -> tuple[bool, list[str]]:
    """가이드 53절 Fast Path 검증. 충돌 pair·identity가 핵심이다."""
    reasons = participant_check(result, threshold)
    return (not reasons), reasons


def evaluate_video_completion(
    result: VideoResult,
    *,
    threshold: float = 0.80,
    score_threshold: int = 80,
) -> AnalysisCompletion:
    reasons: list[str] = []
    gate: dict[str, bool] = {}
    score = 0

    participant_reasons = participant_check(result, threshold)
    gate["collision_participant_identification"] = not participant_reasons
    reasons.extend(participant_reasons)
    if not participant_reasons:
        score += 35
    elif len(result.collision_pair.participants) == 2:
        score += 15

    structure_reasons = structure_check(result)
    gate["basic_accident_structure"] = not structure_reasons
    if structure_reasons:
        reasons.append("basic_accident_structure_missing:" + ",".join(structure_reasons))
    score += int(round(30 * (1 - min(len(structure_reasons), 5) / 5)))

    coverage, missing, profile = factor_coverage(result)
    factors_checked = bool(result.fault_relevant_factors) and coverage >= 0.6
    gate["fault_relevant_factor_check"] = factors_checked
    if not factors_checked:
        reasons.append(
            "fault_relevant_factors_not_checked:" + ",".join(missing) if missing else "fault_relevant_factors_empty"
        )
    score += int(round(25 * coverage)) if result.fault_relevant_factors else 0

    declared = bool(result.unknown_or_unobservable or result.uncertain_facts)
    gate["remaining_uncertainty_declaration"] = declared
    if declared:
        score += 10
    else:
        reasons.append("uncertainty_not_declared")

    complete = (
        gate["collision_participant_identification"]
        and gate["basic_accident_structure"]
        and score >= score_threshold
    )
    return AnalysisCompletion(
        status="VIDEO_ANALYSIS_COMPLETE" if complete else "VIDEO_NEEDS_RECHECK",
        score=min(100, score),
        reasons=reasons,
        gate={**gate, "profile_" + profile: True},
    )


def build_focus_targets(result: VideoResult, threshold: float = 0.80, max_targets: int = 2) -> list[FocusTarget]:
    """1차 결과의 불확실한 쟁점만 좁혀서 focus 목록을 만든다 (가이드 55~56절)."""
    targets: list[FocusTarget] = []
    start, end = collision_window_seconds(result)
    vehicle_ids = result.vehicle_ids()
    participant_reasons = participant_check(result, threshold)

    if participant_reasons:
        pairs = []
        for index, left in enumerate(vehicle_ids):
            for right in vehicle_ids[index + 1 :]:
                pairs.append(f"{left} ↔ {right}")
        if len(vehicle_ids) < 2:
            # 상대 차량을 inventory에 넣지 못한 경우: pair 비교가 아니라 상대 차량 식별이 먼저다
            question = (
                "1차 분석에서 블랙박스 차량 외의 차량을 식별하지 못했다. 충돌(충격) 시각 전후 프레임을 다시 보고, "
                "접촉한 상대 차량을 찾아 vehicle_2로 추가하라(색상·차종·등장 위치·진행 방향 기록). "
                "상대 차량이 화면에 잠깐만 보이거나 일부만 보여도 놓치지 말고, 실제 접촉한 두 차량을 collision_pair로 확정하라. "
                "기존 vehicle ID(" + ", ".join(vehicle_ids) + ")는 변경하지 않는다."
            )
        else:
            question = (
                "충돌 시점 전후에서 실제로 접촉한 두 vehicle ID를 재검증하라. "
                + (f"비교할 pair: {', '.join(pairs)}. " if pairs else "")
                + "기존 vehicle ID를 변경하지 않는다."
            )
        targets.append(
            FocusTarget(
                kind="collision_pair",
                question=question,
                prompt_name="collision_focus",
                start_sec=start,
                end_sec=end,
                reasons=participant_reasons,
            )
        )

    profile = accident_profile(result)
    env = result.road_environment
    signal_unknown = (
        profile == "signalized_intersection"
        and not any(item.status == "CONFIRMED" for item in env.signal_observations)
    )
    if signal_unknown:
        targets.append(
            FocusTarget(
                kind="signal",
                question="충돌 직전 각 차량 진행 방향의 신호등 색상과 정지선 통과 시점을 집중 분석하라.",
                prompt_name="signal_focus",
                start_sec=start,
                end_sec=end,
                reasons=["signal_state_unknown"],
            )
        )

    if profile == "lane_change":
        lane_unknown = all(
            vehicle.lane_change.status == "UNKNOWN" and not _known(vehicle.lane) for vehicle in result.vehicles
        )
        if lane_unknown:
            targets.append(
                FocusTarget(
                    kind="lane",
                    question="충돌 직전 각 차량의 차선 위치, 차선 변경 주체와 시작 시점, 방향지시등을 집중 분석하라.",
                    prompt_name="lane_focus",
                    start_sec=start,
                    end_sec=end,
                    reasons=["lane_change_unknown"],
                )
            )

    if not targets and structure_check(result):
        missing = structure_check(result)
        targets.append(
            FocusTarget(
                kind="structure",
                question=(
                    "다음 항목을 집중 재확인하라: "
                    + ", ".join(missing)
                    + ". 확인할 수 없으면 UNKNOWN으로 유지하라."
                ),
                prompt_name="focus_analysis",
                start_sec=start,
                end_sec=end,
                reasons=missing,
            )
        )

    for explicit in result.recommended_reanalysis_targets[: max(0, max_targets - len(targets))]:
        targets.append(
            FocusTarget(kind="custom", question=explicit, prompt_name="focus_analysis", start_sec=start, end_sec=end)
        )
    return targets[:max_targets]


def build_factor_sweep_focus(result: VideoResult) -> Optional[FocusTarget]:
    """1차 분석 직후 한 번에 묶어서 재확인할 과실 요소 목록 (가이드 63절: 재분석 호출 최소화).

    화면에 찍히는데 아직 CONFIRMED가 아닌 항목만 모아 focus 하나로 만든다. 없으면 None.
    """
    env = result.road_environment
    profile = accident_profile(result)
    items: list[str] = []

    def unknown(observation) -> bool:
        return observation.status != "CONFIRMED"

    lane_change_involved = profile in {"lane_change", "roundabout"} or any(
        (vehicle.lane_change.value or "").lower() in {"true", "yes", "left", "right", "lane_change_left", "lane_change_right"}
        or (vehicle.movement or "").startswith("lane_change")
        for vehicle in result.vehicles
    )
    if lane_change_involved and unknown(env.lane_marking_at_lane_change):
        items.append("차로 변경·접촉 지점의 노면 차선이 실선인지 점선인지 (lane_marking_at_lane_change)")
    if profile == "signalized_intersection":
        if not any(item.status == "CONFIRMED" for item in env.signal_observations):
            items.append("블랙박스 차량 진행 방향 신호등 색과 변경 시점 (signal_observations)")
        if unknown(env.stop_line):
            items.append("정지선 위치와 각 차량의 정지선 통과 시점 (stop_line)")
    if profile in {"signalized_intersection", "unsignalized_intersection", "roundabout"}:
        participants = [result.get_vehicle(v) for v in result.collision_pair.participants]
        if any(vehicle is not None and unknown(vehicle.entered_intersection_first) for vehicle in participants):
            items.append("충돌 당사 차량 중 어느 차량이 교차로(회전교차로)에 먼저 진입했는지 (entered_intersection_first)")
    for vehicle in result.vehicles:
        if vehicle.id not in result.collision_pair.participants:
            continue
        if not vehicle.is_ego and unknown(vehicle.turn_signal):
            items.append(f"{vehicle.id}의 방향지시등 점등 여부 — 화면에 보이면 색·시점을, 화각 밖이면 '화각 밖'이라고 명시 (turn_signal)")
        if vehicle.is_ego and unknown(vehicle.braking):
            items.append("블랙박스 차량의 충돌 직전 제동·감속 여부 (braking)")
    if not result.collision.participant_parts:
        items.append("각 충돌 당사 차량의 충돌 부위 (participant_parts)")
    _coverage, missing, _profile = factor_coverage(result)
    for name in missing:
        if not any(name in item for item in items):
            items.append(f"{name} (사고 유형 체크리스트 미점검 항목)")
    # Video Agent 스스로 미확인으로 남긴 항목도 한 번 더 본다 — 단, 화각 밖·영상 시작 전처럼 재분석해도 못 보는 것과 정량 속도는 제외
    unobservable_markers = ("화각", "보이지 않", "보이지않", "찍히지", "프레임 밖", "화면 밖", "영상 이전", "영상 시작 전", "영상 밖", "시야 밖", "사각")
    flagged = list(result.unknown_or_unobservable) + list(result.uncertain_facts) + [
        f"{factor.factor}: {factor.note}" for factor in result.fault_relevant_factors if factor.observability == "UNKNOWN"
    ]
    for text in flagged:
        text = text.strip()
        if not text or any(marker in text for marker in unobservable_markers) or "속도" in text:
            continue
        if any(text[:20] in item for item in items):
            continue
        items.append(f"{text} (1차 분석 미확인 항목 재점검)")
    if not items:
        return None
    start, end = collision_window_seconds(result, padding=2.0)
    question = (
        "1차 분석에서 확인되지 않은 과실 관련 항목을 한 번에 재확인하라. 각 항목마다 CONFIRMED/INFERRED/UNKNOWN과 근거 시각을 적고, "
        "UNKNOWN이면 그 이유(화각 밖/화질/가려짐/영상 시작 전)를 unknown_or_unobservable에 명시하라.\n- "
        + "\n- ".join(items)
    )
    return FocusTarget(kind="factor_sweep", question=question, prompt_name="focus_analysis", start_sec=start, end_sec=end, reasons=[item.split(" (")[0] for item in items])


def should_use_cv_tracking(result: VideoResult, threshold: float = 0.80) -> bool:
    """가이드 58절: 2회 분석 후에도 identity/pair가 불명확할 때만."""
    if len(result.vehicles) < 3 and result.vehicle_identity_consistent:
        return False
    return result.collision_pair.confidence < threshold or not result.vehicle_identity_consistent


_OPPONENT_CANDIDATE = re.compile(
    r"([가-힣A-Za-z]{1,6}(?:색)?\s?(?:승용차|승합차|SUV|트럭|화물차|버스|택시|오토바이|이륜차|경차|차량))\s*\((vehicle_\d+)\)"
)


def _opponent_candidate_from_text(result: VideoResult) -> str:
    """요약·상세 서술에 '흰색 승용차(vehicle_2)'처럼 적힌, 목록에 없는 상대 차량 후보를 찾는다."""
    known = {vehicle.id for vehicle in result.vehicles}
    ego = result.ego_vehicle_id or "vehicle_1"
    for text in (result.short_summary or "", result.detailed_description or ""):
        for match in _OPPONENT_CANDIDATE.finditer(text):
            desc, vehicle_id = match.group(1).strip(), match.group(2)
            if vehicle_id != ego and vehicle_id not in known and "블랙박스" not in desc:
                return desc
    return ""


def user_confirmation_question(result: VideoResult) -> str:
    """가이드 59절: 영상으로 확정 불가 시 사용자에게 사고 당사 차량을 객관적으로 확인한다."""
    count = len(result.vehicles)
    participants = result.collision_pair.participants
    descriptions = {vehicle.id: vehicle.description or vehicle.id for vehicle in result.vehicles}

    def describe(vehicle_id: str) -> str:
        vehicle = result.get_vehicle(vehicle_id)
        if vehicle and vehicle.is_ego:
            return "블랙박스 차량"
        return descriptions.get(vehicle_id, vehicle_id)

    if count < 2:
        # 상대 차량 자체를 목록(inventory)에 넣지 못한 경우. 요약문에는 "흰색 승용차(vehicle_2)"처럼 상대 차량이
        # 적혀 있을 수 있으므로, 그 후보를 인용해 "찾지 못했다"는 모순된 말을 피한다.
        candidate = _opponent_candidate_from_text(result)
        if candidate:
            return (
                f"영상 설명에서는 상대 차량이 {candidate}로 보이지만, 차량 목록에서 확정하지는 못했어요. "
                f"상대 차량이 {candidate}가 맞나요? 어느 쪽(좌/우/앞/뒤)에서 왔는지도 알려주시면 그 정보로 영상을 다시 확인할게요."
            )
        return (
            "영상 분석에서 상대 차량을 확정하지 못했어요. "
            "상대 차량이 어느 쪽(좌/우/앞/뒤)에서 온 어떤 차량(색상·차종)이었는지 알려주시면 그 정보로 영상을 다시 확인할게요."
        )
    lines = [f"영상에서 차량이 {count}대 보여요."]
    if len(participants) == 2:
        lines.append(
            f"지금 분석으로는 {describe(participants[0])}과(와) {describe(participants[1])}이(가) 충돌한 것으로 보이지만, "
            f"충돌 순간의 확신도가 낮아요(신뢰도 {result.collision_pair.confidence:.2f})."
        )
        lines.append(
            f"실제 충돌 차량이 {describe(participants[0])}과(와) {describe(participants[1])}이(가) 맞나요?"
        )
    else:
        options = ", ".join(f"{vehicle.id}({describe(vehicle.id)})" for vehicle in result.vehicles)
        lines.append(f"어느 두 차량이 실제로 충돌했는지 영상만으로는 확정하기 어려워요. 차량 목록: {options}")
        lines.append("실제로 충돌한 두 차량이 어느 것인지 알려주실 수 있나요?")
    return " ".join(lines)
