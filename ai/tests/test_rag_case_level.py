from pathlib import Path

import numpy as np

from fakes import FakeTextClient, quiet_logger, sample_cases
from rag.case_documents import build_case_documents, classify_metadata, load_case_documents, parse_case_parent
from rag.pdf_index import build_index, create_parent_documents
from rag.query_builder import deterministic_query
from rag.reranker import rerank_cases
from rag.retriever import retrieve_candidates
from state.case_state import CaseMetadata, CaseState, RagQuery
from state.updater import set_slot

SAMPLE_CASE_TEXT = """[PDF 24쪽]
목차보기
자동차사고 과실비율분쟁 심의 사례 │ 제1장 자동차와 자동차의 사고 026

차대차 직진 대 직진 사고 - 사거리 교차로(상대 차량이 측면 방향에서 진입)
참고기준
양 차량 신호위반 사고
(황색 대 적색) (기본과실)
신호등 있음 사거리 황색 직진 적색 직진
203

1. 자동차와 자동차의 사고
사례 개요
심의번호 2019-034537 결정비율 A(청구) : B(피청구) = 30 : 70
• 신호기 있는 사거리 교차로에서 청구차량이 황색신호에 직진하던 중 좌측도로에서 적색신호
사고내용
에 직진하는 피청구차량과 충돌한 사고임

양 차량 모두 신호위반에 해당하지만, 적색신호에 진입
참고 한 B차량의 과실이 더 중하므로 양차량의 기본과실을
인정기준 30:70으로 정한다.
203
기본비율 A : B = 30 : 70

주장 내용
청구인 피청구인
• 청구차량은 황색신호에 직진하였고, 피청구차량은 적색 • 청구차량이 황색신호에 직진하였는지 불분명하고

[PDF 25쪽]
입증 자료
• 교통사고사실확인원상 피청구차량이 적색신호에 직진하였다고 기재되어 있음

주요 쟁점
● 청구차량이 황색신호에 직진하였는지 여부
● 황색신호에서 직진하던 차량과 적색신호에 직진하던 차량의 과실비율

결정 근거
● 청구차량 앞부분, 피청구차량 조수석 측면부 파손 됨

결정 이유
● 신호기 있는 사거리 교차로에서 청구차량은 황색신호에 직진을, 피청구차량은 좌측 도로에서 적색신호에 직진을 하다가 발생한 사고임
● 과실비율 인정기준 203도표의 기본과실비율을 적용함이 타당함
● 청구차량 30% ● 피청구차량 70%
"""


def _parent(text=SAMPLE_CASE_TEXT, **overrides):
    parent = {
        "parent_id": "parent_00003",
        "unit_key": "deliberation_case:2019-034537",
        "unit_type": "case",
        "source_type": "deliberation_case",
        "source_file": "cases.pdf",
        "page_start": 24,
        "page_end": 25,
        "case_number": "2019-034537",
        "chart_number": "203",
        "decision_ratio": "30:70",
        "full_text": text,
    }
    parent.update(overrides)
    return parent


def test_parse_case_parent_extracts_case_level_fields():
    doc = parse_case_parent(_parent())
    assert doc.case_id == "2019-034537"
    assert doc.title.startswith("차대차 직진 대 직진 사고 - 사거리 교차로")
    assert doc.decision_ratio == "30:70"
    assert doc.basic_ratio == "30:70"
    assert "적색신호" in doc.accident_description
    assert any("황색신호" in issue for issue in doc.key_issues)
    assert any("203도표" in reason for reason in doc.decision_reasons)
    assert doc.metadata.road_type == "intersection"
    assert doc.metadata.intersection_type == "four_way"
    assert doc.metadata.signal_present is True
    assert doc.metadata.movement_a == "직진" and doc.metadata.movement_b == "직진"
    assert doc.metadata.accident_target == "차대차"
    assert "목차보기" not in doc.combined_text
    retrieved = doc.to_retrieved_case(similarity=0.8)
    assert retrieved.source_pages == [24, 25]
    assert retrieved.ratio_summary() == "기본 30:70, 결정 30:70"


def test_classify_metadata_roundabout_and_lane_change():
    meta = classify_metadata("차대차 진로변경 사고 - 회전교차로", "회전교차로 내 회전 차량과 진입 차량")
    assert meta.road_type == "roundabout"
    assert meta.lane_change is True


def test_build_and_load_case_documents_from_parents(tmp_path):
    parents = [_parent(), _parent(parent_id="parent_00010", unit_key="fault_standard:203", unit_type="standard_chart", source_type="fault_standard", case_number=None, full_text="203 신호등 있는 사거리 황색 직진 적색 직진\n기본과실비율 A 30 B 70\n수정요소 현저한 과실 +10")]
    docs = build_case_documents(tmp_path, parents=parents)
    assert {doc.case_id for doc in docs} == {"2019-034537", "203"}
    standard = next(doc for doc in docs if doc.unit_type == "standard_chart")
    assert standard.basic_ratio == "30:70"
    assert standard.modification_factors
    loaded = load_case_documents(tmp_path)
    assert set(loaded) == {"parent_00003", "parent_00010"}


def _embedder(texts, _model):
    # '회전교차로' 안의 '교차로'가 사거리 축에 잡히지 않도록 축별 키워드를 분리한다
    vectors = []
    for text in texts:
        vectors.append([text.count("사거리"), text.count("회전교차로"), text.count("보행자") + text.count("횡단보도")])
    return np.asarray(vectors, dtype=np.float32)


def test_retriever_fuses_queries_and_applies_metadata(tmp_path):
    pdf = tmp_path / "cases.pdf"
    pdf.write_bytes(b"placeholder")
    pages = [
        "심의번호 2025-000001 결정비율 A(청구) : B(피청구) = 30 : 70\n차대차 직진 대 직진 사고 - 사거리 교차로\n사고내용\n신호기 있는 사거리 교차로 직진 대 직진 충돌",
        "심의번호 2025-000002 결정비율 A(청구) : B(피청구) = 20 : 80\n차대차 진입 대 회전 사고 - 회전교차로\n사고내용\n회전교차로 회전 차량과 진입 차량 충돌",
        "심의번호 2025-000003 결정비율 A(청구) : B(피청구) = 0 : 100\n차대보행자 사고 - 횡단보도\n사고내용\n횡단보도 보행자 충돌",
    ]
    index_dir = tmp_path / "index"
    build_index(pdf_paths=[pdf], index_dir=index_dir, embedder=_embedder, page_extractor=lambda _p: pages, min_chars=10)
    docs = load_case_documents(index_dir)
    query = RagQuery(structured_query="사거리 교차로 직진 대 직진", detailed_query="신호기 있는 사거리 교차로에서 직진 차량끼리 충돌", filters=CaseMetadata(accident_target="차대차", road_type="intersection"))
    results = retrieve_candidates(query, index_dir=index_dir, case_docs=docs, top_k=5, embedder=_embedder)
    assert results[0].case_id == "2025-000001"
    assert all(item.case_id != "2025-000003" for item in results)  # 차대보행자 hard filter
    assert results[0].decision_ratio == "30:70"
    assert results[0].metadata.intersection_type == "four_way"


DELIBERATION_PDF = "(최종)과실비율심의사례_(54MB).pdf"
STANDARD_PDF = "230630_자동차사고 과실비율 인정기준_최종.pdf"


def _tiered_index(tmp_path):
    """심의사례 PDF(사거리 2건 + 보행자 1건)와 인정기준 PDF(회전교차로 도표 1건)로 인덱스를 만든다."""
    cases_pdf = tmp_path / DELIBERATION_PDF
    standard_pdf = tmp_path / STANDARD_PDF
    cases_pdf.write_bytes(b"placeholder")
    standard_pdf.write_bytes(b"placeholder")
    # 실제 PDF 이름을 쓰면 pdf_index가 앞부분 목차 페이지를 건너뛰므로(심의사례 18쪽, 인정기준 10쪽부터) 빈 페이지로 채운다.
    pages = {
        DELIBERATION_PDF: [""] * 17 + [
            "심의번호 2025-000001 결정비율 A(청구) : B(피청구) = 30 : 70\n차대차 직진 대 직진 사고 - 사거리 교차로\n사고내용\n신호기 있는 사거리 교차로 직진 대 직진 충돌",
            "심의번호 2025-000002 결정비율 A(청구) : B(피청구) = 40 : 60\n차대차 직진 대 좌회전 사고 - 사거리 교차로\n사고내용\n사거리 교차로 직진 차량과 좌회전 차량 충돌",
            "심의번호 2025-000003 결정비율 A(청구) : B(피청구) = 0 : 100\n차대보행자 사고 - 횡단보도\n사고내용\n횡단보도 보행자 충돌",
        ],
        STANDARD_PDF: [""] * 9 + [
            "차1-1 회전교차로 진입 차량과 회전 차량 사고\n기본과실비율 A 80 B 20\n회전교차로 회전 차량 우선 수정요소 감산 -10",
        ],
    }
    index_dir = tmp_path / "index"
    build_index(
        pdf_paths=[cases_pdf, standard_pdf],
        index_dir=index_dir,
        embedder=_embedder,
        page_extractor=lambda path: pages[Path(path).name],
        min_chars=10,
    )
    return index_dir


def _tool(index_dir, client=None, *, run_logger=None, **overrides):
    from rag.tool import SimilarCaseRagTool
    from settings import RagSettings

    settings = RagSettings(index_dir=index_dir, final_top_k=3, candidate_top_k=10, min_semantic_score=0.3, **overrides)
    return SimilarCaseRagTool(client, settings=settings, index_dir=index_dir, embedder=_embedder, run_logger=run_logger, auto_build_index=False)


def test_tool_searches_deliberation_cases_first_and_limits_to_three(tmp_path):
    index_dir = _tiered_index(tmp_path)
    tool = _tool(index_dir, run_logger=quiet_logger(tmp_path))
    result = tool.search(CaseState(), query=RagQuery(structured_query="사거리 교차로 직진 대 직진", detailed_query="신호기 있는 사거리 교차로 직진 충돌"))
    assert result.tier == "deliberation_case"
    assert result.fallback_reason is None
    assert 1 <= len(result.cases) <= 3
    assert all(item.source_type == "deliberation_case" for item in result.cases)
    assert result.cases[0].case_id == "2025-000001"
    assert all(item.case_id != "차1-1" for item in result.cases)


def test_tool_falls_back_to_standards_when_no_deliberation_case_matches(tmp_path):
    index_dir = _tiered_index(tmp_path)
    tool = _tool(index_dir, run_logger=quiet_logger(tmp_path))
    result = tool.search(CaseState(), query=RagQuery(structured_query="회전교차로 회전 차량 진입 차량", detailed_query="회전교차로에서 회전 중인 차량과 진입 차량 충돌"))
    assert result.tier == "fault_standard"
    assert result.fallback_reason
    assert [item.case_id for item in result.cases] == ["차1-1"]
    assert result.cases[0].basic_ratio == "80:20"


def test_tool_uses_reranker_relevance_as_acceptance_when_llm_available(tmp_path):
    index_dir = _tiered_index(tmp_path)

    class LowRelevanceClient(FakeTextClient):
        def _master_case_validation(self, user):
            validations = super()._master_case_validation(user)["validations"]
            for item in validations:
                item["relevance"] = 0.2
                item["usable_as_primary_reference"] = False
            return {"validations": validations}

    tool = _tool(index_dir, LowRelevanceClient(), run_logger=quiet_logger(tmp_path), min_case_relevance=0.5)
    result = tool.search(CaseState(), query=RagQuery(structured_query="사거리 교차로 직진 대 직진", detailed_query="사거리 직진"))
    # 심의사례도, 인정기준 도표도 검증을 넘지 못하면 엉뚱한 표를 기준값으로 잡지 않고 '참고 기준 없음'으로 끝낸다
    assert result.tier == "none"
    assert result.cases == []
    assert "검증 기준" in result.fallback_reason and "도표" in result.fallback_reason

    tool_ok = _tool(index_dir, FakeTextClient(), run_logger=quiet_logger(tmp_path))
    result_ok = tool_ok.search(CaseState(), query=RagQuery(structured_query="사거리 교차로 직진 대 직진", detailed_query="사거리 직진"))
    assert result_ok.tier == "deliberation_case"
    assert len(result_ok.cases) <= 3


def test_reranker_ignores_invented_case_ids_and_keeps_deliberation_case(tmp_path):
    client = FakeTextClient()
    state = CaseState()
    ranked = rerank_cases(client, state, sample_cases(), top_k=2, run_logger=quiet_logger(tmp_path))
    ids = [item.case_id for item in ranked]
    assert "9999-999999" not in ids
    assert ranked[0].case_id == "2018-070162"
    assert ranked[0].relevance is not None and ranked[0].relevance.usable_as_primary_reference
    assert any(item.source_type == "deliberation_case" for item in ranked)


def test_reranker_falls_back_to_similarity_when_llm_fails(tmp_path):
    client = FakeTextClient(fail_tasks={"master_case_validation"})
    ranked = rerank_cases(client, CaseState(), sample_cases(), top_k=3, run_logger=quiet_logger(tmp_path))
    assert [item.case_id for item in ranked] == ["2018-070162", "2019-034537", "203"]


def test_deterministic_query_uses_objective_structure_only():
    state = CaseState()
    set_slot(state, "road.road_type", "intersection", source="video")
    set_slot(state, "road.intersection_type", "four_way", source="video")
    set_slot(state, "road.signal_present", "true", source="video")
    set_slot(state, "ego_vehicle.movement", "straight", source="video")
    set_slot(state, "other_vehicle.movement", "straight", source="video")
    set_slot(state, "other_vehicle.entry_direction", "right_side_road", source="video")
    set_slot(state, "collision.ego_collision_part", "front", source="video")
    set_slot(state, "collision.other_collision_part", "left_side", source="video")
    query = deterministic_query(state)
    assert "사거리 교차로" in query.structured_query
    assert "직진 대 직진" in query.structured_query
    assert "우측 도로에서 진입" in query.structured_query
    assert query.filters.signal_present is True
    assert query.filters.intersection_type == "four_way"
