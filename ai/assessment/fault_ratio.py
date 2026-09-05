"""예상 과실비율 판정 (가이드 2.1-E / 15.6 / 85~90절).

영상 사실 + 사용자 확인 사실 + 유사 심의사례(기본과실·수정요소)를 종합한다.
결과는 단일 숫자가 아니라 근거·신뢰도·범위·불확실성을 함께 제공한다.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt
from rag.reranker import compact_case_for_prompt
from state.case_state import CaseState, FaultAssessment, FaultRatio, MatchedCaseSummary, RetrievedCase
from state.updater import get_slot
from telemetry import RunLogger, get_run_logger

_RATIO = re.compile(r"(\d{1,3})\s*[:대]\s*(\d{1,3})")
_UNVERIFIED_MARKERS = ("불명확", "확인 불가", "확인불가", "미확인", "확인되지 않", "unknown", "불분명", "확인할 수 없", "가능성", "추정")


class AssessmentPreconditions(BaseModel):
    ok: bool = False
    missing: list[str] = Field(default_factory=list)
    checklist: dict[str, bool] = Field(default_factory=dict)


def check_assessment_preconditions(state: CaseState, retrieved_cases: Optional[list[RetrievedCase]] = None) -> AssessmentPreconditions:
    """가이드 85절 체크리스트."""
    cases = retrieved_cases if retrieved_cases is not None else state.retrieved_cases

    def known(path: str) -> bool:
        slot = get_slot(state, path)
        return bool(slot and slot.is_known())

    signal_present = (get_slot(state, "road.signal_present").value or "").lower() if get_slot(state, "road.signal_present") else ""
    signal_ok = True
    if signal_present == "true":
        signal_ok = (
            known("road.signal_state")
            or known("ego_vehicle.signal")
            or known("other_vehicle.signal")
            or any("신호" in item or "signal" in item.lower() for item in state.uncertain_facts)
        )
    checklist = {
        "user_vehicle_identified": bool(state.ego_vehicle.vehicle_id),
        "opponent_vehicle_identified": bool(state.other_vehicle.vehicle_id),
        "road_type_known": known("road.road_type"),
        "user_movement_known": known("ego_vehicle.movement"),
        "opponent_movement_known": known("other_vehicle.movement"),
        "collision_type_known": known("collision.type"),
        "collision_timestamp_known": known("collision.timestamp"),
        "signal_or_lane_condition_declared": signal_ok,
        "similar_case_available": any(item.source_type == "deliberation_case" or item.basic_ratio for item in cases),
    }
    missing = [name for name, passed in checklist.items() if not passed]
    hard = {"user_vehicle_identified", "opponent_vehicle_identified", "road_type_known", "similar_case_available"}
    ok = not (set(missing) & hard) and len(missing) <= 2
    return AssessmentPreconditions(ok=ok, missing=missing, checklist=checklist)


def parse_ratio(text: Optional[str]) -> Optional[tuple[int, int]]:
    if not text:
        return None
    match = _RATIO.search(str(text))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _normalize_ratio(ratio: FaultRatio) -> FaultRatio:
    total = ratio.user + ratio.opponent
    if total == 100:
        return ratio
    if total <= 0:
        return FaultRatio(user=50, opponent=50)
    user = int(round(ratio.user * 100 / total))
    return FaultRatio(user=max(0, min(100, user)), opponent=max(0, min(100, 100 - user)))


def _normalize_range(values: Optional[list[str]]) -> Optional[list[str]]:
    if not values:
        return None
    normalized = []
    for item in values:
        parsed = parse_ratio(item)
        if parsed:
            normalized.append(f"{parsed[0]}:{parsed[1]}")
    return normalized or None


def select_anchor_case(retrieved_cases: list[RetrievedCase]) -> Optional[RetrievedCase]:
    """기준값으로 삼을 가장 유사한 사례: primary reference 가능 사례 우선, 비율 정보가 있는 것."""
    with_ratio = [item for item in retrieved_cases if parse_ratio(item.decision_ratio or item.basic_ratio)]
    if not with_ratio:
        return None
    primary = [item for item in with_ratio if item.relevance and item.relevance.usable_as_primary_reference]
    pool = primary or with_ratio
    return max(pool, key=lambda item: ((item.relevance.relevance if item.relevance else 0.0), item.similarity))


def anchor_ratio_text(case: Optional[RetrievedCase]) -> Optional[str]:
    if case is None:
        return None
    parsed = parse_ratio(case.decision_ratio) or parse_ratio(case.basic_ratio)
    return f"{parsed[0]}:{parsed[1]}" if parsed else None


def _enforce_anchor(assessment: FaultAssessment, anchor: Optional[RetrievedCase]) -> FaultAssessment:
    """확인된 수정요소 없이 기준값(가장 유사한 사례의 결정비율)에서 벗어난 판정은 기준값으로 되돌린다."""
    anchor_text = anchor_ratio_text(anchor)
    if anchor is None or not anchor_text:
        return assessment
    parsed = parse_ratio(anchor_text)
    assessment.anchor_case_id = anchor.case_id
    if parsed is None:
        return assessment
    current = (assessment.fault_ratio.user, assessment.fault_ratio.opponent)
    orientations = [parsed, (parsed[1], parsed[0])]
    if current in orientations:
        assessment.anchor_ratio = f"{current[0]}:{current[1]}"
        return assessment
    confirmed_adjustments = [
        item for item in assessment.adjustment_factors if item.applies and item.source in {"video", "user"}
    ]
    nearest = min(orientations, key=lambda option: abs(option[0] - current[0]))
    assessment.anchor_ratio = f"{nearest[0]}:{nearest[1]}"
    if confirmed_adjustments:
        return assessment
    original = f"{current[0]}:{current[1]}"
    assessment.fault_ratio = FaultRatio(user=nearest[0], opponent=nearest[1])
    assessment.anchor_enforced = True
    ranges = list(assessment.possible_range or [])
    for candidate in (assessment.anchor_ratio, original):
        if candidate not in ranges:
            ranges.append(candidate)
    assessment.possible_range = ranges
    assessment.reasoning_summary.insert(
        0,
        f"[기준값 적용] 가장 유사한 심의사례 {anchor.case_id}의 {'결정' if anchor.decision_ratio else '기본'}비율 {assessment.anchor_ratio}을(를) 기준값으로 두었습니다. "
        f"모델이 제시한 {original}은(는) 현재 사건에서 확인된 수정요소 없이 벗어난 값이라 범위로만 남겼습니다.",
    )
    assessment.ratio_dependencies.append(f"현재 사건에서 수정요소가 확인되면 {original} 방향으로 조정될 수 있음")
    return assessment


def _validate(assessment: FaultAssessment, retrieved_cases: list[RetrievedCase], *, provisional: bool) -> FaultAssessment:
    allowed = {item.case_id for item in retrieved_cases}
    assessment.fault_ratio = _normalize_ratio(assessment.fault_ratio)
    assessment = _enforce_anchor(assessment, select_anchor_case(retrieved_cases))
    assessment.most_likely = assessment.fault_ratio.as_text()
    assessment.possible_range = _normalize_range(assessment.possible_range)
    assessment.primary_case_ids = [case_id for case_id in assessment.primary_case_ids if case_id in allowed]
    assessment.matched_cases = [item for item in assessment.matched_cases if item.case_id in allowed]
    if not assessment.matched_cases:
        for case in retrieved_cases[:3]:
            assessment.matched_cases.append(
                MatchedCaseSummary(
                    case_id=case.case_id,
                    decision_ratio=case.decision_ratio,
                    basic_ratio=case.basic_ratio,
                    relevance=case.relevance.relevance if case.relevance else None,
                    role="primary" if case.case_id in assessment.primary_case_ids else "supporting",
                )
            )
    if not assessment.primary_case_ids and assessment.matched_cases:
        assessment.primary_case_ids = [assessment.matched_cases[0].case_id]
        assessment.matched_cases[0].role = "primary"
    for factor in assessment.adjustment_factors:
        # 확인되지 않은 수정요소는 '적용'으로 두지 않는다 (가이드: UNKNOWN 요소는 uncertainties로만)
        blob = f"{factor.factor} {factor.note}".lower()
        if any(marker in blob for marker in _UNVERIFIED_MARKERS):
            factor.applies = False
            if factor.direction != "unknown":
                factor.note = (factor.note + " " if factor.note else "") + "(확인 불가로 미적용)"
    assessment.confidence = max(0.0, min(1.0, assessment.confidence))
    if provisional:
        assessment.assessment_type = "provisional"
        assessment.confidence = min(assessment.confidence, 0.6)
        if not assessment.possible_range:
            user = assessment.fault_ratio.user
            assessment.possible_range = [f"{max(0, user - 10)}:{min(100, 100 - user + 10)}", f"{min(100, user + 10)}:{max(0, 90 - user)}"]
    if not assessment.explanation:
        assessment.explanation = (
            f"현재 영상과 확인된 사실, 유사 심의사례를 기준으로 사용자 {assessment.fault_ratio.user} : 상대 {assessment.fault_ratio.opponent} "
            "수준의 과실비율이 예상됩니다. 이는 예상치이며 확정 판단이 아닙니다."
        )
    return assessment


def deterministic_assessment(state: CaseState, retrieved_cases: list[RetrievedCase], *, reason: str) -> FaultAssessment:
    """LLM 없이 유사사례 기본비율로 만드는 provisional 판정 (fallback)."""
    primary = select_anchor_case(retrieved_cases)
    ratio = parse_ratio(primary.decision_ratio or primary.basic_ratio) if primary else None
    user, opponent = ratio if ratio else (50, 50)
    assessment = FaultAssessment(
        fault_ratio=FaultRatio(user=user, opponent=opponent),
        assessment_type="provisional",
        confidence=0.3 if ratio else 0.1,
        primary_case_ids=[primary.case_id] if primary else [],
        core_facts=[fact.fact for fact in state.video_confirmed_facts()[:8]],
        reasoning_summary=[
            f"가장 유사한 사례 {primary.case_id}의 {'결정' if primary.decision_ratio else '기본'}비율({primary.decision_ratio or primary.basic_ratio})을 A(사용자):B(상대) 방향 그대로 기준값으로 참고" if primary else "참고할 유사사례가 없어 50:50 기준값 사용",
            f"자동 판정 사유: {reason}",
        ],
        uncertainties=["LLM 종합 판정이 수행되지 않은 임시 결과", "청구/피청구 방향과 사용자/상대 방향의 일치 여부 미검증"] + state.uncertain_facts[:5],
        explanation="현재는 유사 심의사례의 기본비율만 참고한 임시 예상치입니다. 추가 사실 확인 후 재판정이 필요합니다.",
    )
    return _validate(assessment, retrieved_cases, provisional=True)


def assess_fault_ratio(
    client: Optional[TextClient],
    state: CaseState,
    retrieved_cases: Optional[list[RetrievedCase]] = None,
    *,
    run_logger: Optional[RunLogger] = None,
    force_provisional: bool = False,
) -> FaultAssessment:
    cases = retrieved_cases if retrieved_cases is not None else state.retrieved_cases
    preconditions = check_assessment_preconditions(state, cases)
    provisional = force_provisional or not preconditions.ok
    logger = run_logger or get_run_logger()

    if client is None:
        return deterministic_assessment(state, cases, reason="text client 없음")

    system = load_prompt("master_agent", "system")
    task = load_prompt("master_agent", "fault_assessment")
    anchor = select_anchor_case(cases)
    anchor_reference = (
        {
            "case_id": anchor.case_id,
            "title": anchor.title,
            "decision_ratio(A청구:B피청구)": anchor.decision_ratio,
            "basic_ratio": anchor.basic_ratio,
            "matched_factors": anchor.relevance.matched_factors if anchor.relevance else [],
            "different_factors": anchor.relevance.different_factors if anchor.relevance else [],
            "modification_factors": anchor.modification_factors[:6],
        }
        if anchor
        else "기준으로 삼을 비율 정보가 있는 사례 없음"
    )
    user = task.render(
        case_state=compact_json(state.compact(), max_chars=12000),
        retrieved_cases=compact_json([compact_case_for_prompt(item, description_chars=900) | {"validation": item.relevance.model_dump() if item.relevance else None} for item in cases], max_chars=30000),
        anchor_reference=compact_json(anchor_reference),
        review_answers=compact_json(state.review_answers) if state.review_answers else "(없음)",
        precondition_check=compact_json(preconditions.model_dump()),
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=FaultAssessment, task=task.task)
        assessment = FaultAssessment.model_validate(response.data)
        assessment.model = response.metrics.model
        assessment.prompt_version = f"{system.version_id}+{task.version_id}"
        logger.log(
            agent="master_agent",
            task=task.task,
            case_id=state.case_id,
            model=response.metrics.model,
            prompt_version=assessment.prompt_version,
            metrics=response.metrics,
            extra={"fault_ratio": assessment.fault_ratio.model_dump(), "provisional": provisional, "primary_case_ids": assessment.primary_case_ids},
        )
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300], "fallback": "deterministic_assessment"})
        return deterministic_assessment(state, cases, reason=f"LLM 판정 실패: {str(exc)[:80]}")

    if not preconditions.ok:
        assessment.uncertainties = list(dict.fromkeys(assessment.uncertainties + [f"판정 전제 조건 미충족: {', '.join(preconditions.missing)}"]))
    return _validate(assessment, cases, provisional=provisional)
