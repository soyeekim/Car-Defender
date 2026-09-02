import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc
from app.config import get_settings
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
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def start(self, db: AsyncSession, case_id: str, kind: str, handler: JobHandler) -> Job:
        # 락 순서는 항상 채팅 락 → Job 락. 반대로 잡으면 교착한다.
        # 임계 구역은 "실행 중 Job 확인 + INSERT + commit" 까지만이고,
        # 발행과 태스크 생성은 락을 놓은 뒤에 한다.
        async with self._locks[case_id]:
            if await active_job(db, case_id) is not None:
                raise ApiError("JOB_ALREADY_RUNNING")
            job = Job(id=new_id(), case_id=case_id, kind=kind, status="running", started_at=now_utc())
            db.add(job)
            await db.commit()
        # publish가 실패해도 task-less한 running Job을 남기지 않도록, 실패는 삼키고 태스크 생성으로 이어간다.
        try:
            await publish_case_updated(db, case_id)
        except Exception:  # noqa: BLE001
            log.exception("job %s (%s) 시작 알림 발행 실패 (case %s)", job.id, kind, case_id)
        task = asyncio.create_task(self._run(job.id, case_id, kind, handler), name=f"job:{kind}:{job.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job_id: str, case_id: str, kind: str, handler: JobHandler) -> None:
        try:
            async with session_scope() as db:
                await asyncio.wait_for(handler(db, case_id, job_id), timeout=get_settings().job_timeout_seconds)
            async with session_scope() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "running")
                    .values(status="succeeded", ended_at=now_utc())
                )
                await db.commit()
        except Exception as e:  # noqa: BLE001  타임아웃(TimeoutError) 포함
            log.exception("job %s (%s) failed for case %s", job_id, kind, case_id)
            async with session_scope() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "running")
                    .values(status="failed", ended_at=now_utc(), error={"type": type(e).__name__, "message": str(e)})
                )
                await db.commit()
        # 발행 실패가 "job failed" 로 잘못 기록되지 않도록 성공·실패 경로 밖에서 한 번만 알린다.
        try:
            async with session_scope() as db:
                await publish_case_updated(db, case_id)
        except ApiError:
            pass  # 사건이 삭제된 경우
        except Exception:  # noqa: BLE001
            log.exception("job %s (%s) case.updated 발행 실패 (case %s)", job_id, kind, case_id)

    async def wait_all(self, timeout: float | None = None) -> None:
        if timeout is None:
            while self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            return
        tasks = list(self._tasks)
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def reset(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._locks.clear()

    async def cleanup_stale(self) -> None:
        async with session_scope() as db:
            stmt = select(Job).where(Job.status.in_(("queued", "running")))
            for job in (await db.execute(stmt)).scalars().all():
                job.status = "failed"
                job.ended_at = now_utc()
                job.error = {"type": "Restart", "message": "서버 재시작으로 중단됐어요"}
            await db.commit()


runner = JobRunner()
