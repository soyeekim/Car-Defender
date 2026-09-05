"""정보 충분성 판단 (가이드 15.2 / 74 / 85절).

Deterministic 체크리스트가 기준이며, LLM은 누락 항목을 추가하거나 더 엄격하게 만들 수만 있다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt
from state.case_state import CaseState, MissingInformation
from state.updater import get_slot
from telemetry import RunLogger, get_run_logger

_IMPORTANCE_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class SufficiencyResult(BaseModel):
    ready_for_rag: bool = False
    ready_for_assessment: bool = False
    missing_information: list[MissingInformation] = Field(default_factory=list)
    user_answerable: list[str] = Field(default_factory=list)
    video_reanalysis_targets: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    checklist: dict[str, bool] = Field(default_factory=dict)

    def critical_missing(self) -> list[MissingInformation]:
        return [item for item in self.missing_information if item.importance == "critical"]

    def critical_missing_user_answerable(self) -> bool:
        return any(item.importance == "critical" and item.user_answerable for item in self.missing_information)

    def sorted_missing(self) -> list[MissingInformation]:
        return sorted(self.missing_information, key=lambda item: _IMPORTANCE_ORDER.get(item.importance, 9))


def _known(state: CaseState, path: str) -> bool:
    slot = get_slot(state, path)
    return bool(slot and slot.is_known())


def _declared_uncertain(state: CaseState, *keywords: str) -> bool:
    blob = " ".join(state.uncertain_facts).lower()
    return any(keyword.lower() in blob for keyword in keywords)


def deterministic_sufficiency(state: CaseState) -> SufficiencyResult:
    result = SufficiencyResult()
    missing: list[MissingInformation] = []
    checklist: dict[str, bool] = {}
    video = state.video_analysis
    # 이미 답을 받은(또는 모른다고 답한) 항목은 다시 묻지 않지만, 아직 답하지 않은 pending 질문은 다시 물을 수 있다.
    pending = {item.field for item in state.pending_questions}
    asked = set(state.asked_fields) - pending

    def add(field: str, importance: str, reason: str, *, user: bool = False, video_recheck: bool = False) -> None:
        if any(item.field == field for item in missing):
            return
        missing.append(
            MissingInformation(
                field=field,
                importance=importance,  # type: ignore[arg-type]
                reason=reason,
                user_answerable=user and field not in asked,
                video_recheckable=video_recheck,
            )
        )

    # 1. 영상 소유 관계 / 사용자 차량 식별
    owner_known = _known(state, "video_source.vehicle_owner")
    user_vehicle_known = bool(state.ego_vehicle.vehicle_id)
    owner_skipped = "video_source.vehicle_owner" in asked  # 답변 완료 또는 모른다고 답함
    checklist["video_owner_or_user_vehicle_identified"] = owner_known or (user_vehicle_known and owner_skipped)
    if not owner_known and not (user_vehicle_known and owner_skipped):
        add("video_source.vehicle_owner", "critical", "업로드 영상이 사용자 차량 블랙박스인지 상대 차량 블랙박스인지 확인 필요", user=True)
    if video and state.video_analyzed and not user_vehicle_known:
        if owner_known:
            add("ego_vehicle.vehicle_id", "critical", "영상 속 어느 차량이 사용자 차량인지 확인 필요", user=True)
        checklist["user_vehicle_identified"] = False
    else:
        checklist["user_vehicle_identified"] = user_vehicle_known

    # 2. 상대 차량 식별 (충돌 pair)
    opponent_known = bool(state.other_vehicle.vehicle_id)
    checklist["opponent_vehicle_identified"] = opponent_known
    if video and state.video_analyzed and not opponent_known:
        if len(video.collision_pair.participants) != 2:
            add("collision.participants_confirmed", "critical", "실제 충돌한 두 차량이 확정되지 않음", user=True, video_recheck=True)
        elif user_vehicle_known:
            add("other_vehicle.vehicle_id", "critical", "사용자 차량이 충돌 pair에 포함되지 않아 상대 차량을 확정할 수 없음", user=True)

    # 3. 사고 장소 유형
    checklist["road_type_known"] = _known(state, "road.road_type")
    if not checklist["road_type_known"]:
        add("road.road_type", "critical", "사고 장소 유형(교차로/직선도로/회전교차로 등) 미확인", user=True, video_recheck=True)

    # 4. 차량별 진행 방향
    for role, label in (("ego_vehicle", "사용자 차량"), ("other_vehicle", "상대 차량")):
        key = f"{role}.movement"
        checklist[f"{role}_movement_known"] = _known(state, key)
        if not checklist[f"{role}_movement_known"]:
            add(key, "critical", f"{label}의 진행 방향 미확인", user=False, video_recheck=True)

    # 5. 충돌 관계
    checklist["collision_type_known"] = _known(state, "collision.type")
    if not checklist["collision_type_known"]:
        add("collision.type", "high", "충돌 형태 미확인", user=False, video_recheck=True)
    checklist["collision_timestamp_known"] = _known(state, "collision.timestamp")
    if not checklist["collision_timestamp_known"]:
        add("collision.timestamp", "high", "충돌 시점 미확인", user=False, video_recheck=True)
    checklist["relative_direction_known"] = _known(state, "collision.relative_direction") or _known(state, "other_vehicle.entry_direction")
    if not checklist["relative_direction_known"]:
        add("other_vehicle.entry_direction", "high", "상대 차량의 접근/진입 방향 미확인", user=False, video_recheck=True)

    # 6. 신호/차선 조건 (또는 불확실성 명시)
    signal_present = (get_slot(state, "road.signal_present").value or "").lower() if get_slot(state, "road.signal_present") else ""
    if signal_present == "true":
        signal_known = _known(state, "road.signal_state") or _known(state, "ego_vehicle.signal") or _known(state, "other_vehicle.signal")
        declared = _declared_uncertain(state, "신호", "signal")
        checklist["signal_condition_known_or_declared"] = signal_known or declared
        if not signal_known and not declared:
            add("road.signal_state", "high", "신호교차로이나 신호 상태가 확인되지 않았고 불확실성도 명시되지 않음", user=False, video_recheck=True)
    else:
        checklist["signal_condition_known_or_declared"] = True

    # 7. 문서용 정보 (판정 필수는 아님)
    if not _known(state, "accident_datetime.date"):
        add("accident_datetime.date", "medium", "사고 발생 날짜 미확인 (사건경위서 작성에 필요)", user=True)
    if not _known(state, "road.location_name"):
        add("road.location_name", "low", "사고 장소명 미확인 (사건경위서 작성에 필요)", user=True)
    if not _known(state, "video_source.type") or (get_slot(state, "video_source.type").status == "INFERRED"):
        add("video_source.type", "low", "영상 출처(블랙박스/CCTV/제3자) 확인 필요", user=True)

    # 영상 자체가 없거나 분석 실패
    if not state.video_analyzed:
        result.reasons.append("video_not_analyzed")

    critical = [item for item in missing if item.importance == "critical"]
    high = [item for item in missing if item.importance == "high"]
    ready_for_rag = (
        state.video_analyzed
        and checklist.get("road_type_known", False)
        and checklist.get("user_vehicle_identified", False)
        and checklist.get("opponent_vehicle_identified", False)
        and (checklist.get("ego_vehicle_movement_known", False) or checklist.get("other_vehicle_movement_known", False))
    )
    ready_for_assessment = ready_for_rag and not critical and len(high) <= 1
    result.ready_for_rag = ready_for_rag
    result.ready_for_assessment = ready_for_assessment
    result.missing_information = sorted(missing, key=lambda item: _IMPORTANCE_ORDER.get(item.importance, 9))
    result.user_answerable = [item.field for item in result.missing_information if item.user_answerable]
    result.video_reanalysis_targets = [
        _recheck_focus(item.field, state) for item in result.missing_information if item.video_recheckable and item.importance in {"critical", "high"}
    ]
    result.video_reanalysis_targets = list(dict.fromkeys(target for target in result.video_reanalysis_targets if target))
    result.checklist = checklist
    if critical:
        result.reasons.append("critical_missing:" + ",".join(item.field for item in critical))
    return result


def _user_hints(state: CaseState) -> str:
    """사용자가 진술한 상대 차량 정보(색상·위치 등)를 재분석 focus에 붙여준다."""
    hints = [
        fact.fact
        for fact in state.user_confirmed_facts
        if any(keyword in fact.fact for keyword in ("차량", "차", "택시", "트럭", "버스", "승용", "SUV", "좌측", "우측", "왼쪽", "오른쪽", "앞", "뒤"))
    ]
    return (" 사용자 진술 참고: " + " / ".join(hints[-3:])) if hints else ""


def _recheck_focus(field: str, state: CaseState) -> str:
    ego = state.ego_vehicle.vehicle_id or "블랙박스 차량"
    other = state.other_vehicle.vehicle_id or "상대 차량"
    mapping = {
        "road.road_type": "사고 장소의 도로 유형(사거리/삼거리/회전교차로/직선도로/주차장)과 교차로 구조를 재확인하라.",
        "ego_vehicle.movement": f"{ego}의 충돌 직전 진행 방향(직진/좌회전/우회전/차로변경/후진)을 재확인하라.",
        "other_vehicle.movement": f"{other}의 충돌 직전 진행 방향과 등장 방향을 재확인하라.",
        "collision.type": "충돌 형태와 각 차량의 충돌 부위를 충돌 구간 전후에서 재확인하라.",
        "collision.timestamp": "실제 접촉이 발생한 시각(collision_window)을 재확인하라.",
        "other_vehicle.entry_direction": f"{other}가 어느 방향에서 접근·진입했는지 재확인하라.",
        "road.signal_state": "충돌 직전 각 차량 진행 방향의 신호등 색상과 정지선 통과 시점을 집중 분석하라.",
        "collision.participants_confirmed": (
            "충돌 시점 전후에서 실제 접촉한 두 차량을 재검증하라. 상대 차량이 vehicle inventory에 없으면 새 vehicle ID를 부여해 추가하라."
            + _user_hints(state)
        ),
    }
    return mapping.get(field, "")


class _LLMSufficiency(BaseModel):
    ready_for_rag: bool = True
    ready_for_assessment: bool = False
    missing_information: list[MissingInformation] = Field(default_factory=list)
    user_answerable: list[str] = Field(default_factory=list)
    video_reanalysis_targets: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def check_information_sufficiency(
    state: CaseState,
    client: Optional[TextClient] = None,
    *,
    use_llm: bool = True,
    run_logger: Optional[RunLogger] = None,
) -> SufficiencyResult:
    result = deterministic_sufficiency(state)
    if client is None or not use_llm or not state.video_analyzed:
        return result

    logger = run_logger or get_run_logger()
    system = load_prompt("master_agent", "system")
    task = load_prompt("master_agent", "sufficiency")
    user = task.render(
        case_state=compact_json(state.compact(), max_chars=10000),
        deterministic_check=compact_json(result.model_dump(), max_chars=4000),
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=_LLMSufficiency, task=task.task)
        refined = _LLMSufficiency.model_validate(response.data)
        logger.log(
            agent="master_agent",
            task=task.task,
            case_id=state.case_id,
            model=response.metrics.model,
            prompt_version=f"{system.version_id}+{task.version_id}",
            metrics=response.metrics,
        )
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300]})
        return result

    known_fields = {item.field for item in result.missing_information}
    for item in refined.missing_information:
        if item.field in known_fields or item.field in state.asked_fields:
            continue
        slot = get_slot(state, item.field)
        if slot is not None and slot.is_known():
            continue
        if item.field.endswith("estimated_speed"):
            continue
        result.missing_information.append(item)
        known_fields.add(item.field)
    result.missing_information = sorted(result.missing_information, key=lambda item: _IMPORTANCE_ORDER.get(item.importance, 9))
    result.user_answerable = [item.field for item in result.missing_information if item.user_answerable and item.field not in state.asked_fields]
    for target in refined.video_reanalysis_targets:
        if target and target not in result.video_reanalysis_targets:
            result.video_reanalysis_targets.append(target)
    result.ready_for_rag = result.ready_for_rag and refined.ready_for_rag
    result.ready_for_assessment = result.ready_for_assessment and refined.ready_for_assessment
    result.reasons.extend(reason for reason in refined.reasons if reason not in result.reasons)
    return result
