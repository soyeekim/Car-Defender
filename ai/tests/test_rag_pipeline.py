from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from rag.pdf_index import (
    build_index,
    create_chunks,
    create_parent_documents,
    load_index,
    load_parent_documents,
    search_index,
)
from rag.pipeline import _assess_sources, generate_incident_report, run_post_intake_pipeline
from schemas.accident_state import AccidentState, AccidentType
from schemas.evidence_state import EvidenceFusionResult, FinalFact
from schemas.fact_plan import AccidentHypothesis, FactPlan
from schemas.post_intake import IncidentReport, RetrievedSource
from schemas.video_analysis import VideoAnalysisResult
from storage.evidence_store import EvidenceSessionStore


def semantic_embedder(texts, _model):
    vectors = []
    for text in texts:
        vectors.append(
            [
                text.count("회전교차로") + text.count("회전 차량"),
                text.count("보행자") + text.count("횡단보도"),
                text.count("후방추돌") + text.count("뒤 차량"),
            ]
        )
    return np.asarray(vectors, dtype=np.float32)


def test_case_parent_spans_pages_and_stops_at_next_case(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"placeholder")
    parents = create_parent_documents(
        [pdf],
        page_extractor=lambda _path: [
            "심의번호 2025-001234 결정비율 A : B = 20 : 80\n202(가)\n회전교차로 진입 사고",
            "주요 쟁점\n회전 차량과 진입 차량의 우선순위",
            "심의번호 2025-005678 결정비율 A : B = 40 : 60\n보행자 사고",
        ],
    )

    assert len(parents) == 2
    assert parents[0]["page_start"] == 1
    assert parents[0]["page_end"] == 2
    assert parents[0]["case_number"] == "2025-001234"
    assert parents[0]["chart_number"] == "202(가)"
    assert parents[0]["decision_ratio"] == "20:80"
    assert "주요 쟁점" in parents[0]["full_text"]
    assert parents[1]["page_start"] == parents[1]["page_end"] == 3


def test_embedding_index_returns_semantically_related_parent(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"placeholder")
    pages = [
        "회전교차로 회전 차량과 진입 차량이 차로를 변경하며 측면 충돌한 사고",
        "횡단보도에서 보행자가 도로를 건너던 중 발생한 사고",
        "직선도로에서 뒤 차량이 앞 차량을 충격한 후방추돌 사고",
    ]
    index_dir = tmp_path / "index"

    build_index(
        pdf_paths=[pdf],
        index_dir=index_dir,
        embedder=semantic_embedder,
        page_extractor=lambda _path: pages,
        min_chars=10,
    )
    results = search_index(
        "2차로형 회전교차로에서 회전 차량과 진입 차량 충돌",
        index_dir=index_dir,
        top_k=1,
        embedder=semantic_embedder,
    )

    assert results[0].page_number == 1
    assert results[0].page_start == results[0].page_end == 1
    assert results[0].parent_id
    assert results[0].matched_child_ids
    chunks, embeddings, manifest = load_index(index_dir)
    assert len(chunks) == embeddings.shape[0] == 3
    assert manifest["chunk_count"] == 3
    assert manifest["index_strategy"] == "parent_child_hybrid"
    assert len(load_parent_documents(index_dir)) == 3


def test_search_returns_the_full_multi_page_case_parent(tmp_path):
    pdf = tmp_path / "cases.pdf"
    pdf.write_bytes(b"placeholder")
    pages = [
        "심의번호 2025-001234 결정비율 A : B = 20 : 80\n사고 내용\n회전교차로 진입 차량 측면 충돌",
        "결정 이유\n회전 차량을 확인하지 않은 진입 차량의 주의의무 위반",
        "심의번호 2025-005678 결정비율 A : B = 0 : 100\n횡단보도 보행자 사고",
    ]
    index_dir = tmp_path / "index"
    build_index(
        pdf_paths=[pdf],
        index_dir=index_dir,
        embedder=semantic_embedder,
        page_extractor=lambda _path: pages,
        min_chars=10,
    )

    result = search_index(
        "회전교차로 회전 차량 진입 차량",
        index_dir=index_dir,
        top_k=1,
        embedder=semantic_embedder,
    )[0]

    assert result.case_number == "2025-001234"
    assert (result.page_start, result.page_end) == (1, 2)
    assert "회전교차로 진입 차량 측면 충돌" in result.excerpt
    assert "진입 차량의 주의의무 위반" in result.excerpt


def test_reranker_cannot_invent_a_source_id():
    report = IncidentReport(
        title="회전교차로 사고",
        objective_narrative="회전 중 측면 충돌",
        retrieval_query="회전교차로 측면 충돌",
    )
    source = RetrievedSource(
        source_id="src_00001",
        source_type="deliberation_case",
        source_file="cases.pdf",
        page_number=20,
        chunk_index=0,
        similarity_score=0.91,
        excerpt="회전교차로 사고 심의사례",
    )
    model_output = {
        "selected_sources": [
            {
                "source_id": "invented_case",
                "relevance_reason": "존재하지 않는 사례",
            },
            {
                "source_id": "src_00001",
                "relevance_reason": "도로 구조와 충돌 형태가 유사함",
                "matching_factors": ["회전교차로"],
                "differing_factors": [],
                "applicability_caution": "차로 위치 확인 필요",
            },
        ]
    }

    with patch("rag.pipeline.call_json", return_value=model_output):
        assessments = _assess_sources(report, [source])

    assert [item.source.source_id for item in assessments] == ["src_00001"]


def test_reranker_keeps_a_deliberation_case_and_a_standard():
    report = IncidentReport(
        title="회전교차로 사고",
        objective_narrative="측면 충돌",
        retrieval_query="회전교차로 측면 충돌",
    )
    candidates = [
        RetrievedSource(
            source_id="standard_1",
            source_type="roundabout_special_standard",
            source_file="standard.pdf",
            page_number=10,
            chunk_index=0,
            similarity_score=0.9,
            excerpt="회전교차로 기준",
        ),
        RetrievedSource(
            source_id="case_1",
            source_type="deliberation_case",
            source_file="cases.pdf",
            page_number=20,
            chunk_index=0,
            similarity_score=0.8,
            excerpt="회전교차로 심의사례",
        ),
    ]
    output = {
        "selected_sources": [
            {
                "source_id": "standard_1",
                "relevance_reason": "기준이 유사함",
                "applicability_caution": "세부 확인 필요",
            }
        ]
    }

    with patch("rag.pipeline.call_json", return_value=output):
        assessments = _assess_sources(report, candidates)

    assert {item.source.source_type for item in assessments} == {
        "roundabout_special_standard",
        "deliberation_case",
    }


def test_incident_report_falls_back_without_losing_evidence():
    fusion = EvidenceFusionResult(
        facts={
            "accident_place": FinalFact(
                slot="accident_place",
                resolved_value="회전교차로",
                status="verified_by_vision",
                selected_source="vision",
                resolution_reason="직접 관찰",
            ),
            "turn_signal": FinalFact(
                slot="turn_signal",
                status="vision_inferred",
                resolution_reason="영상 추론뿐임",
            ),
        }
    )
    state = AccidentState(accident_type=AccidentType(family="roundabout_collision"))
    state.accident_place.value = "회전교차로"
    agent = SimpleNamespace(
        fusion=fusion,
        fact_plan=FactPlan(
            plan_summary="회전 경로 확인",
            accident_hypotheses=[
                AccidentHypothesis(
                    family="roundabout_collision",
                    confidence=0.8,
                    reason="회전교차로 직접 관찰",
                )
            ],
        ),
        video_analysis=VideoAnalysisResult(summary="회전교차로에서 측면 접촉"),
        state=state,
        history=[],
    )

    with patch("rag.pipeline.call_json", side_effect=RuntimeError("API unavailable")):
        report = generate_incident_report(agent)

    assert report.generation_method == "deterministic_fallback"
    assert any("회전교차로" in item for item in report.confirmed_facts)
    assert any("방향지시등" in item for item in report.unknown_facts)
    assert "회전교차로에서 측면 접촉" not in report.objective_narrative


def test_llm_report_narrative_is_replaced_by_grounded_evidence():
    fusion = EvidenceFusionResult(
        facts={
            "ego_signal": FinalFact(
                slot="ego_signal",
                resolved_value="녹색",
                status="verified_by_vision",
                selected_source="vision",
                resolution_reason="직접 관찰",
            )
        }
    )
    agent = SimpleNamespace(
        fusion=fusion,
        fact_plan=FactPlan(plan_summary="신호 확인"),
        video_analysis=VideoAnalysisResult(summary="상대 신호는 적색으로 추정"),
        state=AccidentState(),
        history=[],
    )
    hallucinated = {
        "title": "신호 사고",
        "objective_narrative": "상대 차량이 적색 신호를 위반했다.",
        "retrieval_query": "상대 적색 신호 위반",
    }

    with patch("rag.pipeline.call_json", return_value=hallucinated):
        report = generate_incident_report(agent)

    assert "상대 차량이 적색 신호를 위반" not in report.objective_narrative
    assert len(report.confirmed_facts) == 1
    assert "신호등 색" in report.confirmed_facts[0]
    assert report.confirmed_facts[0] in report.retrieval_query
    assert report.retrieval_factors["traffic_control"]


def test_post_intake_pipeline_refuses_incomplete_intake():
    agent = SimpleNamespace(_is_complete=lambda: False)

    with pytest.raises(ValueError, match="Intake가 완료"):
        run_post_intake_pipeline(agent)


def test_store_writes_incident_report_and_rag_separately(tmp_path):
    store = EvidenceSessionStore(base_dir=tmp_path, session_id="rag_store")
    report = IncidentReport(
        title="테스트 사고",
        objective_narrative="테스트 경위",
        retrieval_query="테스트 검색",
    )
    rag = {
        "retrieval_query": "테스트 검색",
        "embedding_model": "fake-embedding",
        "candidates": [],
        "similar_cases": [],
    }

    report_path = store.save_incident_report(report)
    rag_path = store.save_rag_results(rag)

    assert report_path.is_file()
    assert rag_path.is_file()
    assert "테스트 경위" in report_path.read_text(encoding="utf-8")
    assert "fake-embedding" in rag_path.read_text(encoding="utf-8")
