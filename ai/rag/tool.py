"""Similar Case RAG Tool (가이드 4절: RAG는 Agent가 아니라 Master Agent가 호출하는 Tool).

검색 → reranking → 결과 반환의 함수형 역할만 담당한다.

검색 순서 (tiered):
  1) 심의사례 PDF(deliberation_case)에서 먼저 검색하고 사고 구조 요소별로 검증한다.
  2) 검증을 통과한 심의사례가 없을 때만 과실비율 인정기준·회전교차로 비정형기준(fault_standard 계열)에서 검색한다.
최종 유사사례는 `RagSettings.final_top_k`(기본 3)개까지만 반환한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from models.clients import TextClient
from rag.case_documents import CaseDocument, load_case_documents
from rag.pdf_index import DEFAULT_INDEX_DIR, build_index
from rag.query_builder import build_rag_query
from rag.reranker import rerank_cases
from rag.retriever import DELIBERATION_SOURCES, STANDARD_SOURCES, retrieve_candidates
from settings import RagSettings, get_settings
from state.case_state import CaseState, RagQuery, RagResult, RetrievedCase
from telemetry import RunLogger, get_run_logger


class SimilarCaseRagTool:
    def __init__(
        self,
        client: Optional[TextClient] = None,
        *,
        settings: Optional[RagSettings] = None,
        index_dir: Optional[str | Path] = None,
        embedder: Optional[Callable[[list[str], str], np.ndarray]] = None,
        case_docs: Optional[dict[str, CaseDocument]] = None,
        run_logger: Optional[RunLogger] = None,
        auto_build_index: bool = True,
    ):
        self.client = client
        self.settings = settings or get_settings().rag
        self.index_dir = Path(index_dir) if index_dir else Path(self.settings.index_dir or DEFAULT_INDEX_DIR)
        self.embedder = embedder
        self._case_docs = case_docs
        self.run_logger = run_logger or get_run_logger()
        self.auto_build_index = auto_build_index

    def ensure_index(self, progress=None) -> None:
        # 이미 만들어진 인덱스는 재사용하고(가이드 115절 캐싱), 없을 때만 PDF에서 새로 만든다.
        manifest = self.index_dir / "manifest.json"
        if self.auto_build_index and not manifest.is_file():
            build_index(index_dir=self.index_dir, embedder=self.embedder, progress=progress)
        if not manifest.is_file():
            raise RuntimeError(f"RAG 인덱스가 없습니다: {self.index_dir} (python build_rag_index.py 실행 필요)")
        if self._case_docs is None:
            self._case_docs = load_case_documents(self.index_dir)

    @property
    def case_docs(self) -> dict[str, CaseDocument]:
        if self._case_docs is None:
            self._case_docs = load_case_documents(self.index_dir)
        return self._case_docs

    def build_query(self, state: CaseState) -> RagQuery:
        return build_rag_query(self.client, state, run_logger=self.run_logger)

    # ------------------------------------------------------------------ tiers
    def _retrieve(self, query: RagQuery, source_types: Optional[set[str]]) -> list[RetrievedCase]:
        return retrieve_candidates(
            query,
            index_dir=self.index_dir,
            case_docs=self.case_docs,
            top_k=self.settings.candidate_top_k,
            embedder=self.embedder,
            source_types=source_types,
        )

    def _is_acceptable(self, case: RetrievedCase) -> bool:
        """심의사례가 '있다'고 볼 기준: reranker 검증 relevance 또는 (LLM 없을 때) 임베딩 유사도."""
        if case.relevance is not None:
            return case.relevance.usable_as_primary_reference or case.relevance.relevance >= self.settings.min_case_relevance
        if case.semantic_score is None:
            return True
        return case.semantic_score >= self.settings.min_semantic_score

    def search(self, state: CaseState, *, query: Optional[RagQuery] = None, progress=None) -> RagResult:
        self.ensure_index(progress=progress)
        rag_query = query or self.build_query(state)
        top_k = self.settings.final_top_k
        if progress:
            progress(f"유사 심의사례 검색: {rag_query.structured_query[:80]}")

        tier = "deliberation_case"
        fallback_reason: Optional[str] = None
        candidates: list[RetrievedCase] = []
        cases: list[RetrievedCase] = []

        if self.settings.deliberation_first:
            candidates = self._retrieve(rag_query, DELIBERATION_SOURCES)
            ranked = rerank_cases(self.client, state, candidates, top_k=top_k, run_logger=self.run_logger) if candidates else []
            cases = [item for item in ranked if self._is_acceptable(item)][:top_k]
            if not cases:
                fallback_reason = (
                    "심의사례에서 검색된 후보가 없음" if not candidates else "심의사례 후보가 사고 구조 검증 기준을 넘지 못함"
                )
                if progress:
                    progress(f"{fallback_reason} → 과실비율 인정기준에서 검색")
                tier = "fault_standard"
                standard_candidates = self._retrieve(rag_query, STANDARD_SOURCES)
                candidates = candidates + standard_candidates
                ranked = rerank_cases(self.client, state, standard_candidates, top_k=top_k, run_logger=self.run_logger, max_candidates=12) if standard_candidates else []
                # 도표도 같은 검증을 통과해야 기준값이 된다. 회전교차로 표를 신호교차로 사건에 붙이는 일을 막는다
                cases = [item for item in ranked if self._is_acceptable(item)][:top_k]
                if not cases:
                    tier = "none"
                    fallback_reason += " / " + ("인정기준 도표에서도 검색된 후보가 없음" if not standard_candidates else "인정기준 도표도 사고 구조 검증 기준을 넘지 못함")
                    if progress:
                        progress("참고할 인정기준 도표도 없음 → 기준 없이 임시 판정")
        else:
            candidates = self._retrieve(rag_query, None)
            cases = rerank_cases(self.client, state, candidates, top_k=top_k, run_logger=self.run_logger)[:top_k]
            tier = "deliberation_case" if any(item.source_type == "deliberation_case" for item in cases) else "fault_standard"

        if not cases:
            tier = "none"
        embedding_model = ""
        manifest = self.index_dir / "manifest.json"
        if manifest.is_file():
            embedding_model = json.loads(manifest.read_text(encoding="utf-8")).get("embedding_model", "")
        self.run_logger.log(
            agent="rag_tool",
            task="similar_case_search",
            case_id=state.case_id,
            model=embedding_model,
            extra={
                "retrieval_query": rag_query.structured_query,
                "tier": tier,
                "fallback_reason": fallback_reason,
                "retrieved_case_ids": [item.case_id for item in candidates],
                "selected_case_ids": [item.case_id for item in cases],
                "rerank_scores": {item.case_id: (item.relevance.relevance if item.relevance else None) for item in cases},
            },
        )
        return RagResult(
            query=rag_query,
            candidates=candidates,
            cases=cases,
            embedding_model=embedding_model,
            tier=tier,  # type: ignore[arg-type]
            fallback_reason=fallback_reason,
        )
