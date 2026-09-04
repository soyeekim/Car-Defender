"""Case State 갱신 로직 (가이드 6·7·75·76·112절).

- merge_video_facts     : Video Result → Case State (source=video)
- apply_user_extraction : 사용자 사실 추출 결과 → Case State (source=user), 충돌 기록
- remap_vehicles_by_owner: 영상 소유 관계에 따라 사용자/상대 차량 ID 매핑
- invalidate_assessment : 중요한 새 사실 유입 시 기존 판정 무효화
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from state.case_state import (
    CaseState,
    Conflict,
    Fact,
    FactSource,
    FactStatus,
    Slot,
    TimelineItem,
    Verification,
)
from video.schemas import Observation, VideoResult

IMPORTANT_FIELD_PREFIXES = (
    "video_source.vehicle_owner",
    "road.",
    "ego_vehicle.",
    "other_vehicle.",
    "collision.",
)

_OWNER_ALIASES = {
    "user": "user",
    "본인": "user",
    "내차": "user",
    "내 차": "user",
    "자차": "user",
    "mine": "user",
    "me": "user",
    "opponent": "opponent",
    "상대": "opponent",
    "상대방": "opponent",
    "상대차": "opponent",
    "상대 차량": "opponent",
    "other": "opponent",
}
_TYPE_ALIASES = {
    "dashcam": "dashcam",
    "블랙박스": "dashcam",
    "cctv": "cctv",
    "도로 cctv": "cctv",
    "third_party": "third_party",
    "제3자": "third_party",
    "third party": "third_party",
}
_UNKNOWN_ANSWERS = {"unknown", "모름", "모르겠", "기억 안", "기억안", "확인 불가", "n/a", "none", "null", ""}


class ExtractedFact(BaseModel):
    field: Optional[str] = None
    value: Optional[str] = None
    fact: str
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    verification: Verification = "unverified"
    answers_field: Optional[str] = None


class ExtractedConflict(BaseModel):
    field: Optional[str] = None
    existing_value: Optional[str] = None
    new_value: Optional[str] = None
    description: str = ""


class UserFactExtraction(BaseModel):
    new_facts: list[ExtractedFact] = Field(default_factory=list)
    conflicts: list[ExtractedConflict] = Field(default_factory=list)
    opponent_claim: Optional[str] = None
    ignored_opinions: list[str] = Field(default_factory=list)
    no_new_facts: bool = False


# --------------------------------------------------------------------------- slot access


def get_slot(state: CaseState, path: str) -> Optional[Slot]:
    target: Any = state
    for part in path.split("."):
        if not hasattr(target, part):
            return None
        target = getattr(target, part)
    return target if isinstance(target, Slot) else None


def normalize_value(field: Optional[str], value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if field and field.startswith("review."):
        if lowered in {"true", "yes", "y", "예", "네", "응", "맞아", "맞아요", "켰", "있음"}:
            return "true"
        if lowered in {"false", "no", "n", "아니오", "아니요", "아니", "없음", "안 켰", "안켰"}:
            return "false"
        return text
    if field == "video_source.vehicle_owner":
        for alias, normalized in _OWNER_ALIASES.items():
            if alias in lowered:
                return normalized
        return lowered
    if field == "video_source.type":
        for alias, normalized in _TYPE_ALIASES.items():
            if alias in lowered:
                return normalized
        return lowered
    boolean_field = field in {"road.signal_present", "collision.participants_confirmed"} or (
        field is not None and field.endswith(".lane_change")
    )
    if boolean_field:
        if lowered in {"true", "yes", "있음", "y", "예", "네"}:
            return "true"
        if lowered in {"false", "no", "없음", "n", "아니오", "아니요"}:
            return "false"
    return text


def is_unknown_answer(value: Optional[str]) -> bool:
    if value is None:
        return True
    lowered = str(value).strip().lower()
    return any(marker and marker in lowered for marker in _UNKNOWN_ANSWERS) or lowered in _UNKNOWN_ANSWERS


def set_slot(
    state: CaseState,
    path: str,
    value: Optional[str],
    *,
    source: FactSource,
    status: FactStatus = "CONFIRMED",
    confidence: Optional[float] = None,
    note: Optional[str] = None,
    overwrite: bool = False,
) -> tuple[bool, Optional[Conflict]]:
    """Slot을 갱신한다. 출처가 다른 확정값과 충돌하면 덮어쓰지 않고 Conflict를 반환한다."""
    slot = get_slot(state, path)
    if slot is None:
        return False, None
    normalized = normalize_value(path, value)
    if normalized is None:
        return False, None

    if slot.is_known() and str(slot.value).lower() != normalized.lower():
        same_source = slot.source == source
        existing_confirmed = slot.status == "CONFIRMED"
        if overwrite or same_source or (not existing_confirmed and status == "CONFIRMED"):
            previous_note = f"이전 값 {slot.value} [{slot.source}/{slot.status}] 갱신"
            slot.value = normalized
            slot.source = source
            slot.status = status
            slot.confidence = confidence
            slot.note = note or previous_note
            state.touch()
            return True, None
        conflict = Conflict(
            field=path,
            description=f"{path}: 기존 {slot.value} [{slot.source}] vs 신규 {normalized} [{source}]",
            existing_value=str(slot.value),
            existing_source=slot.source,
            new_value=normalized,
            new_source=source,
        )
        if not any(item.field == path and item.new_value == normalized and not item.resolved for item in state.conflicts):
            state.conflicts.append(conflict)
        state.touch()
        return False, conflict

    if slot.is_known() and str(slot.value).lower() == normalized.lower() and slot.source != source:
        # 같은 값을 다른 출처가 다시 확인한 경우: 영상 확정 출처는 유지하고 교차 확인만 기록한다.
        if slot.status == "CONFIRMED" and slot.source == "video":
            slot.note = f"{source} 진술과 일치"
        else:
            slot.status = status if status == "CONFIRMED" else slot.status
            slot.source = source
            slot.note = "기존 관찰과 일치"
        state.touch()
        return True, None

    slot.value = normalized
    slot.source = source
    slot.status = status
    slot.confidence = confidence
    if note:
        slot.note = note
    state.touch()
    return True, None


def _slot_from_observation(state: CaseState, path: str, observation: Observation) -> None:
    if observation.value is None or observation.status == "UNKNOWN":
        return
    set_slot(
        state,
        path,
        observation.value,
        source="video",
        status=observation.status,
        confidence=observation.confidence,
        note=observation.evidence,
    )


def _slot_from_plain(state: CaseState, path: str, value: Optional[str], *, status: FactStatus = "CONFIRMED", confidence: Optional[float] = None) -> None:
    if value is None or str(value).strip().lower() in {"", "unknown", "none", "null"}:
        return
    set_slot(state, path, value, source="video", status=status, confidence=confidence)


# --------------------------------------------------------------------------- video → state


def _resolve_vehicle_roles(state: CaseState, result: VideoResult) -> tuple[Optional[str], Optional[str]]:
    dashcam = result.dashcam_vehicle_id()
    participants = list(result.collision_pair.participants)
    owner = state.video_source.vehicle_owner.value
    user_slot_id = state.ego_vehicle.vehicle_id if state.ego_vehicle.vehicle_id in result.vehicle_ids() else None
    user_assigned = any(note.startswith("사용자 지정 차량 식별") for note in state.notes)

    if user_slot_id and user_assigned:
        # 사용자가 직접 vehicle ID를 지정한 경우 유지
        other = result.other_participant(user_slot_id) if user_slot_id in participants else None
        return user_slot_id, other

    if owner == "opponent" and dashcam:
        opponent_id = dashcam
        user_id = result.other_participant(dashcam) if dashcam in participants else None
        return user_id, opponent_id

    if dashcam and (dashcam in participants or not participants):
        return dashcam, result.other_participant(dashcam)

    if len(participants) == 2:
        # 블랙박스 차량이 충돌 당사자가 아닌 경우(CCTV/제3자): 사용자 차량은 아직 미확정
        return None, None
    return dashcam, None


def _signal_for_vehicle(result: VideoResult, vehicle_id: Optional[str]) -> Optional[Observation]:
    if not vehicle_id:
        return None
    best: Optional[Observation] = None
    for item in result.road_environment.signal_observations:
        if vehicle_id not in (item.applies_to or ""):
            continue
        if item.color is None or item.status == "UNKNOWN":
            continue
        candidate = Observation(
            value=f"{item.color}" + (f" @{item.time}" if item.time else ""),
            status=item.status,
            confidence=item.confidence,
            evidence=item.note,
            timestamp=item.time,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def _apply_vehicle(state: CaseState, role: Literal["ego_vehicle", "other_vehicle"], result: VideoResult, vehicle_id: Optional[str]) -> None:
    info = getattr(state, role)
    if not vehicle_id:
        return
    vehicle = result.get_vehicle(vehicle_id)
    if vehicle is None:
        return
    info.vehicle_id = vehicle_id
    info.description = vehicle.description or vehicle_id
    _slot_from_plain(state, f"{role}.movement", vehicle.movement)
    _slot_from_plain(state, f"{role}.entry_direction", vehicle.entry_direction)
    _slot_from_plain(state, f"{role}.lane", vehicle.lane)
    _slot_from_observation(state, f"{role}.estimated_speed", vehicle.speed_qualitative)
    _slot_from_observation(state, f"{role}.turn_signal", vehicle.turn_signal)
    _slot_from_observation(state, f"{role}.lane_change", vehicle.lane_change)
    _slot_from_observation(state, f"{role}.braking", vehicle.braking)
    _slot_from_observation(state, f"{role}.entered_first", vehicle.entered_intersection_first)
    signal = _signal_for_vehicle(result, vehicle_id)
    if signal:
        _slot_from_observation(state, f"{role}.signal", signal)


def merge_video_facts(state: CaseState, result: VideoResult, *, threshold: float = 0.80) -> CaseState:
    state.video_analysis = result
    state.video_analyzed = True
    state.video_status = result.analysis_completion.status if result.analysis_completion.status else "VIDEO_ANALYSIS_COMPLETE"

    env = result.road_environment
    _slot_from_observation(state, "road.road_type", env.road_type)
    _slot_from_observation(state, "road.intersection_type", env.intersection_type)
    _slot_from_observation(state, "road.lane_count", env.lane_count)
    _slot_from_observation(state, "road.signal_present", env.signal_present)
    _slot_from_observation(state, "road.lane_marking", env.lane_marking_at_lane_change)
    _slot_from_observation(state, "road.road_surface", env.road_surface)
    _slot_from_observation(state, "road.weather", env.weather)
    if env.signal_observations:
        confirmed = [item for item in env.signal_observations if item.color and item.status != "UNKNOWN"]
        if confirmed:
            best = max(confirmed, key=lambda item: item.confidence)
            set_slot(
                state,
                "road.signal_state",
                f"{best.applies_to}: {best.color}" + (f" @{best.time}" if best.time else ""),
                source="video",
                status=best.status,
                confidence=best.confidence,
            )

    user_id, opponent_id = _resolve_vehicle_roles(state, result)
    _apply_vehicle(state, "ego_vehicle", result, user_id)
    _apply_vehicle(state, "other_vehicle", result, opponent_id)
    if state.video_source.vehicle_owner.value in {"user", "opponent"} and not state.video_source.type.is_known():
        pass  # 영상 유형은 사용자 확인 사항
    if result.dashcam_vehicle_id() and not state.video_source.type.is_known():
        set_slot(state, "video_source.type", "dashcam", source="video", status="INFERRED", confidence=0.7,
                 note="영상에 블랙박스 촬영 차량이 존재함")

    collision = result.collision
    _slot_from_plain(state, "collision.type", collision.collision_type)
    _slot_from_plain(state, "collision.relative_direction", collision.relative_direction)
    timestamp = collision.timestamp or result.collision_pair.timestamp or result.collision_window.most_likely_timestamp
    _slot_from_plain(state, "collision.timestamp", timestamp, confidence=result.collision_window.confidence or None)
    parts = {item.vehicle_id: item.part for item in collision.participant_parts}
    if user_id and parts.get(user_id):
        _slot_from_plain(state, "collision.ego_collision_part", parts[user_id])
    if opponent_id and parts.get(opponent_id):
        _slot_from_plain(state, "collision.other_collision_part", parts[opponent_id])
    if len(result.collision_pair.participants) == 2:
        set_slot(
            state,
            "collision.participants_confirmed",
            "true" if result.collision_pair.confidence >= threshold else "false",
            source="video",
            status="CONFIRMED" if result.collision_pair.confidence >= threshold else "INFERRED",
            confidence=result.collision_pair.confidence,
        )

    # timeline
    state.timeline = [item for item in state.timeline if item.source != "video"]
    for event in result.timeline:
        state.timeline.append(
            TimelineItem(
                time=event.start_time,
                end_time=event.end_time,
                event=event.event,
                source="video",
                status=event.status,
                confidence=event.confidence,
            )
        )

    # facts with source separation
    state.facts = [item for item in state.facts if item.source != "video"]
    for text in result.confirmed_facts:
        state.facts.append(Fact(fact=text, source="video", status="CONFIRMED", verification="visible_in_video"))
    for text in result.inferred_facts:
        state.facts.append(Fact(fact=text, source="video", status="INFERRED", verification="visible_in_video"))
    for factor in result.fault_relevant_factors:
        if factor.observability == "UNKNOWN":
            continue
        text = f"{factor.factor}: {factor.note}".strip(": ")
        if text and not any(item.fact == text for item in state.facts):
            state.facts.append(Fact(fact=text, source="video", status=factor.observability, verification="visible_in_video"))

    uncertain = list(result.unknown_or_unobservable) + list(result.uncertain_facts)
    for factor in result.fault_relevant_factors:
        if factor.observability == "UNKNOWN":
            uncertain.append(f"{factor.factor}: 영상만으로 확인 불가" + (f" ({factor.note})" if factor.note else ""))
    state.uncertain_facts = list(dict.fromkeys([*state.uncertain_facts, *uncertain]))

    # 사용자 진술 재검증: 영상 확정 사실과 충돌하는 사용자 값이 있으면 conflict가 이미 기록됨
    if state.fault_assessment and not state.assessment_invalidated:
        invalidate_assessment(state, "영상 분석 결과가 갱신됨")
    if state.current_stage in {"INITIAL", "VIDEO_ANALYZING"}:
        state.set_stage("FACT_COLLECTING")
    state.touch()
    return state


def remap_vehicles_by_owner(state: CaseState) -> bool:
    """영상 소유 관계가 확인되면 사용자/상대 차량 ID를 다시 매핑한다."""
    result = state.video_analysis
    if result is None:
        return False
    user_id, opponent_id = _resolve_vehicle_roles(state, result)
    changed = user_id != state.ego_vehicle.vehicle_id or opponent_id != state.other_vehicle.vehicle_id
    if not changed:
        return False
    # 사용자 진술 슬롯은 유지하고 영상 유래 슬롯만 다시 채운다.
    for role in ("ego_vehicle", "other_vehicle"):
        info = getattr(state, role)
        info.vehicle_id = None
        info.description = None
        for name in type(info).model_fields:
            slot = getattr(info, name)
            if isinstance(slot, Slot) and slot.source == "video":
                setattr(info, name, Slot())
    for name in ("ego_collision_part", "other_collision_part"):
        slot = getattr(state.collision, name)
        if slot.source == "video":
            setattr(state.collision, name, Slot())
    _apply_vehicle(state, "ego_vehicle", result, user_id)
    _apply_vehicle(state, "other_vehicle", result, opponent_id)
    parts = {item.vehicle_id: item.part for item in result.collision.participant_parts}
    if user_id and parts.get(user_id):
        _slot_from_plain(state, "collision.ego_collision_part", parts[user_id])
    if opponent_id and parts.get(opponent_id):
        _slot_from_plain(state, "collision.other_collision_part", parts[opponent_id])
    state.notes.append(f"차량 역할 재매핑: 사용자={user_id}, 상대={opponent_id}")
    if state.fault_assessment:
        invalidate_assessment(state, "차량 역할(사용자/상대)이 변경됨")
    state.touch()
    return True


# --------------------------------------------------------------------------- user → state


def _is_important(field: Optional[str]) -> bool:
    return bool(field) and any(field.startswith(prefix) for prefix in IMPORTANT_FIELD_PREFIXES)


def invalidate_assessment(state: CaseState, reason: str) -> None:
    if state.fault_assessment is None:
        return
    state.assessment_invalidated = True
    if reason not in state.assessment_invalidation_reasons:
        state.assessment_invalidation_reasons.append(reason)
    state.retrieved_cases = []
    state.rag_query = None
    if state.current_stage in {"ASSESSMENT_COMPLETE", "REPORT_COMPLETE", "REBUTTAL_COMPLETE"}:
        state.set_stage("FACT_COLLECTING")
    state.touch()


def _assign_vehicle_id(state: CaseState, field: str, value: Optional[str]) -> bool:
    """사용자가 영상 속 vehicle ID로 본인/상대 차량을 지정한 경우."""
    result = state.video_analysis
    if result is None or not value:
        return False
    normalized = str(value).strip().lower().replace(" ", "")
    vehicle = result.get_vehicle(normalized)
    if vehicle is None:
        # "노란색 차량"처럼 설명으로 지정한 경우 설명 매칭
        for candidate in result.vehicles:
            if normalized and normalized in (candidate.description or "").lower().replace(" ", ""):
                vehicle = candidate
                break
    if vehicle is None:
        return False
    other = result.other_participant(vehicle.id)
    if field == "ego_vehicle.vehicle_id":
        user_id, opponent_id = vehicle.id, other
    else:
        user_id, opponent_id = other, vehicle.id
    dashcam = result.dashcam_vehicle_id()
    if dashcam and user_id == dashcam:
        set_slot(state, "video_source.vehicle_owner", "user", source="user", status="CONFIRMED", confidence=0.9, overwrite=True)
    elif dashcam and opponent_id == dashcam:
        set_slot(state, "video_source.vehicle_owner", "opponent", source="user", status="CONFIRMED", confidence=0.9, overwrite=True)
    for role, vehicle_id in (("ego_vehicle", user_id), ("other_vehicle", opponent_id)):
        info = getattr(state, role)
        info.vehicle_id = vehicle_id
        entry = result.get_vehicle(vehicle_id)
        info.description = (entry.description or vehicle_id) if entry else None
        for name in type(info).model_fields:
            slot = getattr(info, name)
            if isinstance(slot, Slot) and slot.source == "video":
                setattr(info, name, Slot())
        _apply_vehicle(state, role, result, vehicle_id)
    parts = {item.vehicle_id: item.part for item in result.collision.participant_parts}
    if user_id and parts.get(user_id):
        set_slot(state, "collision.ego_collision_part", parts[user_id], source="video", overwrite=True)
    if opponent_id and parts.get(opponent_id):
        set_slot(state, "collision.other_collision_part", parts[opponent_id], source="video", overwrite=True)
    state.notes.append(f"사용자 지정 차량 식별: 사용자={user_id}, 상대={opponent_id}")
    return True


def _clear_pending(state: CaseState, field: Optional[str]) -> None:
    if not field:
        return
    state.pending_questions = [item for item in state.pending_questions if item.field != field]
    state.missing_information = [item for item in state.missing_information if item.field != field]


def apply_user_extraction(
    state: CaseState,
    extraction: UserFactExtraction,
    *,
    turn: Optional[int] = None,
) -> tuple[list[Fact], list[Conflict]]:
    added: list[Fact] = []
    conflicts: list[Conflict] = []
    owner_changed = False

    for item in extraction.new_facts:
        field = item.field
        answered = item.answers_field or field
        # 질문 field가 실제 슬롯 경로인데 LLM이 field를 비워 보낸 경우 보정
        if field is None and answered and get_slot(state, answered) is not None:
            field = answered
        if is_unknown_answer(item.value) and (field or answered):
            note = f"사용자가 {answered}에 대해 모른다고 답함"
            if note not in state.uncertain_facts:
                state.uncertain_facts.append(note)
            if answered and answered not in state.asked_fields:
                state.asked_fields.append(answered)
            _clear_pending(state, answered)
            continue

        fact = Fact(
            fact=item.fact,
            source="user",
            status="CONFIRMED",
            confidence=item.confidence,
            field=field,
            value=normalize_value(field, item.value) if field else item.value,
            verification=item.verification,
            turn=turn,
        )
        review_field = answered if (answered or "").startswith("review.") else (field if (field or "").startswith("review.") else None)
        if review_field:
            # 심의사례 검토 단계의 custom 질문 답변: 슬롯이 아니라 review_answers + Fact로 보존
            value = normalize_value(review_field, item.value) or item.value
            fact.field = review_field
            fact.value = value
            state.review_answers[review_field] = str(value)
            _clear_pending(state, review_field)
            if review_field not in state.asked_fields:
                state.asked_fields.append(review_field)
            if state.fault_assessment:
                invalidate_assessment(state, f"검토 질문 답변: {review_field}={value}")
        elif field in {"ego_vehicle.vehicle_id", "other_vehicle.vehicle_id"}:
            assigned = _assign_vehicle_id(state, field, fact.value)
            if assigned:
                owner_changed = True
            _clear_pending(state, answered)
            if answered and answered not in state.asked_fields:
                state.asked_fields.append(answered)
            if state.fault_assessment:
                invalidate_assessment(state, f"차량 식별 갱신: {field}={fact.value}")
        elif field:
            updated, conflict = set_slot(
                state,
                field,
                item.value,
                source="user",
                status="CONFIRMED",
                confidence=item.confidence,
            )
            if conflict:
                fact.verification = "contradicts_video"
                conflicts.append(conflict)
            elif updated and field == "video_source.vehicle_owner":
                owner_changed = True
            _clear_pending(state, answered)
            if answered and answered not in state.asked_fields:
                state.asked_fields.append(answered)
            if _is_important(field) and state.fault_assessment:
                invalidate_assessment(state, f"새로운 사용자 사실: {field}={fact.value}")
        if not any(existing.fact == fact.fact and existing.source == "user" for existing in state.facts):
            state.facts.append(fact)
            state.user_confirmed_facts.append(fact)
            added.append(fact)

    for item in extraction.conflicts:
        if not item.description and not item.field:
            continue
        duplicate = any(
            existing.field == item.field and existing.new_value == item.new_value and not existing.resolved
            for existing in state.conflicts
        )
        if duplicate:
            continue
        conflict = Conflict(
            field=item.field,
            description=item.description or f"{item.field}: {item.existing_value} vs {item.new_value}",
            existing_value=item.existing_value,
            new_value=item.new_value,
            new_source="user",
            existing_source=(get_slot(state, item.field).source if item.field and get_slot(state, item.field) else "unknown"),
        )
        state.conflicts.append(conflict)
        conflicts.append(conflict)

    if extraction.opponent_claim:
        claim = extraction.opponent_claim.strip()
        if claim and claim != state.opponent_claim:
            state.opponent_claim = claim if not state.opponent_claim else f"{state.opponent_claim}\n{claim}"
            state.facts.append(Fact(fact=f"상대방 주장: {claim}", source="user", status="INFERRED", field="opponent_claim", value=claim, turn=turn))

    if owner_changed and not any(item.field in {"ego_vehicle.vehicle_id", "other_vehicle.vehicle_id"} for item in extraction.new_facts):
        remap_vehicles_by_owner(state)
    state.touch()
    return added, conflicts


def state_field_index(state: CaseState) -> dict[str, Slot]:
    """path → Slot 사전 (sufficiency 검사·질문 생성에 사용)."""
    index: dict[str, Slot] = {}
    for group_name in ("video_source", "accident_datetime", "road", "ego_vehicle", "other_vehicle", "collision"):
        group = getattr(state, group_name)
        for name in type(group).model_fields:
            value = getattr(group, name)
            if isinstance(value, Slot):
                index[f"{group_name}.{name}"] = value
    return index
