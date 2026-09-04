from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import Section, WriteInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.ids import new_id
from app.jobs.runner import JobHandler
from app.models import Case, Report
from app.services import cases as case_service
from app.services.report import draft_card, draft_payload
from app.services.verdict import recent_turns, snapshot


def make_report_handler(revision_request: str | None) -> JobHandler:
    async def run_report(db: AsyncSession, case_id: str, job_id: str) -> None:
        verdict = await case_service.active_verdict(db, case_id)
        analysis = await case_service.get_analysis(db, case_id)
        latest = await case_service.latest_report(db, case_id)
        result = await get_agent().write(WriteInput(
            kind="report",
            messages=await recent_turns(db, case_id),
            facts=analysis.facts if analysis else {},
            verdict=snapshot(verdict),
            revision_request=revision_request,
            previous_sections=[Section(**s) for s in latest.sections] if latest else None,
            report_sections=None,
        ))
        if not result.sections:
            raise RuntimeError("Agent가 경위서 sections를 돌려주지 않았어요")
        report = Report(
            id=new_id(), case_id=case_id, version=(latest.version + 1) if latest else 1,
            sections=[s.model_dump() for s in result.sections], caveat=result.caveat,
            page_count=result.page_count or 1, revision_request=revision_request, created_at=now_utc(),
        )
        db.add(report)
        case = await db.get(Case, case_id)
        case_service.touch(case)
        await db.commit()

        card = await draft_card(db, case_id)
        if card is None:
            await case_service.add_message(db, case_id, "assistant", "report_draft", draft_payload(report))
        else:
            await case_service.update_message(db, card, draft_payload(report))

    return run_report
