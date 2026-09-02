from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.runner import runner
from app.models import Case, Job
from app.services.verdict import perform_verdict


async def run_verdict(db: AsyncSession, case_id: str, job_id: str) -> None:
    await perform_verdict(db, case_id)


async def start_verdict(db: AsyncSession, case: Case) -> Job:
    return await runner.start(db, case.id, "verdict", run_verdict)
