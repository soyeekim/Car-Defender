from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.runner import runner
from app.models import Case, Job
from app.services import cases as case_service


async def start_analysis(db: AsyncSession, case: Case) -> Job:
    case_service.set_status(case, "analyzing")
    await db.commit()
    return await runner.start(db, case.id, "analysis", run_analysis)


async def run_analysis(db: AsyncSession, case_id: str, job_id: str) -> None:
    raise NotImplementedError("Task 3에서 구현")
