from sqlalchemy import select

from app.clock import now_utc
from app.db import session_scope
from app.jobs.runner import runner
from app.models import Analysis, Case, Video
from app.services.cases import add_message
from app.services.verdict import perform_verdict


async def test_analysis_job_full_path(client, auth_headers, case_id, upload, sse):
    async with session_scope() as db:
        await add_message(db, case_id, "user", "text", {"text": "교차로에서 오토바이가 박았어요"}, publish=False)
    tap = await sse(case_id)
    res = await upload()
    assert res.json()["analysis"]["started"] is True
    await runner.wait_all()

    frames = await tap.take(7)
    kinds = [(f.split("event: ")[1].split("\n")[0]) for f in frames]
    # video_attachment 카드 · case.updated(analyzing, Job 시작) · 요약 text · 질문 text · message.updated(meta)
    # · case.updated(needs_review, 핸들러 안 — Job은 아직 running) · case.updated(Job 성공 후 — activeJob null)
    assert kinds == ["message.created", "case.updated", "message.created", "message.created", "message.updated", "case.updated", "case.updated"]
    assert '"status": "analyzing"' in frames[1] and '"kind": "analysis"' in frames[1]
    assert "영상을 분석했어요" in frames[2] and '"cta": null' in frames[2]
    assert "1/2" in frames[3]
    assert '"speedKph": 48' in frames[4]
    assert '"status": "needs_review"' in frames[5] and '"title": "교차로 직진 충돌 · 08-22"' in frames[5]
    assert '"status": "needs_review"' in frames[6] and '"activeJob": null' in frames[6]

    async with session_scope() as db:
        a = await db.get(Analysis, case_id)
        assert a.facts["opponent_signal"] == "red" and a.questions
        v = (await db.execute(select(Video).where(Video.case_id == case_id))).scalar_one()
        assert v.meta == {"speedKph": 48, "impactAtSec": 31}
        c = await db.get(Case, case_id)
        assert c.status == "needs_review"


async def test_analysis_without_questions_goes_straight_to_verdict(client, auth_headers, case_id, upload, monkeypatch):
    from app.agent.base import AnalyzeResult, VideoMeta
    from app.agent.loader import get_agent

    async def analyze(inp):
        return AnalyzeResult(summary_text="요약", facts={"impact_part": "x", "my_signal": "green"}, questions=[], title="바로 판정 · 08-22", video_meta=VideoMeta())

    monkeypatch.setattr(get_agent()._impl, "analyze", analyze)
    async with session_scope() as db:
        await add_message(db, case_id, "user", "text", {"text": "설명"}, publish=False)
    await upload()
    await runner.wait_all()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged" and detail["verdict"]["ratio"] == {"mine": 0, "other": 100}
    assert detail["stages"]["fault_ratio"] == {"state": "done"}


async def test_perform_verdict_versions_and_cards(client, auth_headers, case_id, sse):
    tap = await sse(case_id)
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
        v1 = await perform_verdict(db, case_id)
        assert v1.version == 1 and v1.is_active
        a = await db.get(Analysis, case_id)
        a.facts = {"opponent_signal": "yellow"}
        await db.commit()
        v2 = await perform_verdict(db, case_id)
        assert v2.version == 2 and v2.change_reason and (v2.ratio_mine, v2.ratio_other) == (20, 80)
        await db.refresh(v1)
        assert v1.is_active is False
        assert v2.basis["precedents"][0]["body_text"]

    frames = await tap.take(4)
    assert "event: message.created" in frames[0] and '"type": "verdict"' in frames[0] and '"version": 1' in frames[0]
    assert "body_text" not in frames[0] and '"canCreateReport": true' in frames[0]
    assert '"status": "judged"' in frames[1]
    assert '"version": 2' in frames[2] and '"changeReason": "상대' in frames[2]
    assert '"ratio": {"mine": 20, "other": 80}' in frames[3]


async def test_analysis_failure_restores_status_and_posts_error_card(client, auth_headers, case_id, upload, settle, monkeypatch):
    from app.agent.loader import get_agent
    from app.errors import ERROR_CATALOG
    from app.models import Job

    async def boom(inp):
        raise RuntimeError("agent down")

    monkeypatch.setattr(get_agent()._impl, "analyze", boom)
    async with session_scope() as db:
        await add_message(db, case_id, "user", "text", {"text": "교차로에서 오토바이가 박았어요"}, publish=False)
    assert (await upload()).json()["analysis"]["started"] is True
    await settle()

    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "intake"
    assert detail["activeJob"] is None
    assert detail["stages"]["analysis"] == {"state": "pending"}

    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["type"] == "text" and msgs[-1]["role"] == "assistant"
    assert msgs[-1]["payload"]["text"] == ERROR_CATALOG["INTERNAL_ERROR"].message
    assert msgs[-1]["payload"]["cta"] is None  # 영상은 이미 있으니 업로드 CTA는 붙이지 않는다

    async with session_scope() as db:
        job = (await db.execute(select(Job).where(Job.case_id == case_id))).scalars().one()
        assert job.kind == "analysis" and job.status == "failed"


async def test_analysis_failure_is_retried_by_the_next_message(client, auth_headers, case_id, upload, settle, monkeypatch):
    from app.agent.loader import get_agent

    original = get_agent()._impl.analyze
    calls = {"n": 0}

    async def flaky(inp):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("agent down")
        return await original(inp)

    monkeypatch.setattr(get_agent()._impl, "analyze", flaky)
    async with session_scope() as db:
        await add_message(db, case_id, "user", "text", {"text": "교차로에서 오토바이가 박았어요"}, publish=False)
    await upload()
    await settle()

    res = await client.post(f"/cases/{case_id}/messages", json={"text": "다시 해 주세요"}, headers=auth_headers)
    assert res.status_code == 202
    await settle()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "needs_review" and calls["n"] == 2
