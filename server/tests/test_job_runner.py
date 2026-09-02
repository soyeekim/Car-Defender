import asyncio

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.errors import ApiError
from app.jobs.runner import JobRunner
from app.models import Job
from app.sse.hub import hub


async def test_runner_runs_handler_and_marks_succeeded(app, case_id):
    runner = JobRunner()
    seen = {}

    async def handler(db, cid, job_id):
        seen["case"] = cid
        seen["job"] = job_id

    it = hub.subscribe(case_id)
    await it.__anext__()
    async with session_scope() as db:
        job = await runner.start(db, case_id, "analysis", handler)
        assert job.status == "running"
    frame = await it.__anext__()
    assert "event: case.updated" in frame and '"kind": "analysis"' in frame
    await runner.wait_all()
    assert seen == {"case": case_id, "job": job.id}
    async with session_scope() as db:
        assert (await db.get(Job, job.id)).status == "succeeded"
    frame = await it.__anext__()
    assert "event: case.updated" in frame and '"activeJob": null' in frame
    await it.aclose()


async def test_runner_rejects_second_job_while_running(app, case_id):
    runner = JobRunner()
    gate = asyncio.Event()

    async def handler(db, cid, job_id):
        await gate.wait()

    async with session_scope() as db:
        await runner.start(db, case_id, "analysis", handler)
        with pytest.raises(ApiError) as ei:
            await runner.start(db, case_id, "verdict", handler)
        assert ei.value.code == "JOB_ALREADY_RUNNING"
    gate.set()
    await runner.wait_all()


async def test_runner_start_is_race_safe_for_same_case(app, case_id):
    runner = JobRunner()
    gate = asyncio.Event()

    async def handler(db, cid, job_id):
        await gate.wait()

    async with session_scope() as db1, session_scope() as db2:
        results = await asyncio.gather(
            runner.start(db1, case_id, "analysis", handler),
            runner.start(db2, case_id, "verdict", handler),
            return_exceptions=True,
        )
    successes = [r for r in results if isinstance(r, Job)]
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ApiError) and errors[0].code == "JOB_ALREADY_RUNNING"
    async with session_scope() as db:
        rows = (await db.execute(select(Job).where(Job.case_id == case_id))).scalars().all()
        assert len(rows) == 1
    gate.set()
    await runner.wait_all()


async def test_runner_marks_failed_and_publishes_case_updated(app, case_id):
    runner = JobRunner()

    async def handler(db, cid, job_id):
        raise RuntimeError("boom")

    it = hub.subscribe(case_id)
    await it.__anext__()
    async with session_scope() as db:
        job = await runner.start(db, case_id, "verdict", handler)
    await it.__anext__()  # start
    await runner.wait_all()
    frame = await it.__anext__()
    assert '"activeJob": null' in frame
    async with session_scope() as db:
        j = await db.get(Job, job.id)
        assert j.status == "failed" and j.error["message"] == "boom" and j.ended_at is not None
    await it.aclose()


async def test_cleanup_stale(app, case_id):
    runner = JobRunner()
    gate = asyncio.Event()

    async def handler(db, cid, job_id):
        await gate.wait()

    async with session_scope() as db:
        await runner.start(db, case_id, "report", handler)
    await JobRunner().cleanup_stale()
    async with session_scope() as db:
        rows = (await db.execute(select(Job))).scalars().all()
        assert all(r.status == "failed" for r in rows)
        job_id = rows[0].id
    gate.set()
    await runner.wait_all()
    async with session_scope() as db:
        assert (await db.get(Job, job_id)).status == "failed"
