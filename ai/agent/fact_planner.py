import json
from pathlib import Path

from pydantic import ValidationError

from agent.llm_client import call_json, call_text
from schemas.accident_state import AccidentState
from schemas.evidence_state import EvidenceFusionResult
from schemas.fact_plan import (
    AccidentHypothesis,
    FactPlan,
    FactorAnswer,
    RequiredFactor,
)
from schemas.slot_schema import ALL_SLOTS, slot_description
from schemas.video_analysis import VideoAnalysisResult

_ROOT = Path(__file__).resolve().parent.parent
_PLAN_PROMPT = (_ROOT / "prompts" / "plan_factors.txt").read_text(encoding="utf-8")
_QUESTION_PROMPT = (_ROOT / "prompts" / "generate_factor_question.txt").read_text(
    encoding="utf-8"
)
_ANSWER_PROMPT = (_ROOT / "prompts" / "extract_factor_answer.txt").read_text(
    encoding="utf-8"
)

_IMPORTANCE_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_RESOLVED_STATUSES = {
    "confirmed_by_vision",
    "corroborated",
    "user_claimed",
    "disputed_vision_preferred",
    "external_required",
}

_CATEGORY_ALIASES = {
    "maneuver": "actor_behavior",
    "behavior": "actor_behavior",
    "vehicle_behavior": "actor_behavior",
    "speed": "actor_behavior",
    "speed_control": "actor_behavior",
    "signal": "traffic_control",
    "right_of_way": "traffic_control",
    "priority": "traffic_control",
    "collision": "collision_geometry",
    "collision_detail": "collision_geometry",
    "contact": "collision_geometry",
    "timeline": "temporal_sequence",
    "sequence": "temporal_sequence",
    "road": "road_context",
    "lane": "spatial_relationship",
    "external": "external_claim",
}
_STATUS_ALIASES = {
    "verified_by_vision": "confirmed_by_vision",
    "vision_verified": "confirmed_by_vision",
    "vision_inferred": "inferred_by_vision",
    "inferred": "inferred_by_vision",
    "not_observable": "not_visible",
    "missing": "unresolved",
    "pending": "unresolved",
}
_SOURCE_ALIASES = {
    "video": "vision",
    "video_then_user": "vision_then_user",
    "vision_or_user": "vision_then_user",
    "document": "external",
}


def _normalize_plan_response(raw: dict) -> FactPlan:
    hypotheses = []
    for candidate in raw.get("accident_hypotheses") or []:
        try:
            hypotheses.append(AccidentHypothesis.model_validate(candidate))
        except (ValidationError, TypeError):
            continue

    factors = []
    for candidate in raw.get("required_factors") or []:
        if not isinstance(candidate, dict):
            continue
        normalized = dict(candidate)
        category = str(normalized.get("category", "other")).lower()
        normalized["category"] = _CATEGORY_ALIASES.get(category, category)
        if normalized["category"] not in RequiredFactor.model_fields[
            "category"
        ].annotation.__args__:
            normalized["category"] = "other"
        status = str(normalized.get("status", "unresolved")).lower()
        normalized["status"] = _STATUS_ALIASES.get(status, status)
        source = str(
            normalized.get("preferred_source", "vision_then_user")
        ).lower()
        normalized["preferred_source"] = _SOURCE_ALIASES.get(source, source)
        importance = str(normalized.get("importance", "high")).lower()
        normalized["importance"] = (
            importance if importance in _IMPORTANCE_ORDER else "high"
        )
        try:
            factors.append(RequiredFactor.model_validate(normalized))
        except (ValidationError, TypeError):
            continue

    return FactPlan(
        plan_summary=str(raw.get("plan_summary") or "동적 사실 수집 계획"),
        accident_hypotheses=hypotheses,
        required_factors=factors,
    )


def plan_required_factors(
    video: VideoAnalysisResult,
    fusion: EvidenceFusionResult,
    state: AccidentState,
) -> FactPlan:
    prompt = _PLAN_PROMPT.format(
        accident_type=json.dumps(state.accident_type.model_dump(), ensure_ascii=False),
        video_analysis=json.dumps(video.model_dump(), ensure_ascii=False),
        fusion=json.dumps(fusion.model_dump(), ensure_ascii=False),
    )
    raw = call_json(prompt, model_env_var="INTAKE_MODEL")
    plan = _normalize_plan_response(raw)
    plan.accident_hypotheses = sorted(
        plan.accident_hypotheses, key=lambda item: item.confidence, reverse=True
    )[:3]
    unique_factors = {}
    for factor in plan.required_factors:
        if factor.factor_id not in unique_factors:
            unique_factors[factor.factor_id] = factor
    plan.required_factors = sorted(
        unique_factors.values(), key=lambda item: _IMPORTANCE_ORDER[item.importance]
    )[:12]
    if not plan.required_factors:
        fallback_slots = state.missing_slots or state.active_slots
        plan.plan_summary = (
            f"{plan.plan_summary} (동적 계획이 비어 있어 기본 사실 수집 안전망 적용)"
        )
        plan.required_factors = [
            RequiredFactor(
                factor_id=slot_name,
                description=slot_description(slot_name) or slot_name,
                importance="high",
                reason="사고 유형과 과실 판단에 필요한 기본 사실",
                preferred_source="vision_then_user",
                related_fact_keys=[slot_name],
                video_recheck_instruction=(
                    f"원본 영상에서 {slot_description(slot_name) or slot_name}을 "
                    "시간 근거와 함께 다시 확인"
                ),
            )
            for slot_name in fallback_slots[:12]
        ]
    return plan


def refresh_factor_statuses(
    plan: FactPlan,
    fusion: EvidenceFusionResult,
    factor_answers: dict[str, FactorAnswer],
) -> FactPlan:
    for factor in plan.required_factors:
        if factor.factor_id in factor_answers:
            factor.status = "user_claimed"
            continue

        keys = factor.related_fact_keys or [factor.factor_id]
        related = [fusion.facts[key] for key in keys if key in fusion.facts]
        if not related:
            factor.status = (
                "external_required"
                if factor.preferred_source == "external"
                else "not_visible"
            )
            continue

        statuses = {fact.status for fact in related}
        if "disputed_unresolved" in statuses:
            factor.status = "disputed_unresolved"
        elif "vision_inferred" in statuses:
            factor.status = "inferred_by_vision"
        elif "unresolved" in statuses:
            factor.status = "unresolved"
        elif "disputed_vision_preferred" in statuses:
            factor.status = "disputed_vision_preferred"
        elif statuses <= {"verified_by_vision", "corroborated"}:
            factor.status = (
                "corroborated" if "corroborated" in statuses else "confirmed_by_vision"
            )
        elif statuses == {"user_claimed"}:
            factor.status = "user_claimed"
        else:
            factor.status = "unresolved"
    return plan


def unresolved_factors(plan: FactPlan) -> list[RequiredFactor]:
    return [
        factor
        for factor in plan.required_factors
        if factor.importance in {"critical", "high"}
        and factor.status not in _RESOLVED_STATUSES
    ]


def factors_for_video_recheck(plan: FactPlan) -> list[RequiredFactor]:
    return [
        factor
        for factor in unresolved_factors(plan)
        if factor.preferred_source in {"vision", "vision_then_user"}
        and factor.video_recheck_instruction
    ]


def next_question_factor(plan: FactPlan) -> RequiredFactor | None:
    pending = unresolved_factors(plan)
    return pending[0] if pending else None


def generate_factor_question(
    factor: RequiredFactor,
    plan: FactPlan,
    fusion: EvidenceFusionResult,
    history: list,
) -> str:
    resolved = {
        key: fact.resolved_value
        for key, fact in fusion.facts.items()
        if fact.resolved_value is not None
    }
    prompt = _QUESTION_PROMPT.format(
        factor=json.dumps(factor.model_dump(), ensure_ascii=False),
        hypotheses=json.dumps(
            [item.model_dump() for item in plan.accident_hypotheses],
            ensure_ascii=False,
        ),
        resolved_facts=json.dumps(resolved, ensure_ascii=False),
        history=json.dumps(history[-6:], ensure_ascii=False),
    )
    return call_text(prompt, model_env_var="INTAKE_MODEL")


def extract_factor_answer(
    factor: RequiredFactor,
    last_question: str,
    user_input: str,
) -> FactorAnswer:
    prompt = _ANSWER_PROMPT.format(
        factor=json.dumps(factor.model_dump(), ensure_ascii=False),
        last_question=json.dumps(last_question, ensure_ascii=False),
        user_input=json.dumps(user_input, ensure_ascii=False),
        available_fact_keys=json.dumps(ALL_SLOTS, ensure_ascii=False),
    )
    raw = call_json(prompt, model_env_var="INTAKE_MODEL")
    raw["factor_id"] = factor.factor_id
    answer = FactorAnswer.model_validate(raw)
    answer.related_fact_updates = {
        key: value
        for key, value in answer.related_fact_updates.items()
        if key in ALL_SLOTS and value is not None
    }
    return answer
