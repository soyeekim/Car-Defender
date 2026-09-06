import pytest

from agents.document_agent import DocumentAgent
from agents.master_agent import MasterAccidentAgent
from agents.video_agent import VideoAnalysisAgent
from fakes import FakeRagTool, FakeTextClient, FakeVideoAnalyzer, quiet_logger
from service.interface import CarDefenderAI, CaseStore
from state.case_state import CaseState
from video.cache import VideoResultCache


def _service(tmp_path) -> CarDefenderAI:
    logger = quiet_logger(tmp_path)
    client = FakeTextClient()
    agent = MasterAccidentAgent(
        text_client=client,
        video_agent=VideoAnalysisAgent(analyzer=FakeVideoAnalyzer(run_logger=logger), cache=VideoResultCache(tmp_path / "c", enabled=False), run_logger=logger),
        rag_tool=FakeRagTool(client, run_logger=logger),
        document_agent=DocumentAgent(client=client, run_logger=logger),
        run_logger=logger,
    )
    return CarDefenderAI(agent=agent, store=CaseStore(tmp_path / "cases"))


def test_case_store_roundtrip(tmp_path):
    store = CaseStore(tmp_path / "cases")
    state = CaseState(case_id="case-001", initial_description="테스트")
    path = store.save(state)
    assert path.is_file()
    fresh = CaseStore(tmp_path / "cases")
    loaded = fresh.load("case-001")
    assert loaded.initial_description == "테스트"
    assert fresh.list_cases() == ["case-001"]
    with pytest.raises(KeyError):
        fresh.load("missing")


def test_service_contract_end_to_end(tmp_path):
    service = _service(tmp_path)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    response = service.create_case(str(video), "사거리에서 직진하다가 사고났어.")
    case_id = response.case_id
    assert response.action == "ASK_USER"
    assert response.stage == "FACT_COLLECTING"

    response = service.chat(case_id, "내 차 블랙박스야")
    assert response.action == "ASK_USER"
    for _ in range(6):
        if response.action == "SHOW_FAULT_ASSESSMENT":
            break
        if response.data.get("offer_assessment"):
            response = service.chat(case_id, "예상 과실비율 판정해줘")
        elif response.data.get("open_question"):
            response = service.chat(case_id, "없어요")
        else:
            response = service.chat(case_id, "상대는 깜빡이 안 켰어. 내가 먼저 들어가 있었어.")
    assert response.action == "SHOW_FAULT_ASSESSMENT", response.message
    assert response.data["fault_assessment"]["fault_ratio"] == {"user": 30, "opponent": 70}

    assessment = service.assess_fault(case_id)
    assert assessment.fault_ratio.as_text() == "30:70"

    service.chat(case_id, "사고는 2026년 8월 22일이었어")
    report = service.generate_incident_report(case_id)
    assert report.document_type == "incident_report"
    rebuttal = service.generate_rebuttal(case_id, opponent_claim="상대 보험사는 50:50을 주장")
    assert rebuttal.document_type == "rebuttal_opinion"
    assert "50:50" in rebuttal.sections["opponent_claim"] or "상대방은" in rebuttal.sections["opponent_claim"]

    state = service.get_state(case_id)
    assert state.current_stage == "REBUTTAL_COMPLETE"
    assert state.opponent_claim == "상대 보험사는 50:50을 주장"
    # 저장된 상태를 새 store로 다시 읽어도 복원된다
    reloaded = CaseStore(tmp_path / "cases").load(case_id)
    assert reloaded.fault_assessment.fault_ratio.user == 30
    assert reloaded.video_analysis.collision_pair.participants == ["vehicle_1", "vehicle_3"]


def test_document_requires_assessment(tmp_path):
    service = _service(tmp_path)
    response = service.create_case(None, "주차장에서 사고")
    with pytest.raises(RuntimeError):
        service.generate_incident_report(response.case_id)
