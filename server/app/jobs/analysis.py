from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import AnalyzeInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.jobs.runner import runner
from app.models import Analysis, Case, Job, Message
from app.services import cases as case_service
from app.services.verdict import perform_verdict
from app.services.video import attachment_payload
from app.storage import get_storage


async def start_analysis(db: AsyncSession, case: Case) -> Job:
    case_service.set_status(case, "analyzing")
    await db.commit()
    return await runner.start(db, case.id, "analysis", run_analysis)


async def run_analysis(db: AsyncSession, case_id: str, job_id: str) -> None:
    case = await db.get(Case, case_id)
    video = await case_service.get_video(db, case_id)
    if case is None or video is None:
        raise RuntimeError("분석할 영상이 없어요")

    stmt = select(Message).where(Message.case_id == case_id, Message.role == "user", Message.type == "text").order_by(Message.id)
    description = "\n".join(m.payload.get("text", "") for m in (await db.execute(stmt)).scalars().all())

    async with get_storage().local_path(video.storage_key) as path:
        result = await get_agent().analyze(AnalyzeInput(video_path=str(path), video_mime=video.mime_type, description=description))

    now = now_utc()
    analysis = await db.get(Analysis, case_id)
    if analysis is None:
        analysis = Analysis(case_id=case_id, summary_text=result.summary_text, facts=result.facts, questions=result.questions, created_at=now, updated_at=now)
        db.add(analysis)
    else:
        analysis.summary_text = result.summary_text
        analysis.facts = {**(analysis.facts or {}), **result.facts}
        analysis.questions = result.questions
        analysis.updated_at = now
    case.title = result.title[:60] if result.title else case.title
    if result.video_meta:
        video.meta = {"speedKph": result.video_meta.speed_kph, "impactAtSec": result.video_meta.impact_at_sec}
    await db.commit()

    await case_service.assistant_text(db, case_id, result.summary_text)
    if result.questions:
        await case_service.assistant_text(db, case_id, result.questions[0])

    stmt = select(Message).where(Message.case_id == case_id, Message.type == "video_attachment").order_by(Message.id.desc())
    card = (await db.execute(stmt)).scalars().first()
    if card is not None and card.payload.get("videoId") == video.id:
        await case_service.update_message(db, card, attachment_payload(video))

    if result.questions:
        case_service.set_status(case, "needs_review")
        await db.commit()
        await case_service.publish_case_updated(db, case_id)
    else:
        await perform_verdict(db, case_id)
