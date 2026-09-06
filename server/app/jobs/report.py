import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import Section, WriteInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.ids import new_id
from app.jobs.runner import JobHandler
from app.models import Case, Report
from app.services import cases as case_service
from app.services.report import draft_card, draft_payload, ensure_pdf
from app.services.verdict import recent_turns, snapshot

log = logging.getLogger(__name__)


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

        # Agent 의 page_count 는 추정치다. 여기서 PDF 를 바로 렌더링해 실측 페이지 수로 바꿔 둔다 —
        # 카드·전문·현황판이 처음부터 같은 실측값을 보여 주고, 다운로드·발송도 즉시 된다.
        # 렌더링이 실패하면 추정치를 그대로 두고, 다운로드·발송 시점에 다시 시도한다(그때 카드도 맞춰진다).
        try:
            await ensure_pdf(db, case, report)
        except Exception:
            log.exception("경위서 PDF 선렌더링 실패: case=%s version=%s", case_id, report.version)

        card = await draft_card(db, case_id)
        if card is None:
            await case_service.add_message(db, case_id, "assistant", "report_draft", draft_payload(report))
        else:
            await case_service.update_message(db, card, draft_payload(report))

    return run_report
