"""백엔드 Agent 계약(server/docs/agent-interface.md) 어댑터 검증 — 네트워크 없이 Fake 구성요소로.

흐름: analyze(영상+설명) → chat(질문 답변…) → next_action=verdict → judge → chat(경위서) → create_report → write(report)
      → write(rebuttal) → 새 사실 → rejudge(change_reason) → 문서 다시 쓰기. facts 는 항상 JSON 직렬화 가능해야 한다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.codec import META_KEY, STATE_KEY, pack_facts, unpack_state
from agent.presenters import build_case_title, parse_opponent_claim, question_card
from agent.real import RealAgent, prepare_video_path
from agents.document_agent import DocumentAgent
from agents.master_agent import MasterAccidentAgent
from agents.video_agent import VideoAnalysisAgent
from fakes import FakeRagTool, FakeTextClient, FakeVideoAnalyzer, quiet_logger
from state.case_state import CaseState, Question
from video.cache import VideoResultCache

ANSWERS = {
    "video_source.vehicle_owner": "내 차 블랙박스야",
    "other_vehicle.turn_signal": "상대는 깜빡이 안 켰어.",
    "ego_vehicle.entered_first": "내가 먼저 들어가 있었어.",
    "review.additional_facts": "없어요",
    "accident_datetime.date": "2026년 8월 22일 사고였어.",
}


def Turn(role, text):  # noqa: N802  백엔드 ChatTurn 흉내
    return SimpleNamespace(role=role, text=text)


def Verdict(version, mine, other, summary="", basis=None, opponent_claim=None):  # noqa: N802
    return SimpleNamespace(version=version, ratio_mine=mine, ratio_other=other, summary=summary, basis=basis or {}, opponent_claim=opponent_claim)


def Section(index, title, body):  # noqa: N802
    return SimpleNamespace(index=index, title=title, body=body)


def build_real_agent(tmp_path, *, text_client=None, video_kwargs=None) -> tuple[RealAgent, FakeVideoAnalyzer, FakeTextClient]:
    logger = quiet_logger(tmp_path)
    client = text_client or FakeTextClient()
    analyzer = FakeVideoAnalyzer(run_logger=logger, **(video_kwargs or {}))
    master = MasterAccidentAgent(
        text_client=client,
        video_agent=VideoAnalysisAgent(analyzer=analyzer, cache=VideoResultCache(tmp_path / "cache", enabled=False), run_logger=logger, factor_sweep=True),
        rag_tool=FakeRagTool(client, run_logger=logger),
        document_agent=DocumentAgent(client=client, run_logger=logger),
        run_logger=logger,
    )
    from agent.cache import JudgeCache

    return RealAgent(master=master, cache=JudgeCache(tmp_path / "judge")), analyzer, client


def _video(tmp_path):
    video = tmp_path / "blackbox.mp4"
    video.write_bytes(b"fake")
    return str(video)


def _json_safe(value):
    json.dumps(value, ensure_ascii=False)


def _answer_for(state_facts) -> str:
    state = unpack_state(state_facts[STATE_KEY])
    if state.assessment_offer_pending:
        return "예상 과실비율 판정해줘"  # "판정해 드릴까요?" 제안 수락
    field = state.pending_questions[0].field if state.pending_questions else ""
    return ANSWERS.get(field, "모르겠어요")


def _chat(agent, facts, messages, text, *, verdict=None, has_report=False):
    inp = SimpleNamespace(messages=list(messages), new_message=text, facts=dict(facts), questions=[], verdict=verdict, has_video=True, has_report=has_report)
    result = agent.chat(inp)
    facts = {**facts, **result["fact_updates"]}  # 백엔드의 얕은 병합
    messages = messages + [Turn("user", text), Turn("assistant", result["reply"])]
    return result, facts, messages


def _run_until_verdict(agent, tmp_path, description="사거리에서 직진하다가 우측에서 온 차와 부딪혔어."):
    analysis = agent.analyze(SimpleNamespace(video_path=_video(tmp_path), video_mime="video/mp4", description=description))
    facts = dict(analysis["facts"])
    messages = [Turn("user", description), Turn("assistant", analysis["summary_text"])] + [Turn("assistant", q) for q in analysis["questions"]]
    result = None
    for _ in range(10):
        result, facts, messages = _chat(agent, facts, messages, _answer_for(facts))
        if result["next_action"] == "verdict":
            break
    assert result is not None and result["next_action"] == "verdict", (result or {}).get("reply")
    return analysis, result, facts, messages


# --------------------------------------------------------------------------- analyze


def test_analyze_returns_summary_question_title_and_packed_state(tmp_path):
    agent, analyzer, _ = build_real_agent(tmp_path)
    result = agent.analyze(SimpleNamespace(video_path=_video(tmp_path), video_mime="video/mp4", description="사거리에서 직진하다가 사고났어."))
    assert [call["focus"] for call in analyzer.calls] == [None, "agent_gap_fill"]  # 영상 2회, 모두 분석 단계에서
    assert result["summary_text"].strip() and "블랙박스인가요" not in result["summary_text"]  # 질문은 말풍선에서 뺀다
    assert len(result["questions"]) == 1
    assert result["questions"][0].startswith("하나만 물어볼게요.") and "블랙박스인가요" in result["questions"][0]
    assert result["title"].startswith("교차로 직진 측면 충돌 · ")
    assert result["video_meta"] == {"speed_kph": None, "impact_at_sec": 5}
    facts = result["facts"]
    _json_safe(facts)
    assert facts[META_KEY]["impl"] == "ai.agent.real:RealAgent" and facts[META_KEY]["verdict_version"] == 0
    assert facts["key_facts"] and facts["stage"] == "FACT_COLLECTING"
    restored = unpack_state(facts[STATE_KEY])
    assert restored.video_analyzed and restored.pending_questions[0].field == "video_source.vehicle_owner"
    assert restored.conversation_history == []  # 대화는 백엔드가 준다
    assert len(json.dumps(facts, ensure_ascii=False)) < 60_000


def test_analyze_without_questions_goes_straight_to_verdict(tmp_path):
    class NothingToAsk(FakeTextClient):
        def _master_followup_question(self, user):
            return {"reasoning": ["물을 것 없음"], "video_recheck_targets": [], "intro": "", "questions": []}

        def _master_case_review_questions(self, user):
            return {"reasoning": ["검토할 것 없음"], "summary": "", "video_recheck_targets": [], "questions": []}

    agent, _, _ = build_real_agent(tmp_path, text_client=NothingToAsk())
    description = "내 차 블랙박스야. 사거리에서 직진하다가 사고났어."
    result = agent.analyze(SimpleNamespace(video_path=_video(tmp_path), video_mime="video/mp4", description=description))
    # 설명에서 영상 소유 관계가 이미 확인됐고 Agent가 물을 것이 없다고 판단 → 분석 말풍선 + '추가 정황' 열린 질문 카드.
    # 심의사례는 아직 보여주지 않는다 (사용자가 판정을 요청할 때 찾는다)
    assert "심의사례" not in result["summary_text"]
    assert len(result["questions"]) == 1 and "추가로 알려주실" in result["questions"][0]
    facts = result["facts"]
    messages = [Turn("user", description), Turn("assistant", result["summary_text"]), Turn("assistant", result["questions"][0])]
    chat, facts, messages = _chat(agent, facts, messages, "없어요")
    assert chat["next_action"] == "none" and "판정해 드릴까요" in chat["reply"], chat["reply"]
    chat, facts, _ = _chat(agent, facts, messages, "예상 과실비율 판정해줘")
    assert chat["next_action"] == "verdict", chat["reply"]
    assert "비슷한 심의사례를 찾았어요" in chat["reply"] and "2018-070162" in chat["reply"]


def test_prepare_video_path_adds_extension_from_mime(tmp_path, monkeypatch):
    monkeypatch.setenv("CAR_DEFENDER_AGENT_TMP", str(tmp_path / "tmp"))
    raw = tmp_path / "01ABCDEF"
    raw.write_bytes(b"fake")
    prepared = prepare_video_path(str(raw), "video/quicktime")
    assert prepared.endswith(".mov") and (tmp_path / "tmp" / "videos").is_dir()
    assert prepare_video_path(str(tmp_path / "a.mp4"), "video/mp4").endswith("a.mp4")


# --------------------------------------------------------------------------- chat


def test_chat_without_video_asks_for_upload(tmp_path):
    agent, _, _ = build_real_agent(tmp_path)
    result = agent.chat(SimpleNamespace(messages=[], new_message="교차로에서 부딪혔어요", facts={}, questions=[], verdict=None, has_video=False, has_report=False))
    assert "영상을 올려" in result["reply"] and result["next_action"] == "none" and result["fact_updates"] == {}


def _reach_judged(tmp_path):
    """analyze → chat 답변들 → next_action=verdict → judge 까지. (agent, facts, messages, judged)"""
    agent, analyzer, client = build_real_agent(tmp_path)
    analysis, result, facts, messages = _run_until_verdict(agent, tmp_path)
    assert "계산할게요" in result["reply"]
    state = unpack_state(facts[STATE_KEY])
    assert state.retrieved_cases and state.fault_assessment is None  # 검색은 대화에서, 판정은 judge 에서
    assert state.current_stage == "READY_FOR_ASSESSMENT"
    assert len(analyzer.calls) == 2  # 대화 중 영상 추가 호출 없음
    assert all(len(unpack_state(facts[STATE_KEY]).pending_questions) <= 1 for _ in [0])

    judged = agent.judge(SimpleNamespace(messages=messages, facts=facts, previous_verdict=None))
    assert judged["ratio_mine"] + judged["ratio_other"] == 100
    assert judged["ratio_mine"] == 30
    assert "나 30 : 상대 70" in judged["summary"] and "습니다" not in judged["summary"]
    assert judged["change_reason"] is None
    assert judged["basis"]["chart"]["name"] and "2018-070162" in judged["basis"]["chart"]["note"]
    precedents = judged["basis"]["precedents"]
    assert precedents[0]["id"] == "2018-070162" and precedents[0]["title"]
    assert all(p["body_text"].strip() for p in precedents)
    assert "과실비율" in precedents[0]["body_text"] and "\n\n" in precedents[0]["body_text"]  # 사례 내용만, 문단 구조
    _json_safe(judged)
    return agent, facts, messages, judged


def test_conversation_reaches_verdict_request_then_judge(tmp_path):
    _reach_judged(tmp_path)


def _reach_documents_requested(tmp_path):
    agent, facts, messages, judged = _reach_judged(tmp_path)
    verdict = Verdict(1, judged["ratio_mine"], judged["ratio_other"], judged["summary"], judged["basis"])
    # 판정 이후 일반 질문: 판정 상세를 /tmp 캐시에서 되살려 답한다
    result, facts, messages = _chat(agent, facts, messages, "왜 내가 30이야?", verdict=verdict)
    assert result["next_action"] == "none" and result["reply"]
    state = unpack_state(facts[STATE_KEY])
    assert state.fault_assessment is not None and state.fault_assessment.anchor_case_id == "2018-070162"
    assert facts[META_KEY]["verdict_version"] == 1 and facts["fault_ratio"] == {"mine": 30, "other": 70}

    # 사건경위서 요청 → 사고 일시를 먼저 묻고 → 답하면 create_report
    result, facts, messages = _chat(agent, facts, messages, "사건경위서 만들어줘", verdict=verdict)
    assert result["next_action"] == "none" and "사고 일시" in result["reply"]
    result, facts, messages = _chat(agent, facts, messages, "2026년 8월 22일 사고였어.", verdict=verdict)
    assert result["next_action"] == "create_report", result["reply"]
    assert "사건경위서 초안을 만들게요" in result["reply"]

    # 반박의견서 요청 — 경위서가 없으면 잠금 안내, 있으면 create_rebuttal
    result, facts, messages = _chat(agent, facts, messages, "반박의견서도 써줘", verdict=verdict, has_report=False)
    assert result["next_action"] == "create_rebuttal" and "사건경위서" in result["reply"]
    result, facts, messages = _chat(agent, facts, messages, "반박의견서 써줘", verdict=verdict, has_report=True)
    assert result["next_action"] == "create_rebuttal" and "반박의견서 초안을 만들게요" in result["reply"]
    return agent, facts, messages, verdict


def test_after_verdict_chat_answers_and_requests_documents(tmp_path):
    _reach_documents_requested(tmp_path)


# --------------------------------------------------------------------------- write


def test_write_report_and_rebuttal_follow_contract(tmp_path):
    agent, facts, messages, verdict = _reach_documents_requested(tmp_path)
    report = agent.write(SimpleNamespace(kind="report", messages=messages, facts=facts, verdict=verdict, revision_request=None, previous_sections=None, report_sections=None))
    sections = report["sections"]
    assert [s["index"] for s in sections] == [1, 2, 3, 4]
    assert [s["title"] for s in sections] == ["사고 일시 및 장소", "사고 경위", "블랙박스 영상 분석 결과", "주장 요지"]
    assert all(s["body"].strip() for s in sections)
    assert "42 km/h" not in sections[1]["body"]  # grounding: 근거 없는 속도 제거
    assert report["caveat"] and report["page_count"] >= 1
    _json_safe(report)

    revised = agent.write(SimpleNamespace(
        kind="report", messages=messages, facts=facts, verdict=verdict, revision_request="2번을 더 간단하게",
        previous_sections=[Section(**s) for s in sections], report_sections=None,
    ))
    assert len(revised["sections"]) == 4 and "(다시 씀)" in revised["sections"][1]["body"]

    rebuttal = agent.write(SimpleNamespace(
        kind="rebuttal", messages=messages, facts=facts, verdict=verdict, revision_request=None, previous_sections=None,
        report_sections=[Section(**s) for s in sections],
    ))
    assert rebuttal["body"] and len(rebuttal["body"]) <= 5000
    assert "2018-070162" in rebuttal["body"] and "1234-567890" not in rebuttal["body"]
    assert "첨부" not in rebuttal["body"] and "본인이 주장하는 과실비율: 나 30 : 상대 70" in rebuttal["body"]


def test_write_works_from_snapshot_when_judge_cache_is_gone(tmp_path):
    agent, facts, messages, judged = _reach_judged(tmp_path)
    from agent.cache import JudgeCache

    agent._cache = JudgeCache(tmp_path / "empty")  # 컨테이너 재시작 등으로 /tmp 가 비워진 상황
    verdict = Verdict(1, judged["ratio_mine"], judged["ratio_other"], judged["summary"], judged["basis"], opponent_claim={"mine": 50, "other": 50})
    report = agent.write(SimpleNamespace(kind="report", messages=messages, facts=facts, verdict=verdict, revision_request=None, previous_sections=None, report_sections=None))
    assert len(report["sections"]) == 4
    rebuttal = agent.write(SimpleNamespace(kind="rebuttal", messages=messages, facts=facts, verdict=verdict, revision_request=None, previous_sections=None, report_sections=None))
    assert rebuttal["body"]


# --------------------------------------------------------------------------- rejudge


def test_new_important_fact_requests_rejudge_with_change_reason(tmp_path):
    agent, facts, messages, judged = _reach_judged(tmp_path)
    verdict = Verdict(1, judged["ratio_mine"], judged["ratio_other"], judged["summary"], judged["basis"])
    result, facts, messages = _chat(agent, facts, messages, "사실 내가 좌회전 중이었어", verdict=verdict)
    assert result["next_action"] == "rejudge", result["reply"]
    assert "다시 계산할게요" in result["reply"]
    state = unpack_state(facts[STATE_KEY])
    assert state.assessment_invalidated and state.assessment_invalidation_reasons

    # 재판정 결과가 달라지는 상황: 확인된 수정요소가 있어야 기준값(30:70)에서 벗어난다
    agent.master._text_client.ratio = (40, 60)
    agent.master._text_client.confirmed_adjustment = True
    rejudged = agent.judge(SimpleNamespace(messages=messages, facts=facts, previous_verdict=verdict))
    assert (rejudged["ratio_mine"], rejudged["ratio_other"]) == (40, 60)
    assert rejudged["change_reason"] and "나 30 : 상대 70에서 나 40 : 상대 60" in rejudged["change_reason"]
    assert "내 차 진행 방향" in rejudged["change_reason"]

    # 새 판정(v2)이 오면 무효화가 풀리고 상세가 되살아난다
    verdict2 = Verdict(2, 40, 60, rejudged["summary"], rejudged["basis"])
    result, facts, messages = _chat(agent, facts, messages, "고마워", verdict=verdict2)
    state = unpack_state(facts[STATE_KEY])
    assert not state.assessment_invalidated and state.fault_assessment.fault_ratio.user == 40
    assert facts[META_KEY]["verdict_version"] == 2


def test_document_request_survives_rejudge(tmp_path):
    """판정 후 새 사실 + 경위서 요청 → 먼저 재판정을 요청하고, 판정이 오면 다음 메시지에서 이어서 경위서를 만든다."""
    agent, facts, messages, judged = _reach_judged(tmp_path)
    verdict = Verdict(1, judged["ratio_mine"], judged["ratio_other"], judged["summary"], judged["basis"])
    result, facts, messages = _chat(agent, facts, messages, "사실 내가 좌회전 중이었어. 그리고 사건경위서 만들어줘", verdict=verdict)
    # 경위서에 필요한 사고 일시를 먼저 묻고(요청은 pending), 답하면 새 사실 때문에 재판정을 먼저 요청한다
    assert result["next_action"] == "none" and "사고 일시" in result["reply"], result["reply"]
    assert unpack_state(facts[STATE_KEY]).pending_intent == "request_incident_report"
    result, facts, messages = _chat(agent, facts, messages, "2026년 8월 22일 사고였어.", verdict=verdict)
    assert result["next_action"] == "rejudge", result["reply"]
    assert "이어서 사건경위서를 만들게요" in result["reply"]
    assert unpack_state(facts[STATE_KEY]).pending_intent == "request_incident_report"

    rejudged = agent.judge(SimpleNamespace(messages=messages, facts=facts, previous_verdict=verdict))
    verdict2 = Verdict(2, rejudged["ratio_mine"], rejudged["ratio_other"], rejudged["summary"], rejudged["basis"])
    # 판정이 오면 다음 메시지(무엇이든)에서 남겨 둔 경위서 요청을 이어서 수행한다
    result, facts, messages = _chat(agent, facts, messages, "응", verdict=verdict2)
    assert result["next_action"] == "create_report", result["reply"]
    assert unpack_state(facts[STATE_KEY]).pending_intent is None


def test_judge_rebuilds_state_when_facts_are_foreign(tmp_path):
    """MockAgent 로 분석된 사건처럼 _case_state 가 없어도 대화만으로 판정한다."""
    agent, _, _ = build_real_agent(tmp_path)
    messages = [Turn("user", "교차로에서 직진 중이었는데 우측에서 오토바이가 신호를 무시하고 들어왔어요. 내 차 블랙박스야"), Turn("assistant", "영상을 분석했어요.")]
    result = agent.judge(SimpleNamespace(messages=messages, facts={"my_lane": "2차로 직진", "opponent_signal": "red"}, previous_verdict=None))
    assert result["ratio_mine"] + result["ratio_other"] == 100
    assert result["basis"]["precedents"] and all(p["body_text"] for p in result["basis"]["precedents"])


def test_opponent_claim_is_parsed_and_returned(tmp_path):
    agent, facts, messages, judged = _reach_judged(tmp_path)
    verdict = Verdict(1, judged["ratio_mine"], judged["ratio_other"], judged["summary"], judged["basis"])
    result, facts, messages = _chat(agent, facts, messages, "상대 보험사가 나 50 : 상대 50 이라고 주장하는데", verdict=verdict)
    assert facts["opponent_claim"] == {"mine": 50, "other": 50}
    assert result["reply"].startswith("상대 보험사 주장(나 50 : 상대 50)을 판정 카드에 함께 표시했어요.")  # 판정 뒤에 말해도 카드 비교에 반영
    assert parse_opponent_claim("보험사는 70대30을 주장") == {"mine": 70, "other": 30}
    assert parse_opponent_claim("상대 보험사 측에서 5:5 과실비율을 주장") == {"mine": 50, "other": 50}  # 10 단위 축약 표기
    assert parse_opponent_claim("상대가 7대3 이래요") == {"mine": 70, "other": 30}
    assert parse_opponent_claim("과실 60% 이래요") is None
    assert parse_opponent_claim(None, {"mine": 30, "other": 70}) == {"mine": 30, "other": 70}


# --------------------------------------------------------------------------- presenters / codec


def test_presenters_and_codec_helpers():
    state = CaseState()
    assert build_case_title(state).startswith("교통사고 · ")
    from state.case_state import Slot

    state.road.road_type = Slot(value="roundabout", source="video", status="CONFIRMED")
    state.ego_vehicle.movement = Slot(value="straight", source="video", status="CONFIRMED")
    state.collision.type = Slot(value="rear_end", source="video", status="CONFIRMED")
    state.accident_datetime.date = Slot(value="2026-08-22", source="user", status="CONFIRMED")
    assert build_case_title(state) == "회전교차로 직진 추돌 · 08-22"
    # LLM이 enum 밖의 표현으로 추출해도 영문 키가 제목에 새지 않는다
    state.ego_vehicle.movement = Slot(value="going_straight", source="user", status="CONFIRMED")
    state.collision.type = Slot(value="side_swipe", source="video", status="CONFIRMED")
    assert build_case_title(state) == "회전교차로 직진 측면 접촉 · 08-22"
    state.ego_vehicle.movement = Slot(value="turning left", source="user", status="CONFIRMED")
    state.road.road_type = Slot(value="signalized intersection", source="user", status="CONFIRMED")
    assert build_case_title(state) == "교차로 좌회전 측면 접촉 · 08-22"
    state.ego_vehicle.movement = Slot(value="weird_value", source="user", status="CONFIRMED")
    state.collision.type = Slot(value="mystery", source="user", status="CONFIRMED")
    assert build_case_title(state) == "교차로 충돌 · 08-22"
    state.road.road_type = Slot(value="roundabout", source="video", status="CONFIRMED")
    card = question_card(Question(field="other_vehicle.turn_signal", question="상대 차가 깜빡이를 켰나요?", why="방향지시등 미점등은 수정요소예요"), first=True)
    assert card == "하나만 물어볼게요. 상대 차가 깜빡이를 켰나요?\n(확인 이유: 방향지시등 미점등은 수정요소예요)"
    assert question_card(Question(field="video_source.vehicle_owner", question="본인 블랙박스인가요?", why="x")) == "본인 블랙박스인가요?"
    facts = pack_facts(state, verdict_version=3)
    _json_safe(facts)
    assert facts[META_KEY]["verdict_version"] == 3 and unpack_state(facts[STATE_KEY]).road.road_type.value == "roundabout"


def test_real_agent_construction_is_cheap_and_lazy(monkeypatch):
    agent = RealAgent()
    assert agent._master is None  # 네트워크·모델 준비는 첫 호출 때
    with pytest.raises(AttributeError):
        agent.nonexistent  # noqa: B018


def test_second_pass_changes_are_humanized():
    from agents.master_agent import MasterAccidentAgent
    from video.schemas import VehicleEntry, VideoResult

    video = VideoResult(
        vehicles=[
            VehicleEntry(id="vehicle_1", description="블랙박스 차량", is_ego=True),
            VehicleEntry(id="vehicle_2", description="노란색 승합차"),
        ],
        changes_from_previous=[
            "재분석 상세 서술 보완",
            "vehicle_2.turn_signal: 00:02.5~00:05.0 구간에서 좌측 방향지시등 미점등 상태를 관찰하고 'none'(CONFIRMED, 신뢰도 0.85)으로 확정 갱신함",
            "vehicle_2.braking: 후미등이 가려져 제동등 직접 확인 불가함을 UNKNOWN으로 명시함",
            "collision pair 변경: ['vehicle_1', 'vehicle_3'] → ['vehicle_1', 'vehicle_2']",
            "네 번째 항목은 잘린다",
        ],
    )
    items = MasterAccidentAgent._second_pass_changes(video)
    assert len(items) == 3
    assert items[0].startswith("노란색 승합차 방향지시등:")
    assert "vehicle_2" not in items[0] and "CONFIRMED" not in items[0] and "신뢰도" not in items[0]
    assert "없음으로" in items[0] and "none" not in items[0]
    assert items[1].startswith("노란색 승합차 제동:") and "UNKNOWN" not in items[1]
    assert items[2].startswith("충돌 차량 조합 변경") and "블랙박스 차량" in items[2] and "vehicle_1" not in items[2]


def test_video_intro_and_recheck_note_hide_internal_ids():
    from agents.master_agent import MasterAccidentAgent
    from video.schemas import VehicleEntry, VideoResult

    video = VideoResult(
        short_summary="블랙박스 차량(vehicle_1)이 교차로를 직진하던 중 좌측에서 진입한 흰색 승용차(vehicle_2)와 충돌한 사고입니다.",
        vehicles=[VehicleEntry(id="vehicle_1", description="블랙박스 촬영 차량 (자차)", is_ego=True), VehicleEntry(id="vehicle_2", description="흰색 승용차")],
    )
    text = MasterAccidentAgent._humanize_vehicle_ids(video, video.short_summary)
    assert "vehicle_" not in text and "흰색 승용차와" in text and "블랙박스 차량이" in text

    state = CaseState()
    state.video_analysis = video
    agent = MasterAccidentAgent(text_client=None, video_agent=None, rag_tool=None, document_agent=None, run_logger=None)  # type: ignore[arg-type]
    intro = agent._video_intro(state)
    assert "vehicle_" not in intro and "블랙박스 촬영 차량, 흰색 승용차" in intro and "자차" not in intro  # 소유 관계는 묻기 전이라 단정하지 않는다

    # 재분석 안내: 내부 지시문·모델의 변경 기록 대신 슬롯에서 새로 확정된 라벨만
    events = [
        "영상 focus 재분석 완료 [충돌 시점 전후에서 실제 접촉한 두 차량을 재검증하라. 상대 차량이 vehicle inventory에 없으면]: vehicle_2.turn_signal 필드를 'none'(CONFIRMED)으로 기록함",
        "영상 재확인 결과: 상대 방향지시등 미점등; 점선 구간",
    ]
    note = agent._recheck_note(events, state)
    assert note == "말씀해 주신 내용을 바탕으로 영상을 다시 확인했어요. 새로 확인된 점: 상대 방향지시등 미점등, 점선 구간."
    assert "재검증하라" not in note and "필드" not in note and "vehicle_2" not in note
    assert agent._recheck_note(["영상 focus 재분석 완료 [x]: 기존 결론 유지", "영상 재확인 결과: 새로 확인된 사실 없음"], state).endswith("결론은 그대로예요.")
    assert agent._recheck_note([], state) == ""


def test_review_ack_and_opponent_candidate_question():
    from agents.master_agent import MasterAccidentAgent
    from video.schemas import VehicleEntry, VideoResult
    from video.validation import user_confirmation_question

    # 요약문에는 상대 차량이 적혀 있지만 목록에는 없는 경우 → '찾지 못했다'가 아니라 후보를 확인한다
    video = VideoResult(
        short_summary="블랙박스 차량(vehicle_1)이 직진 중 좌측에서 중앙선을 넘어 진입한 흰색 승용차(vehicle_2)와 충돌한 사고입니다.",
        vehicles=[VehicleEntry(id="vehicle_1", description="블랙박스 촬영 차량", is_ego=True)],
        ego_vehicle_id="vehicle_1",
    )
    question = user_confirmation_question(video)
    assert "흰색 승용차" in question and "찾지 못했어요" not in question and "맞나요" in question
    video.short_summary = "충돌 사고입니다."
    assert user_confirmation_question(video).startswith("영상 분석에서 상대 차량을 확정하지 못했어요.")


def test_precedent_body_text_is_structured_case_only():
    """팝업 글은 그 심의사례 내용만, 소제목 + 문장 하나 = 문단 하나. 내 사건 비교·기준값 문장은 넣지 않는다."""
    from agent.presenters import PRECEDENT_SECTION_LABELS, precedent_body_text
    from state.case_state import CaseRelevance, RetrievedCase

    case = RetrievedCase(
        case_id="2017-045140",
        title="차대차 직진 대 좌회전 사고(맞은편) - 사거리 교차로(상대 차량이 맞은편 방향에서 진입)",
        accident_type="차대차 직진 대 좌회전 사고(맞은편)",
        chart_number="213(나)",
        basic_ratio="80:20",
        decision_ratio="70:30",
        accident_description="신호에 직진하던 피청구차량과 충돌한 사고임 (나) A차량이 황색신호에 진입하여 참고 신호위반을 하였다 인정기준 213(나) 는 점에서",
        key_issues=["청구차량이 황색신호에 교차로 진입하였는지 여부", "녹색신호에 직진한 피청구차량의 과실 유무"],
        decision_reasons=[
            "청구차량이 교차로의 신호가 좌회전신호에서 황색신호로 바뀌었음에도 좌회전하여 교차로에 진입하다가 우 측도로에서 녹색신호로 바뀌자마자 직진하던 피청구차량과 충돌한 사고임",
            "청구차량이 황색신호로 바뀌었음에도 교차로에 꼬리물기식으로 진입하였던 점, 피청구차량은 교차로의 상황 을 살피지 않고 녹색신호로 바뀌자마자 직진을 하여 교차로에 진입하였던 점 고려하여 결정함",
            "청구차량 70% ● 피청구차량 30%",
        ],
        relevance=CaseRelevance(case_id="2017-045140", relevance=0.9, matched_factors=["사거리 교차로"], different_factors=["상대 차량 진입 방향"]),
    )
    text = precedent_body_text(case)
    paragraphs = text.split("\n\n")
    labels = [p for p in paragraphs if p in PRECEDENT_SECTION_LABELS]
    # 사례 내용 → 내 사건과의 비교 순서. 판정 정보가 없으면 '판정에서의 역할'은 없다
    assert labels == ["사고 유형", "사고 내용", "쟁점", "과실비율", "심의 이유", "참고 인정기준", "내 사건과 비슷한 점", "내 사건과 다른 점"]
    assert "사거리 교차로" in text
    i = paragraphs.index("사고 내용")
    assert paragraphs[i + 1].startswith("청구차량이 교차로의 신호가") and paragraphs[i + 1].endswith("충돌한 사고임.")
    assert "70% ● 피청구차량" not in text and "참고 신호위반" not in text
    assert "기본 80:20 → 결정 70:30 (A 청구차량 : B 피청구차량)" in paragraphs
    assert "도표 213(나)" in paragraphs
    # 심의 이유는 문장마다 한 문단
    j = paragraphs.index("심의 이유")
    assert paragraphs[j + 1].startswith("청구차량이 황색신호로 바뀌었음에도") and paragraphs[j + 1].endswith("결정함.")
    assert paragraphs[paragraphs.index("내 사건과 비슷한 점") + 1] == "사거리 교차로"
    assert paragraphs[paragraphs.index("내 사건과 다른 점") + 1] == "상대 차량 진입 방향"

    # 판정 정보가 있으면 이 사례가 판정에서 어떤 역할이었는지 한 줄 덧붙인다
    from state.case_state import FaultAssessment, FaultRatio

    assessment = FaultAssessment(fault_ratio=FaultRatio(user=30, opponent=70), anchor_case_id="2017-045140", primary_case_ids=["2017-045140"])
    with_role = precedent_body_text(case, assessment).split("\n\n")
    assert with_role[-2] == "판정에서의 역할" and with_role[-1].startswith("가장 비슷한 사례예요. 이 사례의 결정비율 70:30을 기준값으로")
    assessment.anchor_case_id = "other"
    assessment.primary_case_ids = []
    assert precedent_body_text(case, assessment).endswith("비교를 위해 함께 살펴본 사례예요.")

    chart = RetrievedCase(case_id="차1-1", source_type="fault_standard", title="녹색직진 대 적색직진", chart_number="차1-1", basic_ratio="0:100", modification_factors=["A 현저한 과실 +10", "A 중대한 과실 +20"])
    chart_text = precedent_body_text(chart)
    assert chart_text.startswith("과실비율 인정기준 도표 차1-1") and "기본 과실비율\n\nA:B = 0:100" in chart_text and "수정요소\n\nA 현저한 과실 +10" in chart_text


def test_readable_lines_breaks_after_sentences_only():
    from agent.presenters import readable_lines

    text = "확인했어요. 한 가지만 더 확인할게요. 상대 차량이 깜빡이를 켰나요? 속도는 0.85 정도예요. 1. 첫째 2. 둘째 (mp4 권장 · 최대 200MB · 3분 이내)"
    assert readable_lines(text) == (
        "확인했어요.\n한 가지만 더 확인할게요.\n상대 차량이 깜빡이를 켰나요?\n속도는 0.85 정도예요.\n1. 첫째 2. 둘째 (mp4 권장 · 최대 200MB · 3분 이내)"
    )
    # 이미 있는 줄바꿈과 빈 줄은 그대로, 괄호 안 마침표는 문장 끝이 아니다
    card = "하나만 물어볼게요. 상대 차가 깜빡이를 켰나요?\n(확인 이유: 방향지시등 미점등은 수정요소예요.)"
    assert readable_lines(card) == "하나만 물어볼게요.\n상대 차가 깜빡이를 켰나요?\n(확인 이유: 방향지시등 미점등은 수정요소예요.)"
    assert readable_lines("- 2017-045140 제목 · 기본 80:20, 결정 70:30\n  공통점: 사거리 교차로\n\n심의사례의 사실관계는 다를 수 있어요.") == (
        "- 2017-045140 제목 · 기본 80:20, 결정 70:30\n  공통점: 사거리 교차로\n\n심의사례의 사실관계는 다를 수 있어요."
    )
    assert readable_lines(None) is None and readable_lines("") == ""


def test_fact_chips_from_video_confirmed_slots():
    """사건 현황판 칩: 영상 CONFIRMED 항목만 짧은 라벨로, 영상으로 못 본 과실 요소는 '확인 필요'로."""
    from agent.presenters import fact_chips
    from state.case_state import Slot

    state = CaseState()
    v = lambda value: Slot(value=value, source="video", status="CONFIRMED")  # noqa: E731
    state.road.road_type = v("roundabout")
    state.road.signal_present = v("false")
    state.road.lane_marking = v("dashed")
    state.ego_vehicle.movement = v("straight")
    state.ego_vehicle.lane = v("lane_2")
    state.ego_vehicle.estimated_speed = v("약 48km/h")
    state.other_vehicle.entry_direction = v("left_side_road")
    state.other_vehicle.movement = v("lane_change_right")
    state.other_vehicle.lane = v("lane_1")
    state.other_vehicle.signal = Slot(value="red", source="user", status="CONFIRMED")  # 사용자 진술 → 칩 아님
    state.other_vehicle.turn_signal = Slot(value="none", source="video", status="INFERRED")  # 추정 → 칩 아님
    state.collision.type = v("side_swipe")
    state.collision.ego_collision_part = v("left_side")
    state.uncertain_facts = ["vehicle_2의 우측 방향지시등 점등 여부 (화각 사각지대로 확인 불가)", "사용자가 accident_datetime.date에 대해 모른다고 답함", "정지선 통과 시점 확인 불가"]

    chips = fact_chips(state)
    labels = [item["label"] for item in chips["items"]]
    assert labels[:6] == ["회전교차로", "2차로 직진", "상대 좌측 진입", "상대 1차로 차로 변경", "신호등 없음", "점선 구간"]
    assert "약 48km/h" in labels and "측면 접촉" in labels and "내 차 좌측면" in labels
    assert "상대 적색 신호 (위반)" not in labels and "상대 방향지시등 미점등" not in labels
    pending = [item["label"] for item in chips["items"] if item["source"] == "pending"]
    assert pending == ["상대 방향지시등 확인 필요", "정지선 통과 확인 필요"]  # 사고 일시 '모름'은 쟁점이 아니다
    assert chips["confirmed"] == len(labels) - 2 and chips["total"] == len(labels)
    assert all(len(item["label"]) <= 16 for item in chips["items"])

    packed = pack_facts(state)
    assert packed["fact_chips"]["items"][0]["label"] == "회전교차로"
    _json_safe(packed)
