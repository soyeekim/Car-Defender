import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc
from app.db import session_scope
from app.errors import ApiError
from app.ids import new_id
from app.models import Job
from app.services.cases import active_job, publish_case_updated

log = logging.getLogger(__name__)

JobHandler = Callable[[AsyncSession, str, str], Awaitable[None]]


class JobRunner:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    async def start(self, db: AsyncSession, case_id: str, kind: str, handler: JobHandler) -> Job:
        if await active_job(db, case_id) is not None:
            raise ApiError("JOB_ALREADY_RUNNING")
        job = Job(id=new_id(), case_id=case_id, kind=kind, status="running", started_at=now_utc())
        db.add(job)
        await db.commit()
        await publish_case_updated(db, case_id)
        task = asyncio.create_task(self._run(job.id, case_id, kind, handler), name=f"job:{kind}:{job.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job_id: str, case_id: str, kind: str, handler: JobHandler) -> None:
        try:
            async with session_scope() as db:
                await handler(db, case_id, job_id)
            async with session_scope() as db:
                await db.execute(update(Job).where(Job.id == job_id).values(status="succeeded", ended_at=now_utc()))
                await db.commit()
                try:
                    await publish_case_updated(db, case_id)
                except ApiError:
                    pass  # 사건이 삭제된 경우
        except Exception as e:  # noqa: BLE001
            log.exception("job %s (%s) failed for case %s", job_id, kind, case_id)
            async with session_scope() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(status="failed", ended_at=now_utc(), error={"type": type(e).__name__, "message": str(e)})
                )
                await db.commit()
                try:
                    await publish_case_updated(db, case_id)
                except ApiError:
                    pass  # 사건이 삭제된 경우

    async def wait_all(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def cleanup_stale(self) -> None:
        async with session_scope() as db:
            stmt = select(Job).where(Job.status.in_(("queued", "running")))
            for job in (await db.execute(stmt)).scalars().all():
                job.status = "failed"
                job.ended_at = now_utc()
                job.error = {"type": "Restart", "message": "서버 재시작으로 중단됐어요"}
            await db.commit()


runner = JobRunner()
