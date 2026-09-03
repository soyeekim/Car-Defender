"""Agent 계약 준수 테스트 — AI 담당자가 자기 구현체를 검사할 때 쓴다.

기본값은 MockAgent라 평소 테스트에도 같이 돈다. 자기 구현체를 검사하려면:

    AGENT_CONTRACT_IMPL=ai.agent.real:RealAgent \
    AGENT_CONTRACT_VIDEO=/경로/블랙박스.mp4 \
    .venv/Scripts/python -m pytest tests/test_agent_contract.py -v

여기를 통과하면 백엔드에 붙는다. 실패 메시지는 무엇을 고쳐야 하는지까지 알려준다.
자세한 계약은 docs/agent-interface.md, 스키마 정본은 app/agent/base.py.
"""

import json
import os

import pytest

from app.agent.base import (
    AnalyzeInput,
    ChatInput,
    ChatTurn,
    JudgeInput,
    VerdictSnapshot,
    WriteInput,
)
from app.agent.loader import AgentAdapter, load_agent_class

IMPL = os.environ.get("AGENT_CONTRACT_IMPL", "app.agent.mock:MockAgent")
VIDEO = os.environ.get("AGENT_CONTRACT_VIDEO", "")

MESSAGES = [
    ChatTurn(role="user", text="교차로에서 직진 중이었는데 우측에서 오토바이가 신호를 무시하고 들어왔어요."),
    ChatTurn(role="assistant", text="영상을 분석했어요."),
]
FACTS = {"my_lane": "2차로 직진", "opponent_signal": "red", "my_speed_kph": 48}
VERDICT = VerdictSnapshot(
    version=1, ratio_mine=0, ratio_other=100,
    summary="상대 신호위반이 영상으로 확인돼요.",
    opponent_claim={"mine": 30, "other": 70},
    basis={},
)


@pytest.fixture(scope="module")
def agent():
    return AgentAdapter(load_agent_class(IMPL)())


def assert_json_safe(value, where: str):
    """facts / fact_updates 는 DB에 JSON으로 저장된다. datetime·set·커스텀 객체는 저장 시점에 Job을 죽인다."""
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError as e:
        pytest.fail(f"{where} 에 JSON으로 저장할 수 없는 값이 있어요: {e}\n"
                    f"dict/list/str/int/float/bool/None 만 담아야 해요.")


async def test_analyze(agent, tmp_path):
    path = VIDEO
    if not path or not os.path.exists(path):
        path = str(tmp_path / "sample.mp4")
        open(path, "wb").write(b"\x00" * 1024)
    r = await agent.analyze(AnalyzeInput(
        video_path=path, video_mime="video/mp4",
        description="교차로에서 직진 중 우측에서 진입한 오토바이와 부딪혔어요.",
    ))
    assert r.summary_text.strip(), "summary_text 가 비었어요. 분석 결과 말풍선에 그대로 나가는 본문이에요."
    assert r.title.strip(), "title 이 비었어요. 사건 목록에 보이는 제목이에요."
    assert_json_safe(r.facts, "analyze 의 facts")
    assert all(isinstance(q, str) and q.strip() for q in r.questions), "questions 에 빈 문자열이 있어요."


async def test_chat_asks_or_answers(agent):
    r = await agent.chat(ChatInput(
        messages=MESSAGES, new_message="앞쪽 오른쪽 펜더에 부딪혔어요",
        facts=FACTS, questions=[], verdict=None, has_video=True, has_report=False,
    ))
    assert r.reply.strip(), "reply 가 비었어요. 사용자에게 보여 줄 답변이에요."
    assert_json_safe(r.fact_updates, "chat 의 fact_updates")


async def test_chat_can_request_verdict(agent):
    """대화가 무르익으면 next_action 으로 다음 단계를 요청할 수 있어야 한다."""
    r = await agent.chat(ChatInput(
        messages=MESSAGES, new_message="과실비율 계산해줘",
        facts={**FACTS, "impact_part": "앞 오른쪽 펜더"},
        questions=[], verdict=None, has_video=True, has_report=False,
    ))
    assert r.next_action in ("none", "verdict", "rejudge", "create_report", "create_rebuttal")


async def test_judge(agent):
    r = await agent.judge(JudgeInput(messages=MESSAGES, facts=FACTS, previous_verdict=None))
    assert r.ratio_mine + r.ratio_other == 100
    assert r.summary.strip(), "summary 가 비었어요. 판정 카드 본문이에요."
    assert r.basis.chart.name.strip(), "basis.chart.name 이 비었어요. 근거로 삼은 도표 이름이에요."
    assert r.basis.precedents, "basis.precedents 가 비었어요. 판례를 최소 하나는 제시해야 해요."
    for p in r.basis.precedents:
        assert p.title.strip(), f"판례 {p.id} 의 title 이 비었어요."
        assert p.body_text.strip(), (
            f"판례 {p.id} 의 body_text 가 비었어요.\n"
            "H37 판례 팝업에 그대로 나가는 본문이라 judge 가 채워 보내야 해요. "
            "백엔드는 explain() 을 부르지 않으니 여기서 비우면 나중에 채울 방법이 없어요."
        )


async def test_judge_rejudge_explains_change(agent):
    """재판정이면 무엇이 왜 바뀌었는지 사용자에게 설명해야 한다."""
    r = await agent.judge(JudgeInput(
        messages=MESSAGES + [ChatTurn(role="user", text="사실 신호가 황색이었어요")],
        facts=FACTS, previous_verdict=VERDICT,
    ))
    assert r.ratio_mine + r.ratio_other == 100
    if (r.ratio_mine, r.ratio_other) != (VERDICT.ratio_mine, VERDICT.ratio_other):
        assert r.change_reason and r.change_reason.strip(), (
            "비율이 바뀌었는데 change_reason 이 비었어요. 무엇 때문에 바뀌었는지 적어야 해요."
        )


async def test_write_report(agent):
    r = await agent.write(WriteInput(kind="report", messages=MESSAGES, facts=FACTS, verdict=VERDICT))
    assert r.sections, "report 인데 sections 가 비었어요. 경위서는 4개 섹션으로 이뤄져요."
    assert len(r.sections) == 4, f"경위서 섹션은 4개여야 해요 (지금 {len(r.sections)}개)."
    assert [s.index for s in r.sections] == [1, 2, 3, 4], "섹션 index 는 1,2,3,4 순서예요."
    for s in r.sections:
        assert s.title.strip() and s.body.strip(), f"섹션 {s.index} 의 title 또는 body 가 비었어요."


async def test_write_report_revision(agent):
    """다시 쓰기 요청이 오면 이전 원고를 받아 고쳐 쓴다."""
    first = await agent.write(WriteInput(kind="report", messages=MESSAGES, facts=FACTS, verdict=VERDICT))
    r = await agent.write(WriteInput(
        kind="report", messages=MESSAGES, facts=FACTS, verdict=VERDICT,
        revision_request="속도 이야기를 조금 더 자세히 써 주세요.",
        previous_sections=first.sections,
    ))
    assert r.sections and len(r.sections) == 4, "다시 쓴 경위서도 섹션 4개여야 해요."


async def test_write_rebuttal(agent):
    report = await agent.write(WriteInput(kind="report", messages=MESSAGES, facts=FACTS, verdict=VERDICT))
    r = await agent.write(WriteInput(
        kind="rebuttal", messages=MESSAGES, facts=FACTS, verdict=VERDICT,
        report_sections=report.sections,
    ))
    assert r.body and r.body.strip(), "rebuttal 인데 body 가 비었어요. 메일 본문으로 그대로 나가요."
    assert len(r.body) <= 5000, (
        f"반박의견서 본문이 {len(r.body)}자예요. 5000자를 넘으면 안 돼요.\n"
        "만들 때는 통과하지만, 사용자가 그 글을 화면에서 고치려 하면 5000자 제한에 걸려 수정이 막혀요."
    )


def test_all_required_methods_exist():
    """explain 은 선택이고 백엔드가 부르지 않는다. 나머지 4개는 반드시 있어야 한다."""
    impl = load_agent_class(IMPL)()
    missing = [m for m in ("analyze", "chat", "judge", "write") if not callable(getattr(impl, m, None))]
    assert not missing, f"{IMPL} 에 없는 메서드: {', '.join(missing)}"


def test_construction_is_cheap():
    """인스턴스는 프로세스당 한 번만 만들어지고, 이벤트 루프 밖에서 생성된다.

    __init__ 에서 네트워크 호출이나 asyncio.run 을 하면 서버 기동이 막히거나 죽는다.
    """
    cls = load_agent_class(IMPL)
    cls()  # 예외 없이 즉시 끝나야 한다
