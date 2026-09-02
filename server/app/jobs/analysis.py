import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import AnalyzeInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.errors import ERROR_CATALOG, ApiError
from app.jobs.runner import runner
from app.models import Analysis, Case, Job, Message
from app.services import cases as case_service
from app.services.verdict import perform_verdict
from app.services.video import attachment_payload
from app.storage import get_storage

log = logging.getLogger(__name__)


async def start_analysis(db: AsyncSession, case: Case) -> Job:
    previous = case.status
    case_service.set_status(case, "analyzing")
    await db.commit()
    try:
        return await runner.start(db, case.id, "analysis", run_analysis)
    except ApiError:
        # Job을 못 띄웠으면 "분석중" 스피너만 남는다. 원래 상태로 되돌리고 실패를 그대로 올린다.
        case_service.set_status(case, previous)
        await db.commit()
        raise


async def _restore_after_failure(db: AsyncSession, case_id: str, previous: str) -> None:
    """분석이 실패했을 때 스피너를 걷고 오류 카드를 남긴다. 여기서 또 실패해도 원래 예외를 가리지 않는다."""
    try:
        await db.rollback()
        case = await db.get(Case, case_id)
        if case is None:
            return  # 사건이 지워졌으면 남길 곳도 없다
        case_service.set_status(case, previous)
        await db.commit()
        await case_service.assistant_text(db, case_id, ERROR_CATALOG["INTERNAL_ERROR"].message)
    except Exception:  # noqa: BLE001
        log.exception("분석 실패 뒷정리에 실패했어요 (case %s)", case_id)


async def run_analysis(db: AsyncSession, case_id: str, job_id: str) -> None:
    case = await db.get(Case, case_id)
    previous = case.status if case is not None else "intake"
    if previous == "analyzing":
        previous = "intake"  # 분석 전 상태로 돌아간다
    try:
        await _run_analysis(db, case_id, job_id)
    except Exception:
        await _restore_after_failure(db, case_id, previous)
        raise  # 러너가 Job을 failed 로 기록하게 둔다


async def _run_analysis(db: AsyncSession, case_id: str, job_id: str) -> None:
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
