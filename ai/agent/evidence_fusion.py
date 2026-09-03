import os
from typing import Optional

from schemas.accident_state import AccidentState
from schemas.evidence_state import (
    EvidenceFusionResult,
    FinalFact,
    UserEvidence,
)
from schemas.fact_plan import FactorAnswer
from schemas.slot_schema import ALL_SLOTS
from schemas.video_analysis import (
    VIDEO_STATE_SLOTS,
    VideoAnalysisResult,
    VisionSlotValue,
)


_NORMALIZED_TERMS = {
    "있습니다": "있음",
    "없습니다": "없음",
    "예": "있음",
    "아니오": "없음",
    "무신호": "없음",
    "오른쪽": "우측",
    "왼쪽": "좌측",
}


def normalize_evidence_value(value: str) -> str:
    normalized = value.lower().replace(" ", "")
    for source, target in _NORMALIZED_TERMS.items():
        normalized = normalized.replace(source, target)
    return normalized


def _vision_threshold() -> float:
    raw = float(os.getenv("VISION_VERIFIED_THRESHOLD", "0.8"))
    return max(0.0, min(1.0, raw))


def _strong_vision_evidence(
    vision: VisionSlotValue, threshold: Optional[float] = None
) -> bool:
    threshold = _vision_threshold() if threshold is None else threshold
    return (
        vision.value is not None
        and vision.observation_type in {"direct_visual", "sensor_readout"}
        and vision.confidence >= threshold
        and bool(vision.evidence)
        and bool(vision.timestamp)
    )


def _user_evidence(state: AccidentState, slot_name: str) -> Optional[UserEvidence]:
    slot = getattr(state, slot_name)
    if slot.value is None:
        return None
    return UserEvidence(
        value=slot.value,
        source=slot.source or "user",
        confidence=slot.confidence,
    )


def fuse_evidence(
    video: VideoAnalysisResult,
    user_state: AccidentState,
    *,
    threshold: Optional[float] = None,
    factor_answers: Optional[dict[str, FactorAnswer]] = None,
) -> EvidenceFusionResult:
    facts = {}
    factor_answers = factor_answers or {}
    video_slots = set(VIDEO_STATE_SLOTS)

    for slot_name in ALL_SLOTS:
        vision = getattr(video, slot_name) if slot_name in video_slots else None
        if vision is not None and vision.value is None:
            vision = None
        user = _user_evidence(user_state, slot_name)

        if vision is not None and user is not None:
            if normalize_evidence_value(vision.value) == normalize_evidence_value(
                user.value
            ):
                fact = FinalFact(
                    slot=slot_name,
                    resolved_value=vision.value,
                    status="corroborated",
                    selected_source="both",
                    vision=vision,
                    user=user,
                    resolution_reason="영상 관찰값과 사용자 진술이 일치함",
                )
            elif _strong_vision_evidence(vision, threshold):
                fact = FinalFact(
                    slot=slot_name,
                    resolved_value=vision.value,
                    status="disputed_vision_preferred",
                    selected_source="vision",
                    vision=vision,
                    user=user,
                    resolution_reason=(
                        "사용자 진술과 다르지만 직접 영상 또는 센서 근거가 "
                        "기준 신뢰도 이상으로 확인됨"
                    ),
                )
            else:
                fact = FinalFact(
                    slot=slot_name,
                    status="disputed_unresolved",
                    vision=vision,
                    user=user,
                    resolution_reason=(
                        "영상값이 추론이거나 근거 신뢰도가 낮아 자동 선택하지 않음"
                    ),
                )
        elif vision is not None:
            if _strong_vision_evidence(vision, threshold):
                fact = FinalFact(
                    slot=slot_name,
                    resolved_value=vision.value,
                    status="verified_by_vision",
                    selected_source="vision",
                    vision=vision,
                    resolution_reason="직접 영상 또는 센서 근거가 기준 신뢰도 이상임",
                )
            else:
                fact = FinalFact(
                    slot=slot_name,
                    status="vision_inferred",
                    vision=vision,
                    resolution_reason="영상 추론값 또는 저신뢰 값이므로 추가 확인이 필요함",
                )
        elif user is not None:
            fact = FinalFact(
                slot=slot_name,
                resolved_value=user.value,
                status="user_claimed",
                selected_source="user",
                user=user,
                resolution_reason="영상 근거가 없으며 사용자 진술로 수집됨",
            )
        else:
            fact = FinalFact(
                slot=slot_name,
                status="unresolved",
                resolution_reason="영상과 사용자 양쪽에서 값이 확인되지 않음",
            )
        facts[slot_name] = fact

    for observation in video.additional_observations:
        if observation.fact_key in facts or observation.value is None:
            continue
        answer = factor_answers.get(observation.fact_key)
        user = (
            UserEvidence(
                value=answer.value,
                source="user",
                confidence=answer.confidence,
            )
            if answer
            else None
        )
        if user and normalize_evidence_value(observation.value) == normalize_evidence_value(
            user.value
        ):
            facts[observation.fact_key] = FinalFact(
                slot=observation.fact_key,
                resolved_value=observation.value,
                status="corroborated",
                selected_source="both",
                vision=observation,
                user=user,
                resolution_reason=f"범용 관찰 '{observation.fact_label}'과 사용자 답변이 일치함",
            )
        elif user and _strong_vision_evidence(observation, threshold):
            facts[observation.fact_key] = FinalFact(
                slot=observation.fact_key,
                resolved_value=observation.value,
                status="disputed_vision_preferred",
                selected_source="vision",
                vision=observation,
                user=user,
                resolution_reason=f"범용 관찰 '{observation.fact_label}'에 강한 영상 근거가 있음",
            )
        elif user:
            facts[observation.fact_key] = FinalFact(
                slot=observation.fact_key,
                status="disputed_unresolved",
                vision=observation,
                user=user,
                resolution_reason=f"범용 관찰 '{observation.fact_label}'과 답변 차이를 자동 확정할 수 없음",
            )
        elif _strong_vision_evidence(observation, threshold):
            facts[observation.fact_key] = FinalFact(
                slot=observation.fact_key,
                resolved_value=observation.value,
                status="verified_by_vision",
                selected_source="vision",
                vision=observation,
                resolution_reason=(
                    f"범용 영상 관찰 '{observation.fact_label}'에 직접 근거가 있음"
                ),
            )
        else:
            facts[observation.fact_key] = FinalFact(
                slot=observation.fact_key,
                status="vision_inferred",
                vision=observation,
                resolution_reason=(
                    f"범용 영상 관찰 '{observation.fact_label}'이 추론 또는 저신뢰임"
                ),
            )

    for factor_id, answer in factor_answers.items():
        if factor_id in facts:
            continue
        facts[factor_id] = FinalFact(
            slot=factor_id,
            resolved_value=answer.value,
            status="user_claimed",
            selected_source="user",
            user=UserEvidence(
                value=answer.value,
                source="user",
                confidence=answer.confidence,
            ),
            resolution_reason="동적 Fact 질문에 대한 사용자 답변으로 수집됨",
        )

    return EvidenceFusionResult(
        facts=facts,
        observed_events=video.timeline_events,
    )


def resolved_state_from_fusion(fusion: EvidenceFusionResult) -> AccidentState:
    """Build the working state used to avoid asking an answered question again.

    A disputed low-confidence vision value remains unresolved in final facts,
    but the user's answer is still placed in this working state because asking
    the same question again cannot resolve that evidentiary dispute.
    """
    state = AccidentState()
    for slot_name, fact in fusion.facts.items():
        if slot_name not in ALL_SLOTS:
            continue
        working_value = fact.resolved_value
        working_source = fact.selected_source
        if working_value is None and fact.user is not None:
            working_value = fact.user.value
            working_source = "user"
        if working_value is None:
            continue
        slot = getattr(state, slot_name)
        slot.value = working_value
        slot.source = working_source
        if fact.selected_source == "vision" and fact.vision:
            slot.confidence = fact.vision.confidence
        elif fact.user:
            slot.confidence = fact.user.confidence
    return state
