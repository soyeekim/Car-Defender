from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import Section, WriteInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.ids import new_id
from app.models import Case, Rebuttal
from app.services import cases as case_service
from app.services.rebuttal import (
    auto_subject,
    default_attachments,
    draft_card,
    draft_payload,
    resolve_attachments,
)
from app.services.verdict import recent_turns, snapshot


async def run_rebuttal(db: AsyncSession, case_id: str, job_id: str) -> None:
    case = await db.get(Case, case_id)
    verdict = await case_service.active_verdict(db, case_id)
    analysis = await case_service.get_analysis(db, case_id)
    report = await case_service.latest_report(db, case_id)
    result = await get_agent().write(WriteInput(
        kind="rebuttal", messages=await recent_turns(db, case_id), facts=analysis.facts if analysis else {},
        verdict=snapshot(verdict), revision_request=None, previous_sections=None,
        report_sections=[Section(**s) for s in report.sections] if report else None,
    ))
    if not result.body:
        raise RuntimeError("Agent가 반박의견서 body를 돌려주지 않았어요")

    rebuttal = await case_service.get_rebuttal(db, case_id)
    now = now_utc()
    if rebuttal is None:
        rebuttal = Rebuttal(
            id=new_id(), case_id=case_id, recipient=None, claim_number=None, subject=auto_subject(None)[:200], subject_auto=True,
            body=result.body, attachments=await default_attachments(db, case), status="draft", created_at=now, updated_at=now,
        )
        db.add(rebuttal)
    else:
        rebuttal.body = result.body
        rebuttal.attachments = await default_attachments(db, case)
        rebuttal.updated_at = now
    case_service.touch(case)
    await db.commit()

    payload = draft_payload(rebuttal, await resolve_attachments(db, rebuttal))
    card = await draft_card(db, case_id)
    if card is None:
        await case_service.add_message(db, case_id, "assistant", "rebuttal_draft", payload)
    else:
        await case_service.update_message(db, card, payload)
