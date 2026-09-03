import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from agent.llm_client import call_json
from rag.pdf_index import (
    DEFAULT_INDEX_DIR,
    build_index,
    search_index,
)
from schemas.post_intake import (
    IncidentReport,
    PostIntakeResult,
    RagResult,
    SimilarCaseAssessment,
)
from schemas.accident_state import UNKNOWN_MARKERS
from schemas.slot_schema import slot_description

_ROOT = Path(__file__).resolve().parent.parent
_REPORT_PROMPT = (_ROOT / "prompts" / "generate_incident_report.txt").read_text(
    encoding="utf-8"
)
_ASSESS_PROMPT = (_ROOT / "prompts" / "assess_retrieved_cases.txt").read_text(
    encoding="utf-8"
)

_SLOT_RETRIEVAL_CATEGORIES = {
    "accident_target": "road_context",
    "accident_place": "road_context",
    "place_signal_presence": "road_context",
    "ego_maneuver": "actor_behavior",
    "opponent_maneuver": "actor_behavior",
    "ego_speed": "actor_behavior",
    "opponent_speed": "actor_behavior",
    "pre_collision_sudden_braking": "actor_behavior",
    "braking_reason": "actor_behavior",
    "turn_signal": "actor_behavior",
    "lane_change_direction": "actor_behavior",
    "left_turn_type": "actor_behavior",
    "ego_signal": "traffic_control",
    "opponent_signal": "traffic_control",
    "pedestrian_signal": "traffic_control",
    "ego_lane": "spatial_relationship",
    "opponent_lane": "spatial_relationship",
    "intersection_entry_order": "spatial_relationship",
    "pedestrian_crossing_state": "spatial_relationship",
    "ego_collision_area": "collision_geometry",
    "opponent_collision_area": "collision_geometry",
    "collision_type": "collision_geometry",
    "collision_timestamp": "temporal_sequence",
    "speed_change_before_collision": "temporal_sequence",
    "braking_evidence": "temporal_sequence",
    "post_collision_stop": "post_accident",
    "opponent_claimed_fault_ratio": "external_claim",
}


def _fact_lines(fusion) -> tuple[list[str], list[str], list[str], list[str]]:
    confirmed, claims, disputed, unknown = [], [], [], []
    for key, fact in fusion.facts.items():
        label = slot_description(key) or key
        if fact.status in {"verified_by_vision", "corroborated"}:
            confirmed.append(f"{label}: {fact.resolved_value}")
        elif fact.status == "user_claimed":
            normalized = str(fact.resolved_value or "").strip().lower()
            if normalized in {value.lower() for value in UNKNOWN_MARKERS}:
                unknown.append(f"{label}: 사용자가 모른다고 답함")
            else:
                claims.append(f"{label}: {fact.resolved_value}")
        elif fact.status.startswith("disputed"):
            values = []
            if fact.vision and fact.vision.value is not None:
                values.append(f"영상={fact.vision.value}")
            if fact.user:
                values.append(f"진술={fact.user.value}")
            disputed.append(f"{label}: {', '.join(values)}")
        elif fact.status == "vision_inferred":
            value = fact.vision.value if fact.vision else None
            unknown.append(f"{label}: 영상 추론 {value}" if value else label)
    return confirmed, claims, disputed, unknown


def _retrieval_factors(agent) -> dict[str, list[str]]:
    """확인된 사실을 사고요소별로 묶되 출처 상태를 잃지 않는다."""
    categories: dict[str, list[str]] = defaultdict(list)
    for hypothesis in agent.fact_plan.accident_hypotheses:
        value = hypothesis.family
        if hypothesis.subtype:
            value += f"/{hypothesis.subtype}"
        categories["accident_type"].append(value)

    dynamic_categories = {}
    for factor in agent.fact_plan.required_factors:
        dynamic_categories[factor.factor_id] = factor.category
        for fact_key in factor.related_fact_keys:
            dynamic_categories.setdefault(fact_key, factor.category)

    for key, fact in agent.fusion.facts.items():
        label = slot_description(key) or key
        category = _SLOT_RETRIEVAL_CATEGORIES.get(
            key, dynamic_categories.get(key, "other")
        )
        if fact.status in {"verified_by_vision", "corroborated", "user_claimed"}:
            value = str(fact.resolved_value or "").strip()
            if not value or value.lower() in {marker.lower() for marker in UNKNOWN_MARKERS}:
                continue
            evidence_label = (
                "영상확인"
                if fact.status == "verified_by_vision"
                else "교차확인"
                if fact.status == "corroborated"
                else "사용자진술"
            )
            categories[category].append(f"{label}: {value} [{evidence_label}]")
        elif fact.status.startswith("disputed"):
            values = []
            if fact.vision and fact.vision.value is not None:
                values.append(f"영상={fact.vision.value}")
            if fact.user and fact.user.value is not None:
                values.append(f"진술={fact.user.value}")
            if values:
                categories["disputed"].append(f"{label}: {', '.join(values)}")
    return {key: list(dict.fromkeys(values)) for key, values in categories.items() if values}


def _fallback_report(agent) -> IncidentReport:
    confirmed, claims, disputed, unknown = _fact_lines(agent.fusion)
    hypotheses = [
        f"{item.family}/{item.subtype or '미분류'} ({item.confidence:.2f})"
        for item in agent.fact_plan.accident_hypotheses
    ]
    narrative_parts = [agent.video_analysis.summary]
    if confirmed:
        narrative_parts.append("확인 사실: " + "; ".join(confirmed[:12]))
    if claims:
        narrative_parts.append("사용자 진술: " + "; ".join(claims[:8]))
    retrieval_query = " ".join(
        hypotheses + confirmed[:12] + claims[:8] + disputed[:5]
    ).strip()
    return IncidentReport(
        title=(hypotheses[0] if hypotheses else "교통사고 사건경위서"),
        location=agent.state.accident_place.value,
        parties=["블랙박스 장착 차량", "영상 속 상대 당사자"],
        objective_narrative=" ".join(narrative_parts),
        timeline=[event.description for event in agent.video_analysis.timeline_events],
        confirmed_facts=confirmed,
        user_claims=claims,
        disputed_facts=disputed,
        unknown_facts=unknown,
        accident_hypotheses=hypotheses,
        retrieval_factors=_retrieval_factors(agent),
        retrieval_query=retrieval_query or agent.video_analysis.summary,
        generation_method="deterministic_fallback",
    )


def generate_incident_report(agent) -> IncidentReport:
    confirmed, claims, disputed, unknown = _fact_lines(agent.fusion)
    prompt = _REPORT_PROMPT.format(
        accident_type=json.dumps(agent.state.accident_type.model_dump(), ensure_ascii=False),
        fact_plan=json.dumps(agent.fact_plan.model_dump(), ensure_ascii=False),
        final_facts=json.dumps(agent.fusion.model_dump(), ensure_ascii=False),
        evidence_groups=json.dumps(
            {
                "confirmed_facts": confirmed,
                "user_claims": claims,
                "disputed_facts": disputed,
                "unknown_or_inferred_facts": unknown,
            },
            ensure_ascii=False,
        ),
        video_summary=json.dumps(agent.video_analysis.summary, ensure_ascii=False),
        conversation=json.dumps(agent.history, ensure_ascii=False),
    )
    try:
        raw = call_json(prompt, model_env_var="DOCUMENT_MODEL")
        raw["generation_method"] = "llm"
        report = IncidentReport.model_validate(raw)
    except Exception:
        report = _fallback_report(agent)
    return _enforce_grounded_report(agent, report)


def _enforce_grounded_report(agent, report: IncidentReport) -> IncidentReport:
    confirmed, claims, disputed, unknown = _fact_lines(agent.fusion)
    report.confirmed_facts = confirmed
    report.user_claims = claims
    report.disputed_facts = disputed
    report.unknown_facts = unknown
    report.accident_hypotheses = [
        f"{item.family}/{item.subtype or '미분류'} ({item.confidence:.2f})"
        for item in agent.fact_plan.accident_hypotheses
    ]
    report.retrieval_factors = _retrieval_factors(agent)
    report.location = agent.state.accident_place.value

    narrative = []
    if confirmed:
        narrative.append("영상 및 양측 증거로 확인된 사실은 " + "; ".join(confirmed) + "이다.")
    if claims:
        narrative.append("사용자는 " + "; ".join(claims) + "라고 진술하였다.")
    if disputed:
        narrative.append("영상과 사용자 진술이 서로 다른 항목은 " + "; ".join(disputed) + "이다.")
    if unknown:
        narrative.append("추가 확인이 필요한 항목은 " + "; ".join(unknown) + "이다.")
    if not narrative:
        narrative.append("현재 Evidence Fusion에서 확정하거나 진술로 수집된 사고 사실이 부족하다.")
    report.objective_narrative = " ".join(narrative)

    threshold = max(
        0.0,
        min(1.0, float(os.getenv("VISION_VERIFIED_THRESHOLD", "0.8"))),
    )
    direct_events = [
        event.description
        for event in agent.video_analysis.timeline_events
        if event.observation_type in {"direct_visual", "sensor_readout"}
        and event.confidence >= threshold
        and event.evidence
        and event.start_timestamp
    ]
    report.timeline = direct_events
    query_categories = [
        "accident_type",
        "road_context",
        "actor_behavior",
        "traffic_control",
        "spatial_relationship",
        "collision_geometry",
        "temporal_sequence",
        "post_accident",
        "other",
        "disputed",
    ]
    query_parts = [
        item
        for category in query_categories
        for item in report.retrieval_factors.get(category, [])
    ]
    report.retrieval_query = " ".join(query_parts).strip()
    if not report.retrieval_query:
        report.retrieval_query = "교통사고 유사 심의사례"
    return report


def _assess_sources(report: IncidentReport, candidates) -> list[SimilarCaseAssessment]:
    compact_sources = [
        {
            "source_id": item.source_id,
            "source_type": item.source_type,
            "source_file": item.source_file,
            "page_number": item.page_number,
            "page_start": item.page_start or item.page_number,
            "page_end": item.page_end or item.page_number,
            "case_number": item.case_number,
            "chart_number": item.chart_number,
            "decision_ratio": item.decision_ratio,
            "similarity_score": item.similarity_score,
            "semantic_score": item.semantic_score,
            "lexical_score": item.lexical_score,
            "matched_sections": item.matched_sections,
            "excerpt": item.excerpt,
        }
        for item in candidates
    ]
    prompt = _ASSESS_PROMPT.format(
        incident_report=json.dumps(report.model_dump(), ensure_ascii=False),
        retrieved_sources=json.dumps(compact_sources, ensure_ascii=False),
    )
    by_id = {item.source_id: item for item in candidates}
    try:
        raw = call_json(prompt, model_env_var="REASONING_MODEL")
    except Exception:
        raw = {"selected_sources": []}

    assessments = []
    used = set()
    for item in raw.get("selected_sources", []):
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        if source_id not in by_id or source_id in used:
            continue
        used.add(source_id)
        assessments.append(
            SimilarCaseAssessment(
                source=by_id[source_id],
                relevance_reason=str(item.get("relevance_reason") or "검색 질의와 유사함"),
                matching_factors=[str(value) for value in item.get("matching_factors", [])],
                differing_factors=[str(value) for value in item.get("differing_factors", [])],
                applicability_caution=str(
                    item.get("applicability_caution")
                    or "원문 전체와 실제 증거를 함께 검토해야 합니다."
                ),
            )
        )
        if len(assessments) >= 5:
            break

    if not assessments:
        for candidate in candidates[:3]:
            assessments.append(
                SimilarCaseAssessment(
                    source=candidate,
                    relevance_reason="임베딩 검색에서 높은 유사도를 보인 자료",
                    matching_factors=[],
                    differing_factors=[],
                    applicability_caution="LLM 재평가를 완료하지 못해 원문 직접 검토가 필요합니다.",
                )
            )
    _ensure_source_type_coverage(assessments, candidates)
    return assessments


def _ensure_source_type_coverage(assessments, candidates) -> None:
    required_groups = [
        {"deliberation_case"},
        {"fault_standard", "roundabout_special_standard"},
    ]
    selected_ids = {item.source.source_id for item in assessments}
    for group in required_groups:
        if any(item.source.source_type in group for item in assessments):
            continue
        candidate = next(
            (
                item
                for item in candidates
                if item.source_type in group and item.source_id not in selected_ids
                and (item.source_type != "deliberation_case" or item.case_number)
            ),
            None,
        )
        if candidate is None:
            candidate = next(
                (
                    item
                    for item in candidates
                    if item.source_type in group and item.source_id not in selected_ids
                ),
                None,
            )
        if candidate is None:
            continue
        fallback = SimilarCaseAssessment(
            source=candidate,
            relevance_reason="출처 유형을 교차 검토하기 위해 포함한 상위 유사 자료",
            matching_factors=[],
            differing_factors=[],
            applicability_caution="원문 전체와 현재 사고의 세부 사실을 비교해야 합니다.",
        )
        if len(assessments) >= 5:
            removed = assessments.pop()
            selected_ids.discard(removed.source.source_id)
        assessments.append(fallback)
        selected_ids.add(candidate.source_id)


def run_post_intake_pipeline(
    agent,
    *,
    index_dir: str | Path = DEFAULT_INDEX_DIR,
    progress: Optional[Callable[[str], None]] = None,
    embedder=None,
) -> PostIntakeResult:
    if not agent._is_complete():
        raise ValueError("Multi-turn Intake가 완료된 뒤에만 사건경위서와 RAG를 실행할 수 있습니다.")
    if progress:
        progress("수집된 증거로 객관적인 사건경위서를 생성합니다.")
    report = generate_incident_report(agent)
    rag = retrieve_similar_sources(
        report,
        index_dir=index_dir,
        progress=progress,
        embedder=embedder,
    )
    return PostIntakeResult(incident_report=report, rag=rag)


def retrieve_similar_sources(
    report: IncidentReport,
    *,
    index_dir: str | Path = DEFAULT_INDEX_DIR,
    progress: Optional[Callable[[str], None]] = None,
    embedder=None,
) -> RagResult:
    build_index(index_dir=index_dir, embedder=embedder, progress=progress)
    if progress:
        progress("사건경위서와 유사한 심의사례·인정기준을 검색합니다.")
    candidates = search_index(
        report.retrieval_query,
        index_dir=index_dir,
        top_k=12,
        embedder=embedder,
    )
    similar_cases = _assess_sources(report, candidates)
    return RagResult(
        retrieval_query=report.retrieval_query,
        embedding_model=_read_embedding_model(index_dir),
        candidates=candidates,
        similar_cases=similar_cases,
    )


def _read_embedding_model(index_dir: str | Path) -> str:
    path = Path(index_dir).expanduser().resolve() / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["embedding_model"]
