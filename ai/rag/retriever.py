"""Similar Case Retriever (가이드 80~81, 107~108절).

1. Metadata / accident-type filtering (soft boost + hard target filter)
2. Dense + lexical hybrid retrieval (`rag/pdf_index.search_index`, structured/detailed 두 query RRF 융합)
3. Case-level document 결합 (`rag/case_documents`)
→ Reranker(`rag/reranker`)로 전달할 후보 목록
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from rag.case_documents import CaseDocument, load_case_documents
from rag.pdf_index import DEFAULT_INDEX_DIR, RetrievedSource, search_index
from state.case_state import CaseMetadata, RagQuery, RetrievedCase

Embedder = Optional[Callable[[list[str], str], np.ndarray]]


def _metadata_boost(filters: CaseMetadata, doc_metadata: CaseMetadata) -> tuple[float, bool]:
    """(boost, drop). accident_target이 확실히 다르면 drop."""
    boost = 0.0
    drop = False
    if filters.accident_target and doc_metadata.accident_target:
        if filters.accident_target == doc_metadata.accident_target:
            boost += 0.03
        else:
            drop = True
    if filters.road_type and doc_metadata.road_type:
        boost += 0.04 if filters.road_type == doc_metadata.road_type else -0.02
    if filters.intersection_type and doc_metadata.intersection_type:
        boost += 0.03 if filters.intersection_type == doc_metadata.intersection_type else -0.01
    if filters.signal_present is not None and doc_metadata.signal_present is not None:
        boost += 0.03 if filters.signal_present == doc_metadata.signal_present else -0.02
    if filters.movement_a and doc_metadata.movement_a:
        pair_a = {filters.movement_a, filters.movement_b}
        pair_b = {doc_metadata.movement_a, doc_metadata.movement_b}
        if pair_a == pair_b:
            boost += 0.04
        elif pair_a & pair_b:
            boost += 0.01
    if filters.lane_change and doc_metadata.lane_change:
        boost += 0.03
    return boost, drop


DELIBERATION_SOURCES = {"deliberation_case"}
STANDARD_SOURCES = {"fault_standard", "roundabout_special_standard"}


def retrieve_candidates(
    query: RagQuery,
    *,
    index_dir: str | Path = DEFAULT_INDEX_DIR,
    case_docs: Optional[dict[str, CaseDocument]] = None,
    top_k: int = 20,
    embedder: Embedder = None,
    rrf_k: int = 60,
    source_types: Optional[set[str]] = None,
) -> list[RetrievedCase]:
    """source_types로 검색 대상 자료를 제한할 수 있다 (예: 심의사례만 → 없으면 인정기준)."""
    directory = Path(index_dir).expanduser().resolve()
    docs = case_docs if case_docs is not None else load_case_documents(directory)
    queries = [query.structured_query.strip()]
    if query.detailed_query.strip() and query.detailed_query.strip() != query.structured_query.strip():
        queries.append(query.detailed_query.strip())
    queries = [item for item in queries if item]
    if not queries:
        return []

    fused: dict[str, float] = defaultdict(float)
    sources: dict[str, RetrievedSource] = {}
    fetch = max(top_k * 2, 24)
    for text in queries:
        results = search_index(
            text,
            index_dir=directory,
            top_k=fetch,
            embedder=embedder,
            ensure_source_diversity=source_types is None,
            source_types=source_types,
        )
        for rank, source in enumerate(results):
            fused[source.source_id] += 1.0 / (rrf_k + rank + 1)
            if source.source_id not in sources or source.similarity_score > sources[source.source_id].similarity_score:
                sources[source.source_id] = source

    if not fused:
        return []
    best = max(fused.values()) or 1.0
    candidates: list[RetrievedCase] = []
    for parent_id, score in fused.items():
        source = sources[parent_id]
        doc = docs.get(parent_id)
        normalized = score / best
        if doc is not None:
            boost, drop = _metadata_boost(query.filters, doc.metadata)
            if drop:
                continue
            case = doc.to_retrieved_case(
                similarity=max(0.0, min(1.0, normalized + boost)),
                semantic=source.semantic_score,
                lexical=source.lexical_score,
                excerpt=source.excerpt[:2000],
            )
        else:
            case = RetrievedCase(
                case_id=source.case_number or source.chart_number or parent_id,
                source_type=source.source_type,
                chart_number=source.chart_number,
                decision_ratio=source.decision_ratio,
                source_file=source.source_file,
                source_pages=list(range(source.page_start or source.page_number, (source.page_end or source.page_number) + 1)),
                similarity=max(0.0, min(1.0, normalized)),
                semantic_score=source.semantic_score,
                lexical_score=source.lexical_score,
                excerpt=source.excerpt[:2000],
            )
        candidates.append(case)

    candidates.sort(key=lambda item: item.similarity, reverse=True)
    deduped: list[RetrievedCase] = []
    seen: set[str] = set()
    for case in candidates:
        if case.case_id in seen:
            continue
        seen.add(case.case_id)
        deduped.append(case)
    return deduped[:top_k]
