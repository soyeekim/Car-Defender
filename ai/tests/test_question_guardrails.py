"""질문 가드레일: LLM 이 고른 질문이 조용히 사라져 대화가 갑자기 심의사례·판정으로 넘어가는 일을 막는다."""

from types import SimpleNamespace

from case.questions import classify_video_topic, normalize_field
from fakes import FakeTextClient, sample_observation
from state.case_state import CaseState, Question
from state.updater import merge_video_facts
from video.schemas import VideoResult


def _video_state() -> CaseState:
    state = CaseState(video_path="x.mp4", video_uploaded=True)
    merge_video_facts(state, VideoResult.from_observation(sample_observation(), video_backend="fake"))
    return state


def test_normalize_field_maps_video_schema_names_to_case_state_slots():
    state = _video_state()
    state.ego_vehicle.vehicle_id = "vehicle_1"
    state.other_vehicle.vehicle_id = "vehicle_3"
    assert normalize_field(state, "vehicles.vehicle_3.turn_signal") == "other_vehicle.turn_signal"
    assert normalize_field(state, "vehicle_2.turn_signal") == "other_vehicle.turn_signal"  # 블랙박스 차량이 아니면 상대 차량
    assert normalize_field(state, "vehicles.vehicle_1.braking") == "ego_vehicle.braking"
    assert normalize_field(state, "opponent.entered_first") == "other_vehicle.entered_first"
    assert normalize_field(state, "other_vehicle.turn_signal") == "other_vehicle.turn_signal"  # 이미 슬롯 경로
    assert normalize_field(state, "review.pre_video_situation") == "review.pre_video_situation"
    assert normalize_field(state, "vehicles.vehicle_3.honked") == "review.other_vehicle_honked"  # 슬롯이 없어도 질문은 살린다
    assert normalize_field(state, "something.odd") == "review.odd"
    assert normalize_field(state, None) is None


def test_video_topic_is_asked_when_no_recheck_budget(monkeypatch):
    from case.questions import VIDEO_OBSERVABLE_TOPICS

    state = _video_state()
    # 영상이 이미 CONFIRMED 로 확인한 주제는 'drop' 이 맞으므로, 확인되지 않은 화면 주제 하나를 고른다
    confirmed = " ".join(fact.fact for fact in state.video_confirmed_facts()).lower()
    keywords, _ = next(item for item in VIDEO_OBSERVABLE_TOPICS if not any(k.lower() in confirmed for k in item[0]))
    question = Question(field="review.video_topic", question=f"충돌 직전 {keywords[0]} 상황이 어땠나요?", why=f"{keywords[0]} 확인")
    # 기본 설정(MASTER_MAX_RECHECKS=0): 재분석으로 보내면 실행되지 않고 질문만 사라진다 → 사용자에게 묻는다
    monkeypatch.setattr("case.questions.get_settings", lambda: SimpleNamespace(agent=SimpleNamespace(max_master_rechecks=0)))
    assert classify_video_topic(state, question)[0] == "ask"
    # 예산이 있으면 화면에 찍히는 사실은 재분석으로 보낸다
    monkeypatch.setattr("case.questions.get_settings", lambda: SimpleNamespace(agent=SimpleNamespace(max_master_rechecks=2)))
    decision, target = classify_video_topic(state, question)
    assert decision == "recheck" and target is not None and target.focus


def test_nonstandard_field_from_llm_still_gets_asked(tmp_path):
    """실제 웹 대화에서 났던 문제: LLM 이 'vehicles.vehicle_2.turn_signal' 로 질문을 골랐는데 슬롯이 없어 버려지고,
    영상 소유 답변 직후 질문 없이 심의사례로 넘어갔다."""
    from test_server_adapter import Turn, _chat, _video, build_real_agent

    class VideoSchemaNames(FakeTextClient):
        def _master_followup_question(self, user):
            out = super()._master_followup_question(user)
            if any(q.get("importance") == "critical" for q in out.get("questions", [])):
                return out  # 영상 소유 관계 같은 필수 질문 턴은 그대로
            out["questions"] = [{
                "field": "vehicles.vehicle_2.turn_signal",  # 실제 LLM 이 쓴 영상 스키마식 이름
                "question": "상대 차량이 차로를 바꾸기 전에 방향지시등을 켰나요?",
                "importance": "high",
                "why": "방향지시등 미점등은 상대 과실을 더하는 수정요소예요.",
            }]
            out["video_recheck_targets"] = []
            return out

    agent, _, _ = build_real_agent(tmp_path, text_client=VideoSchemaNames())
    description = "사거리에서 직진하다가 우측에서 온 차와 부딪혔어."
    analysis = agent.analyze(SimpleNamespace(video_path=_video(tmp_path), video_mime="video/mp4", description=description))
    facts = dict(analysis["facts"])
    messages = [Turn("user", description), Turn("assistant", analysis["summary_text"])] + [Turn("assistant", q) for q in analysis["questions"]]
    result, facts, _ = _chat(agent, facts, messages, "내 차 블랙박스야")
    # 영상 소유 답변 다음에 심의사례로 건너뛰지 않고 방향지시등 질문을 한다
    assert "방향지시등" in result["reply"] and "비슷한 심의사례" not in result["reply"], result["reply"]
    from agent.codec import STATE_KEY, unpack_state

    state = unpack_state(facts[STATE_KEY])
    assert state.pending_questions and state.pending_questions[0].field == "other_vehicle.turn_signal"


def test_opponent_direction_is_never_asked_to_user(tmp_path):
    """영상이 상대 진행 방향을 못 봤어도 사용자에게 '어떤 방향으로 진행했나요'를 묻지 않는다 (사고 정황은 영상 agent 의 몫)."""
    from case.questions import classify_video_topic
    from fakes import sample_observation
    from state.case_state import CaseState, Question
    from state.updater import merge_video_facts
    from video.schemas import VideoResult

    state = CaseState(video_path="v.mp4", video_uploaded=True)
    merge_video_facts(state, VideoResult.model_validate(sample_observation().model_dump() | {"video_backend": "fake"}))
    direction = Question(field="other_vehicle.movement", question="상대 차량(흰색 승용차)이 사고 당시 어떤 방향으로 진행하고 있었나요? 예를 들어 직진, 좌회전, 우회전 등 알려주세요.", why="우선권 판단")
    assert classify_video_topic(state, direction, max_rechecks=0)[0] == "drop"
    identity = Question(field="collision.participants_confirmed", question="상대 차량이 어느 쪽에서 온 흰색 세단이 맞나요?", why="충돌 차량 확인")
    assert classify_video_topic(state, identity, max_rechecks=0)[0] == "ask"
    driver = Question(field="review.opponent_driver_state", question="경찰 조사에서 상대 운전자의 음주·무면허가 확인됐나요?", why="중대한 과실")
    assert classify_video_topic(state, driver, max_rechecks=0)[0] == "ask"



def test_turn_signal_is_not_asked_for_vehicle_without_lane_change_or_turn():
    """실서버 대화: 차로를 유지한 블랙박스 차량에게 방향지시등을 물었다. 직진·차로 유지 차량의 방향지시등은 과실 요소가 아니다."""
    from case.questions import field_is_askable, turn_signal_irrelevant
    from state.updater import set_slot

    state = _video_state()
    state.ego_vehicle.vehicle_id, state.other_vehicle.vehicle_id = "vehicle_1", "vehicle_2"
    set_slot(state, "ego_vehicle.movement", "직진", source="video", status="CONFIRMED", overwrite=True)
    set_slot(state, "ego_vehicle.lane_change", "false", source="video", status="CONFIRMED", overwrite=True)
    assert turn_signal_irrelevant(state, "ego_vehicle.turn_signal")
    assert not field_is_askable(state, "ego_vehicle.turn_signal")
    # 차로를 바꾼 상대 차량의 방향지시등은 그대로 묻는다
    set_slot(state, "other_vehicle.lane_change", "true", source="video", status="CONFIRMED", overwrite=True)
    assert not turn_signal_irrelevant(state, "other_vehicle.turn_signal")
    assert field_is_askable(state, "other_vehicle.turn_signal")
    # 상대 차량은 직진처럼 보여도 차로 변경이 명시 부정되지 않았으면 묻는다(기존 동작 유지)
    set_slot(state, "other_vehicle.movement", "직진", source="video", status="CONFIRMED", overwrite=True)
    state.other_vehicle.lane_change.value, state.other_vehicle.lane_change.status = None, "UNKNOWN"
    assert not turn_signal_irrelevant(state, "other_vehicle.turn_signal")


def test_lane_keeping_answer_resolves_pending_turn_signal_question():
    """실서버 대화: "제 차선에서 주행중이었습니다"에 "확인했어요"라 하고 같은 방향지시등 질문을 그대로 되물었다."""
    from case.questions import resolve_pending_by_implication

    state = _video_state()
    state.ego_vehicle.vehicle_id = "vehicle_1"
    state.pending_questions = [Question(field="ego_vehicle.turn_signal", question="블랙박스 차량(vehicle_1)의 방향지시등이 켜져 있었나요?", importance="high")]
    assert resolve_pending_by_implication(state, "저는 제 차선에서 주행중이었습니다.") == ["ego_vehicle.turn_signal"]
    assert state.pending_questions == []
    assert state.ego_vehicle.lane_change.value == "false" and state.ego_vehicle.lane_change.source == "user"
    assert "ego_vehicle.turn_signal" in state.asked_fields and "ego_vehicle.turn_signal" in state.review_answers
    # 관련 없는 답은 건드리지 않는다
    state.pending_questions = [Question(field="ego_vehicle.turn_signal", question="q", importance="high")]
    assert resolve_pending_by_implication(state, "날씨가 맑았어요.") == []
    assert len(state.pending_questions) == 1


def test_fallback_patterns_understand_common_korean_answers():
    """안전망 정규식도 활용형을 알아야 한다: 모릅니다 / 특별히 없어요 / 넵."""
    from agents.master_agent import _AFFIRMATIVE, _NEGATIVE_ANSWER
    from case.questions import UNKNOWN_ANSWER_PATTERN

    for text in ("모릅니다.", "몰라요", "몰랐어요", "기억이 안 나요", "기억나지 않아요", "확인 안 됐어요", "알 수 없어요", "패스"):
        assert UNKNOWN_ANSWER_PATTERN.search(text), text
    for text in ("모릅니다.", "특별히 없어요", "별로요", "음, 없는 것 같아요", "글쎄요, 아니요"):
        assert _NEGATIVE_ANSWER.match(text), text
    for text in ("넵", "넹", "맞아요", "그럼요", "당연하죠", "ㅇㅋ"):
        assert _AFFIRMATIVE.match(text), text
    assert not _AFFIRMATIVE.match("아니요")
