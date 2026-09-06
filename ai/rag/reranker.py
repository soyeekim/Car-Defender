"""Retrieved Case Validation / Reranking (가이드 15.5 / 82 / 84절).

단순 텍스트 유사도가 아니라 도로형태·진행방향·신호·선진입·충돌관계·기본과실·수정요소를
현재 사건과 비교하여 relevance를 매기고, 제공된 case_id만 사용한다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt
from state.case_state import CaseRelevance, CaseState, RetrievedCase
from telemetry import RunLogger, get_run_logger


class _Validations(BaseModel):
    validations: list[CaseRelevance] = Field(default_factory=list)


def compact_case_for_prompt(case: RetrievedCase, *, description_chars: int = 700) -> dict:
    compact = {
        "case_id": case.case_id,
        "source_type": case.source_type,
        "title": case.title,
        "accident_type": case.accident_type,
        "chart_number": case.chart_number,
        "basic_ratio": case.basic_ratio,
        "decision_ratio": case.decision_ratio,
        "accident_description": (case.accident_description or case.excerpt)[:description_chars],
        "key_issues": case.key_issues[:4],
        "decision_reasons": [item[:300] for item in case.decision_reasons[:4]],
        "modification_factors": case.modification_factors[:6],
        "metadata": case.metadata.model_dump(exclude_none=True),
        "retrieval_similarity": round(case.similarity, 3),
    }
    if case.source_type != "deliberation_case":
        # 인정기준 도표: A·B 역할과 변형별 기본비율이 사건과의 대응을 정한다
        compact["role_a"] = case.role_a
        compact["role_b"] = case.role_b
        if case.chart_variants:
            compact["variants"] = [f"{item.label} {item.description} → 기본 {item.basic_ratio}".strip() for item in case.chart_variants]
        compact["modification_factors"] = [item.text() for item in case.chart_modifiers[:12]] or compact["modification_factors"]
    return compact


def _ensure_deliberation_case(ranked: list[RetrievedCase], pool: list[RetrievedCase], top_k: int) -> list[RetrievedCase]:
    if any(item.source_type == "deliberation_case" and item.decision_ratio for item in ranked[:top_k]):
        return ranked[:top_k]
    fallback = next(
        (item for item in pool if item.source_type == "deliberation_case" and item.decision_ratio and item not in ranked[:top_k]),
        None,
    )
    if fallback is None:
        return ranked[:top_k]
    selected = ranked[: max(0, top_k - 1)] + [fallback]
    return selected


def rerank_cases(
    client: Optional[TextClient],
    state: CaseState,
    candidates: list[RetrievedCase],
    *,
    top_k: int = 5,
    run_logger: Optional[RunLogger] = None,
    max_candidates: int = 8,
) -> list[RetrievedCase]:
    if not candidates:
        return []
    logger = run_logger or get_run_logger()
    by_id = {item.case_id: item for item in candidates}

    if client is not None:
        system = load_prompt("master_agent", "system")
        task = load_prompt("master_agent", "case_validation")
        # 후보 8건·설명 500자로 제한: rerank 호출이 RAG 턴 지연의 대부분(약 20초)을 차지한다 (도표는 짧아서 12건까지)
        user = task.render(
            case_state=compact_json(state.compact(include_timeline=False), max_chars=6000),
            retrieved_cases=compact_json([compact_case_for_prompt(item, description_chars=500) for item in candidates[:max_candidates]], max_chars=20000),
        )
        try:
            response = client.generate_json(system=system.text, user=user, schema=_Validations, task=task.task)
            validations = _Validations.model_validate(response.data).validations
            logger.log(
                agent="master_agent",
                task=task.task,
                case_id=state.case_id,
                model=response.metrics.model,
                prompt_version=f"{system.version_id}+{task.version_id}",
                metrics=response.metrics,
                extra={"candidates": len(candidates), "validated": len(validations)},
            )
            invented = [item.case_id for item in validations if item.case_id not in by_id]
            for item in validations:
                case = by_id.get(item.case_id)
                if case is None:
                    continue
                case.relevance = item
            if invented:
                logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"ignored_invented_case_ids": invented})
        except Exception as exc:  # noqa: BLE001
            logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300], "fallback": "similarity_order"})

    def score(case: RetrievedCase) -> float:
        if case.relevance is None:
            return case.similarity * 0.5
        base = case.relevance.relevance * 0.7 + case.similarity * 0.3
        if case.relevance.usable_as_primary_reference:
            base += 0.1
        if case.source_type == "deliberation_case" and case.decision_ratio:
            base += 0.03
        return base

    ranked = sorted(candidates, key=score, reverse=True)
    selected = _ensure_deliberation_case(ranked, ranked, top_k)
    return selected
