"""백엔드 화면에 그대로 실리는 글 만들기 (해요체) + 계약 모델 매핑 보조.

- 판정 카드 summary / change_reason, 판례 팝업 body_text, 근거 도표 이름
- 사건 제목, video_meta, 질문 카드, 상대 보험사 주장 비율 파싱
- 사건경위서 4개 섹션 매핑, 반박의견서 메일 본문
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assessment.fault_ratio import parse_ratio
from case.questions import format_questions
from common.timeutil import parse_timestamp
from document.grounding import SECTION_TITLES
from document.incident_report import INCIDENT_SECTIONS
from state.case_state import CaseState, FaultAssessment, FaultRatio, MatchedCaseSummary, Question, RetrievedCase

KST = timezone(timedelta(hours=9))

# 코드 필수 질문(영상 소유 관계 등)은 확인 이유를 붙이지 않는다 (case/questions._with_why 와 동일)
_NO_WHY_FIELDS = {"video_source.vehicle_owner", "ego_vehicle.vehicle_id", "collision.participants_confirmed"}

_ROAD_LABELS = {
    "intersection": "교차로", "t_intersection": "삼거리", "roundabout": "회전교차로", "parking_lot": "주차장",
    "straight": "직선도로", "highway": "고속도로", "alley": "이면도로", "ramp": "진출입로", "other": "도로",
}
_MOVEMENT_LABELS = {
    "straight": "직진", "left_turn": "좌회전", "right_turn": "우회전", "u_turn": "유턴", "lane_change_left": "차로 변경",
    "lane_change_right": "차로 변경", "reversing": "후진", "stopped": "정차", "parking": "주차",
}
_COLLISION_LABELS = {
    "rear_end": "추돌", "side_swipe": "측면 접촉", "head_on": "정면 충돌", "front_to_left_side": "측면 충돌",
    "front_to_right_side": "측면 충돌", "front_to_side": "측면 충돌", "side_to_side": "측면 접촉",
}
_PREFIX_LABELS = {"ego_vehicle": "내 차", "other_vehicle": "상대 차", "road": "도로", "collision": "충돌", "video_source": "영상", "accident_datetime": "사고 일시", "review": "확인 사항"}
_LEAF_LABELS = {
    "signal": "신호", "signal_state": "신호 상태", "signal_present": "신호등 유무", "turn_signal": "방향지시등", "movement": "진행 방향",
    "entered_first": "선진입 여부", "lane_change": "차로 변경", "braking": "제동", "entry_direction": "진입 방향", "lane": "차로",
    "estimated_speed": "속도", "speed": "속도", "lane_marking": "차선 종류", "road_type": "장소 유형", "type": "형태",
    "ego_collision_part": "내 차 충돌 부위", "other_collision_part": "상대 차 충돌 부위", "vehicle_owner": "촬영 차량", "vehicle_id": "차량 식별",
}
_FIELD_LABELS = {
    "video_source.vehicle_owner": "영상 촬영 차량",
    "road.road_type": "사고 장소 유형", "road.signal_present": "신호등 유무", "road.signal_state": "신호 상태", "road.lane_marking": "차선 종류",
    "ego_vehicle.movement": "내 차 진행 방향", "ego_vehicle.signal": "내 차 신호", "ego_vehicle.turn_signal": "내 차 방향지시등",
    "ego_vehicle.entered_first": "내 차 선진입 여부", "ego_vehicle.lane_change": "내 차 차로 변경", "ego_vehicle.braking": "내 차 제동",
    "other_vehicle.movement": "상대 차 진행 방향", "other_vehicle.signal": "상대 차 신호", "other_vehicle.turn_signal": "상대 차 방향지시등",
    "other_vehicle.entered_first": "상대 차 선진입 여부", "other_vehicle.lane_change": "상대 차 차로 변경", "other_vehicle.entry_direction": "상대 차 진입 방향",
    "collision.type": "충돌 형태", "collision.ego_collision_part": "내 차 충돌 부위", "collision.other_collision_part": "상대 차 충돌 부위",
}
_VALUE_LABELS = {"true": "예", "false": "아니오", "red": "적색", "green": "녹색", "yellow": "황색", "user": "내 차", "opponent": "상대 차", "dashed": "점선", "solid": "실선"}


def _label(value: Optional[str], table: dict[str, str], default: str = "") -> str:
    if not value:
        return default
    key = str(value).strip().lower()
    return table.get(key, default or str(value))


# LLM이 추출한 값은 enum 밖의 표현("going_straight", "turning left", "직진 중")으로 올 때가 있다.
# 제목에 영문 키가 그대로 새지 않도록 부분 일치로 한국어 라벨을 고르고, 못 고르면 default 를 쓴다.
_ROAD_PATTERNS = [
    (("회전교차로", "roundabout", "rotary"), "회전교차로"), (("삼거리", "t_intersection", "t-intersection", "t자"), "삼거리"),
    (("교차로", "intersection", "crossroad", "junction"), "교차로"), (("주차", "parking"), "주차장"), (("고속", "highway", "expressway"), "고속도로"),
    (("이면", "골목", "alley", "narrow"), "이면도로"), (("진출입", "ramp"), "진출입로"), (("직선", "straight"), "직선도로"),
]
_MOVEMENT_PATTERNS = [
    (("차로 변경", "차선 변경", "lane"), "차로 변경"), (("유턴", "u_turn", "uturn", "u-turn"), "유턴"),
    (("직진", "straight", "forward"), "직진"), (("좌회전", "left"), "좌회전"), (("우회전", "right"), "우회전"),
    (("후진", "revers", "backing"), "후진"), (("정차", "정지", "stop"), "정차"), (("주차", "park"), "주차"),
]
_COLLISION_PATTERNS = [
    (("추돌", "rear"), "추돌"), (("정면", "head_on", "head-on", "frontal"), "정면 충돌"),
    (("측면 접촉", "swipe", "side_to_side"), "측면 접촉"), (("측면", "side"), "측면 충돌"),
]


def _label_fuzzy(value: Optional[str], table: dict[str, str], patterns: list[tuple[tuple[str, ...], str]], default: str = "") -> str:
    if not value:
        return default
    key = str(value).strip().lower()
    if key in table:
        return table[key]
    for needles, label in patterns:
        if any(needle in key for needle in needles):
            return label
    if re.search(r"[a-z_]", key):  # 알 수 없는 영문 키는 제목에 내보내지 않는다
        return default
    return str(value).strip()[:10] or default


# ----------------------------------------------------------------------------- analyze


def build_case_title(state: CaseState, *, today: Optional[datetime] = None) -> str:
    """사건 목록에 보이는 제목. 예: '교차로 직진 충돌 · 08-22'"""
    place = _label_fuzzy(state.road.road_type.value if state.road.road_type.is_known() else None, _ROAD_LABELS, _ROAD_PATTERNS)
    if state.video_analysis and not place:
        place = _label_fuzzy(state.video_analysis.road_environment.road_type.value, _ROAD_LABELS, _ROAD_PATTERNS)
    movement = _label_fuzzy(state.ego_vehicle.movement.value if state.ego_vehicle.movement.is_known() else None, _MOVEMENT_LABELS, _MOVEMENT_PATTERNS)
    collision = _label_fuzzy(state.collision.type.value if state.collision.type.is_known() else None, _COLLISION_LABELS, _COLLISION_PATTERNS, default="충돌")
    if collision == "충돌" and movement in {"후진", "주차"}:
        collision = "접촉"
    date_value = state.accident_datetime.date.value if state.accident_datetime.date.is_known() else None
    label = None
    if date_value:
        text = str(date_value)
        match = re.search(r"(?:\d{2,4}\s*[-./년]\s*)?(\d{1,2})\s*[-./월]\s*(\d{1,2})", text)
        if match:
            month, day = int(match.group(1)), int(match.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                label = f"{month:02d}-{day:02d}"
    if label is None:
        label = (today or datetime.now(KST)).strftime("%m-%d")
    words = [word for word in (place, movement, collision) if word]
    title = " ".join(dict.fromkeys(words))
    if not place and not movement:
        title = "교통사고"
    return f"{title} · {label}"[:60]


def build_video_meta(state: CaseState) -> dict[str, Optional[int]]:
    speed = None
    raw_speed = state.ego_vehicle.estimated_speed.value if state.ego_vehicle.estimated_speed.is_known() else None
    if raw_speed:
        match = re.search(r"(\d{1,3})\s*(?:km|킬로)", str(raw_speed), re.IGNORECASE)
        if match:
            speed = int(match.group(1))
    impact = None
    timestamp = state.collision.timestamp.value if state.collision.timestamp.is_known() else None
    seconds = parse_timestamp(timestamp)
    if seconds is not None:
        impact = int(seconds)
    return {"speed_kph": speed, "impact_at_sec": impact}


def question_card(question: Question, *, first: bool = False) -> str:
    """백엔드가 그대로 사용자에게 보내는 질문 카드 본문."""
    text = question.question.strip()
    if question.why and question.field not in _NO_WHY_FIELDS:
        text += f"\n(확인 이유: {question.why.strip()})"
    return ("하나만 물어볼게요. " + text) if first else text


def split_initial_response(message: str, questions: list[Question]) -> tuple[str, list[str]]:
    """create_case 응답을 (분석 완료 말풍선, 질문 카드) 로 나눈다. 질문 본문은 말풍선에서 뺀다."""
    if not questions:
        return message.strip(), []
    block = format_questions("", questions)
    summary = message.replace(block, "").strip() if block and block in message else message.strip()
    # "판정 전에 한 가지만 확인할게요" 같은 안내 줄 뒤에 질문 카드가 따로 붙으므로 그대로 둔다
    return summary, [question_card(questions[0], first=True)]


NO_VIDEO_REPLY = (
    "많이 놀라셨겠어요. 말씀해 주신 내용은 잘 적어 뒀어요. "
    "블랙박스 영상을 올려 주시면 영상을 분석해서 예상 과실비율을 근거와 함께 알려드릴게요. (mp4 권장 · 최대 200MB · 3분 이내)"
)


# ----------------------------------------------------------------------------- judge


def parse_opponent_claim(text: Optional[str], stored: Optional[dict[str, Any]] = None) -> Optional[dict[str, int]]:
    """'보험사가 나 30 상대 70 이래요' / '70:30' → {"mine": 30, "other": 70}. 합이 100이 아니면 무시."""
    if isinstance(stored, dict) and isinstance(stored.get("mine"), int) and isinstance(stored.get("other"), int):
        if stored["mine"] + stored["other"] == 100:
            candidate_stored = {"mine": stored["mine"], "other": stored["other"]}
        else:
            candidate_stored = None
    else:
        candidate_stored = None
    if not text:
        return candidate_stored
    match = re.search(r"(?:나|본인|제|내)\s*(?:차량?)?\s*(\d{1,3})\s*(?:[:：대]|,|\s)\s*(?:상대(?:방|측)?|저쪽)\s*(?:차량?)?\s*(\d{1,3})", text)
    if match:
        mine, other = int(match.group(1)), int(match.group(2))
        if mine + other == 100:
            return {"mine": mine, "other": other}
    for line in text.splitlines()[::-1]:
        ratio = parse_ratio(line)
        if ratio and sum(ratio) == 100:
            return {"mine": ratio[0], "other": ratio[1]}
    return candidate_stored


def _humanize_reason(reason: str) -> str:
    match = re.match(r"^(새로운 사용자 사실|검토 질문 답변|차량 식별 갱신):\s*([\w.]+)=(.*)$", reason)
    if not match:
        return reason
    field, value = match.group(2), match.group(3).strip()
    label = _FIELD_LABELS.get(field)
    if label is None:
        prefix, _, leaf = field.rpartition(".")
        leaf_label = _LEAF_LABELS.get(leaf, leaf.replace("_", " "))
        prefix_label = _PREFIX_LABELS.get(prefix, "")
        label = f"{prefix_label} {leaf_label}".strip()
    return f"{label}: {_VALUE_LABELS.get(value.lower(), value)}"


_CTA_SENTENCE = re.compile(r"[^.!?。]*(알려\s*주세요|말씀해\s*주세요|물어보세요|요청해\s*주세요|문의해\s*주세요)[^.!?。]*[.!?。]?\s*$")
_USER_RATIO_MENTION = re.compile(r"(?:나|사용자|본인)\s*(?:\([^)]*\))?\s*(?:차량)?\s*(\d{1,3})\s*[:：]\s*(?:상대(?:방|측)?)\s*(?:차량)?\s*(\d{1,3})")


def _clean_item(text: str) -> str:
    return text.strip().rstrip(".。 ").strip()


def ratio_text(mine: int, other: int) -> str:
    return f"나 {mine} : 상대 {other}"


def judge_summary(assessment: FaultAssessment, state: CaseState) -> str:
    mine, other = assessment.fault_ratio.user, assessment.fault_ratio.opponent
    explanation = assessment.explanation.strip()
    # "나(사용자 차량) 80 : 상대 20", "사용자 30 : 상대 70" 같은 표기를 화면 표기("나 30 : 상대 70", 최종 비율)로 맞춘다
    explanation = _USER_RATIO_MENTION.sub(ratio_text(mine, other), explanation)
    # 판정 카드에는 "…알려주세요" 같은 대화용 맺음말을 싣지 않는다
    explanation = _CTA_SENTENCE.sub("", explanation).strip()
    if ratio_text(mine, other) not in explanation:
        explanation = f"예상 과실비율은 {ratio_text(mine, other)}예요. " + explanation
    lines = [explanation]
    if assessment.assessment_type == "provisional":
        lines.append("아직 확인되지 않은 사실이 있어서 임시 예상치예요.")
    if state.rag_tier == "fault_standard":
        lines.append("꼭 맞는 심의사례가 없어서 과실비율 인정기준 도표를 근거로 계산했어요.")
    if assessment.uncertainties:
        lines.append("아직 확실하지 않은 점: " + "; ".join(_clean_item(item) for item in assessment.uncertainties[:2]) + ".")
    if assessment.ratio_dependencies:
        lines.append("추가로 확인되면 바뀔 수 있어요: " + "; ".join(_clean_item(item) for item in assessment.ratio_dependencies[:2]) + ".")
    text = "\n".join(line for line in lines if line)
    return text[:1200]


def change_reason_text(previous: Optional[Any], assessment: FaultAssessment, reasons: list[str]) -> Optional[str]:
    if previous is None:
        return None
    mine, other = assessment.fault_ratio.user, assessment.fault_ratio.opponent
    humanized = [_humanize_reason(item) for item in reasons if item][:3]
    if (previous.ratio_mine, previous.ratio_other) == (mine, other):
        text = f"다시 확인해도 예상 과실비율은 {ratio_text(mine, other)}으로 같아요."
        if humanized:
            text += " 반영한 내용: " + "; ".join(humanized) + "."
        return text
    text = f"{ratio_text(previous.ratio_mine, previous.ratio_other)}에서 {ratio_text(mine, other)}(으)로 바뀌었어요."
    if humanized:
        text += " 이유: " + "; ".join(humanized) + "."
    elif assessment.reasoning_summary:
        text += " " + assessment.reasoning_summary[0].strip().rstrip(".") + "."
    else:
        text += " 새로 확인된 사실을 반영했어요."
    return text


def _ratio_line(case: RetrievedCase) -> str:
    parts = []
    if case.basic_ratio:
        parts.append(f"기본 과실비율은 {case.basic_ratio}")
    if case.decision_ratio:
        parts.append(f"심의 결과 {case.decision_ratio}(으)로 결정됐어요" if parts else f"심의 결과는 {case.decision_ratio}(으)로 결정됐어요")
    if not parts:
        return ""
    text = ", ".join(parts)
    return text if text.endswith("어요") else text + "예요"


def precedent_title(case: RetrievedCase) -> str:
    title = (case.title or "").strip()
    if not title:
        title = "과실비율 인정기준 도표" if case.source_type != "deliberation_case" else "유사 심의사례"
        if case.chart_number:
            title += f" {case.chart_number}"
    return title[:60]


def precedent_body_text(case: RetrievedCase, assessment: Optional[FaultAssessment] = None) -> str:
    """H37 판례 팝업 본문 (해요체). 판정 시점에 반드시 채운다."""
    lines: list[str] = []
    description = (case.accident_description or case.excerpt or "").strip().replace("\n", " ")
    if case.source_type == "deliberation_case":
        head = f"심의사례 {case.case_id}"
        if case.accident_type:
            head += f" — {case.accident_type}"
        lines.append(head + "예요.")
        if description:
            lines.append("사고 개요: " + description[:300] + ("…" if len(description) > 300 else ""))
        ratio = _ratio_line(case)
        if ratio:
            lines.append(ratio + ".")
        if case.decision_reasons:
            lines.append("심의 이유: " + "; ".join(item.strip() for item in case.decision_reasons[:2]) + ".")
        if case.modification_factors:
            lines.append("적용된 수정요소(기본 비율에서 더하거나 뺀 조건): " + ", ".join(item.strip() for item in case.modification_factors[:4]) + ".")
    else:
        head = "과실비율 인정기준 도표"
        if case.chart_number:
            head += f" {case.chart_number}"
        if case.title:
            head += f" — {case.title}"
        lines.append(head + "예요.")
        if description:
            lines.append("사고 유형: " + description[:300] + ("…" if len(description) > 300 else ""))
        if case.basic_ratio:
            lines.append(f"기본 과실비율은 {case.basic_ratio}예요.")
        if case.modification_factors:
            lines.append("수정요소(기본 비율에서 더하거나 빼는 조건): " + ", ".join(item.strip() for item in case.modification_factors[:5]) + ".")
    relevance = case.relevance
    if relevance:
        if relevance.matched_factors:
            lines.append("내 사건과 비슷한 점: " + ", ".join(item.strip() for item in relevance.matched_factors[:4]) + ".")
        if relevance.different_factors:
            lines.append("다른 점: " + ", ".join(item.strip() for item in relevance.different_factors[:3]) + ".")
    if assessment is not None and assessment.anchor_case_id == case.case_id:
        lines.append("이 사례의 비율을 기준값으로 삼아 내 사건의 예상 과실비율을 계산했어요.")
    text = "\n".join(lines).strip()
    return text or f"심의사례 {case.case_id}에 대한 설명이에요."


def ordered_cases(state: CaseState, assessment: Optional[FaultAssessment], top_k: int = 3) -> list[RetrievedCase]:
    cases = list(state.retrieved_cases)
    anchor_id = assessment.anchor_case_id if assessment else None
    cases.sort(key=lambda item: (0 if item.case_id == anchor_id else 1, -(item.relevance.relevance if item.relevance else item.similarity)))
    return cases[:top_k]


def build_chart(state: CaseState, assessment: FaultAssessment, cases: list[RetrievedCase]) -> dict[str, str]:
    """판정 카드의 '근거 도표' 한 줄."""
    anchor = next((case for case in cases if case.case_id == assessment.anchor_case_id), cases[0] if cases else None)
    standard = next((case for case in cases if case.source_type != "deliberation_case"), None)
    if standard is not None and (anchor is None or anchor.source_type != "deliberation_case"):
        name = "인정기준 도표"
        if standard.chart_number:
            name += f" {standard.chart_number}"
        if standard.title:
            name += f" — {standard.title[:40]}"
        note = f"기본 과실비율 {standard.basic_ratio}" if standard.basic_ratio else "사고 유형별 기본 비율을 정해 둔 표"
        return {"name": name[:120], "note": note[:200]}
    if anchor is None:
        return {"name": "유사 심의사례 없음 · 일반 기준", "note": "꼭 맞는 사례가 없어서 일반 기준으로 계산했어요"}
    name = (anchor.accident_type or anchor.title or f"심의사례 {anchor.case_id}").strip()
    if anchor.chart_number:
        name = f"인정기준 도표 {anchor.chart_number} · {name}"
    note = f"가장 비슷한 심의사례 {anchor.case_id}의 {'결정' if anchor.decision_ratio else '기본'}비율 {anchor.decision_ratio or anchor.basic_ratio or '(미상)'}을 기준값으로 삼았어요"
    if assessment.anchor_enforced:
        note += " · 확인된 수정요소가 없어서 그대로 적용했어요"
    return {"name": name[:120], "note": note[:200]}


def assessment_from_snapshot(verdict: Any) -> FaultAssessment:
    """judge 상세가 없을 때(컨테이너 재시작 등) 백엔드의 판정 스냅샷만으로 최소 판정 객체를 만든다."""
    basis = verdict.basis or {}
    precedents = [item for item in basis.get("precedents") or [] if isinstance(item, dict) and item.get("id")]
    ids = [str(item["id"]) for item in precedents]
    return FaultAssessment(
        fault_ratio=FaultRatio(user=verdict.ratio_mine, opponent=verdict.ratio_other),
        assessment_type="estimated",
        most_likely=f"{verdict.ratio_mine}:{verdict.ratio_other}",
        confidence=0.5,
        anchor_case_id=ids[0] if ids else None,
        primary_case_ids=ids[:1],
        matched_cases=[MatchedCaseSummary(case_id=case_id, role="primary" if index == 0 else "supporting") for index, case_id in enumerate(ids)],
        reasoning_summary=[verdict.summary] if verdict.summary else [],
        explanation=verdict.summary or "",
        model="server_snapshot",
        prompt_version=f"server/verdict_v{verdict.version}",
    )


# ----------------------------------------------------------------------------- write


def report_section_key(index: Optional[int], title: Optional[str]) -> Optional[str]:
    if index and 1 <= index <= len(INCIDENT_SECTIONS):
        return INCIDENT_SECTIONS[index - 1]
    if title:
        for key in INCIDENT_SECTIONS:
            if SECTION_TITLES[key].split(". ", 1)[-1] in title:
                return key
    return None


def sections_to_dict(sections: Optional[list[Any]]) -> Optional[dict[str, str]]:
    if not sections:
        return None
    result: dict[str, str] = {}
    for item in sections:
        index = getattr(item, "index", None) if not isinstance(item, dict) else item.get("index")
        title = getattr(item, "title", None) if not isinstance(item, dict) else item.get("title")
        body = getattr(item, "body", None) if not isinstance(item, dict) else item.get("body")
        key = report_section_key(index, title) or (title or f"section_{index}")
        result[key] = str(body or "")
    return result


def report_sections_for_server(sections: dict[str, str]) -> list[dict[str, Any]]:
    """사건경위서 → 백엔드 Section[4] (index 1~4, 제목은 명세서 3.1)."""
    output = []
    for index, key in enumerate(INCIDENT_SECTIONS, start=1):
        body = (sections.get(key) or "").strip() or "해당 내용은 확인되지 않아 기재하지 않았습니다."
        output.append({"index": index, "title": SECTION_TITLES[key].split(". ", 1)[-1], "body": body})
    return output


def estimate_page_count(sections: list[dict[str, Any]]) -> int:
    total = sum(len(item.get("body") or "") for item in sections)
    return max(1, math.ceil(total / 1400))
