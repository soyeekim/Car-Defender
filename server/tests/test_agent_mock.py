import pytest
from pydantic import ValidationError

from app.agent.base import (
    AnalyzeInput,
    ChatInput,
    JudgeInput,
    JudgeResult,
    VerdictSnapshot,
    WriteInput,
)
from app.agent.loader import get_agent, reset_agent
from app.agent.mock import MockAgent


def test_judge_result_requires_ratio_sum_100():
    with pytest.raises(ValidationError):
        JudgeResult(ratio_mine=10, ratio_other=80, summary="s", basis={"chart": {"name": "n", "note": "x"}, "precedents": []})


async def test_mock_full_scenario():
    agent = MockAgent()
    a = await agent.analyze(AnalyzeInput(video_path="x.mp4", video_mime="video/mp4", description="교차로에서 오토바이가 박았어요"))
    assert a.title == "교차로 직진 충돌 · 08-22"
    assert a.video_meta.speed_kph == 48 and a.video_meta.impact_at_sec == 31
    assert len(a.questions) == 1 and "1/2" in a.questions[0]

    facts = dict(a.facts)
    c1 = await agent.chat(ChatInput(messages=[], new_message="우측 앞펜더요.", facts=facts, questions=a.questions, verdict=None, has_video=True, has_report=False))
    assert c1.next_action == "none" and "2/2" in c1.reply
    facts.update(c1.fact_updates)

    c2 = await agent.chat(ChatInput(messages=[], new_message="초록불이었어요.", facts=facts, questions=a.questions, verdict=None, has_video=True, has_report=False))
    assert c2.next_action == "verdict"
    facts.update(c2.fact_updates)

    j = await agent.judge(JudgeInput(messages=[], facts=facts, previous_verdict=None))
    assert (j.ratio_mine, j.ratio_other) == (0, 100) and j.change_reason is None
    assert len(j.basis.precedents) == 2 and j.basis.precedents[0].body_text

    snap = VerdictSnapshot(version=1, ratio_mine=0, ratio_other=100, summary=j.summary, opponent_claim=None, basis=j.basis.model_dump())
    c3 = await agent.chat(ChatInput(messages=[], new_message="다시 보니까 상대 신호가 황색이었던 것 같아요.", facts=facts, questions=[], verdict=snap, has_video=True, has_report=False))
    assert c3.next_action == "rejudge" and c3.fact_updates == {"opponent_signal": "yellow"}
    facts.update(c3.fact_updates)
    j2 = await agent.judge(JudgeInput(messages=[], facts=facts, previous_verdict=snap))
    assert (j2.ratio_mine, j2.ratio_other) == (20, 80) and j2.change_reason

    c4 = await agent.chat(ChatInput(messages=[], new_message="상대 보험사는 30:70이라고 해요", facts=facts, questions=[], verdict=snap, has_video=True, has_report=False))
    assert c4.fact_updates == {"opponent_claim": {"mine": 30, "other": 70}} and c4.next_action == "none"

    c5 = await agent.chat(ChatInput(messages=[], new_message="사건경위서 만들어 주세요", facts=facts, questions=[], verdict=snap, has_video=True, has_report=False))
    assert c5.next_action == "create_report"
    c6 = await agent.chat(ChatInput(messages=[], new_message="바로 반박의견서 보낼 수 있어요?", facts=facts, questions=[], verdict=snap, has_video=True, has_report=True))
    assert c6.next_action == "create_rebuttal"

    w = await agent.write(WriteInput(kind="report", messages=[], facts=facts, verdict=snap, revision_request=None, previous_sections=None, report_sections=None))
    assert [s.index for s in w.sections] == [1, 2, 3, 4] and w.page_count == 2
    w2 = await agent.write(WriteInput(kind="rebuttal", messages=[], facts=facts, verdict=snap, revision_request=None, previous_sections=None, report_sections=w.sections))
    assert w2.body and w2.sections is None


async def test_judge_fills_body_text_for_every_precedent():
    """계약: judge 가 H37 팝업 본문(body_text)을 채워 준다. 백엔드는 explain 을 부르지 않는다."""
    agent = MockAgent()
    for previous in (None, VerdictSnapshot(version=1, ratio_mine=0, ratio_other=100, summary="s", opponent_claim=None, basis={})):
        j = await agent.judge(JudgeInput(messages=[], facts={"opponent_signal": "yellow"}, previous_verdict=previous))
        assert j.basis.precedents
        assert all(p.body_text.strip() for p in j.basis.precedents)


async def test_mock_chat_without_video_asks_for_upload():
    agent = MockAgent()
    c = await agent.chat(ChatInput(messages=[], new_message="어제 사고났어요", facts={}, questions=[], verdict=None, has_video=False, has_report=False))
    assert "영상" in c.reply and c.next_action == "none"


async def test_get_agent_wraps_sync_impl(test_env, monkeypatch):
    monkeypatch.setenv("AGENT_IMPL", "tests.test_agent_mock:SyncAgent")
    from app.config import get_settings

    get_settings.cache_clear()
    reset_agent()
    agent = get_agent()
    r = await agent.chat(ChatInput(messages=[], new_message="x", facts={}, questions=[], verdict=None, has_video=True, has_report=False))
    assert r.reply == "sync"
    reset_agent()


class SyncAgent:
    def chat(self, inp):
        return {"reply": "sync", "next_action": "none", "fact_updates": {}}
