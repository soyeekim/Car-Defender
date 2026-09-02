import asyncio
import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import ChatInput
from app.agent.loader import get_agent
from app.content.texts import REBUTTAL_LOCKED_CARD, UPLOAD_CTA
from app.db import session_scope
from app.errors import ERROR_CATALOG, ApiError
from app.jobs.analysis import start_analysis
from app.jobs.verdict import start_verdict
from app.models import Case, Message
from app.services import cases as case_service
from app.services.actions import run_action
from app.services.verdict import merge_facts, recent_turns, snapshot

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def pending() -> int:
    return len(_tasks)


async def wait_all() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def send_user_message(db: AsyncSession, case: Case, text: str) -> Message:
    job = await case_service.active_job(db, case.id)
    if job is not None and job.kind in ("analysis", "verdict"):
        raise ApiError("JOB_ALREADY_RUNNING")
    msg = await case_service.add_message(db, case.id, "user", "text", {"text": text})
    task = asyncio.create_task(process_user_message(case.id, msg.id), name=f"chat:{case.id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return msg


async def _assistant_text(db: AsyncSession, case_id: str, text: str) -> None:
    has_video = await case_service.get_video(db, case_id) is not None
    await case_service.add_message(db, case_id, "assistant", "text", {"text": text, "cta": None if has_video else dict(UPLOAD_CTA)})


async def process_user_message(case_id: str, message_id: str) -> None:
    async with _locks[case_id]:
        async with session_scope() as db:
            try:
                await _process(db, case_id, message_id)
            except Exception:  # noqa: BLE001
                log.exception("chat processing failed for case %s", case_id)
                try:
                    await _assistant_text(db, case_id, ERROR_CATALOG["INTERNAL_ERROR"].message)
                except Exception:  # noqa: BLE001
                    log.exception("could not even send the error card")


async def _process(db: AsyncSession, case_id: str, message_id: str) -> None:
    case = await db.get(Case, case_id)
    if case is None:
        return
    video = await case_service.get_video(db, case_id)
    analysis = await case_service.get_analysis(db, case_id)
    if video is not None and analysis is None:
        try:
            await start_analysis(db, case)
        except ApiError as e:
            log.info("analysis not started: %s", e.code)
        return

    new_msg = await db.get(Message, message_id)
    verdict = await case_service.active_verdict(db, case_id)
    result = await get_agent().chat(ChatInput(
        messages=await recent_turns(db, case_id, exclude_id=message_id),
        new_message=new_msg.payload.get("text", "") if new_msg else "",
        facts=analysis.facts if analysis else {},
        questions=analysis.questions if analysis else [],
        verdict=snapshot(verdict),
        has_video=video is not None,
        has_report=await case_service.latest_report(db, case_id) is not None,
    ))
    await merge_facts(db, case_id, result.fact_updates)
    await _assistant_text(db, case_id, result.reply)

    if result.next_action == "none":
        return
    try:
        if result.next_action in ("verdict", "rejudge"):
            await start_verdict(db, case)
        else:
            await run_action(result.next_action, db, case)
    except ApiError as e:
        if e.code == "REBUTTAL_LOCKED":
            missing = (e.fields or {}).get("missing", "report")
            payload = {**REBUTTAL_LOCKED_CARD, "missing": [m for m in str(missing).split(",") if m]}
            await case_service.add_message(db, case_id, "assistant", "rebuttal_locked", payload)
        else:
            await _assistant_text(db, case_id, e.message or ERROR_CATALOG[e.code].message)
