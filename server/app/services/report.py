from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kst_date_label, now_utc, to_kst_iso
from app.content.texts import DISCLAIMER, REPORT_INTRO, REPORT_REVISION_PLACEHOLDER
from app.errors import ApiError
from app.ids import new_id
from app.models import Case, Job, Message, Report, ReportPdf
from app.pdf.report_pdf import render_report_pdf, report_pdf_filename
from app.services import cases as case_service
from app.services.actions import register_action
from app.services.presenters import ordinal_label
from app.storage import get_storage


def preview_lines(sections: list[dict], n: int = 2) -> list[str]:
    out = []
    for s in sections[:n]:
        body = s.get("body", "")
        out.append(f"{s.get('index')}. {s.get('title')} — {body[:40]}{' …' if len(body) > 40 else ''}")
    return out


def draft_payload(report: Report) -> dict:
    return {
        "reportId": report.id,
        "version": report.version,
        "versionLabel": ordinal_label(report.version),
        "pageCount": report.page_count,
        "preview": preview_lines(report.sections),
        "caveat": report.caveat,
        "canCreateRebuttal": True,
    }


def full_text(report: Report) -> dict:
    label = ordinal_label(report.version)
    return {
        "reportId": report.id,
        "version": report.version,
        "versionLabel": label,
        "dateLabel": kst_date_label(report.created_at),
        "pageCount": report.page_count,
        "intro": REPORT_INTRO.format(label=label),
        "sections": report.sections,
        "revisionPlaceholder": REPORT_REVISION_PLACEHOLDER,
        "disclaimer": DISCLAIMER,
    }


async def report_by_version(db: AsyncSession, case_id: str, version: str | int) -> Report:
    if version == "latest":
        r = await case_service.latest_report(db, case_id)
    else:
        try:
            v = int(version)
        except ValueError as e:
            raise ApiError("NOT_FOUND") from e
        r = (await db.execute(select(Report).where(Report.case_id == case_id, Report.version == v))).scalar_one_or_none()
    if r is None:
        raise ApiError("NOT_FOUND")
    return r


async def pdf_for(db: AsyncSession, report_id: str) -> ReportPdf | None:
    return (await db.execute(select(ReportPdf).where(ReportPdf.report_id == report_id))).scalar_one_or_none()


async def versions_list(db: AsyncSession, case_id: str) -> dict:
    stmt = select(Report).where(Report.case_id == case_id).order_by(Report.version.desc())
    reports = list((await db.execute(stmt)).scalars().all())
    items = []
    for r in reports:
        items.append({
            "version": r.version, "versionLabel": ordinal_label(r.version), "pageCount": r.page_count,
            "revisionRequest": r.revision_request, "hasPdf": await pdf_for(db, r.id) is not None,
            "createdAt": to_kst_iso(r.created_at),
        })
    return {"items": items, "latestVersion": reports[0].version if reports else None}


async def start_report(db: AsyncSession, case: Case, revision_request: str | None = None) -> tuple[Job, int, int | None]:
    if await case_service.active_verdict(db, case.id) is None:
        raise ApiError("REPORT_VERDICT_REQUIRED")
    latest = await case_service.latest_report(db, case.id)
    if revision_request is not None and latest is None:
        raise ApiError("NOT_FOUND")
    from app.jobs.report import make_report_handler
    from app.jobs.runner import runner

    job = await runner.start(db, case.id, "report", make_report_handler(revision_request))
    from_version = latest.version if latest else None
    return job, (from_version or 0) + 1, from_version


async def draft_card(db: AsyncSession, case_id: str) -> Message | None:
    stmt = select(Message).where(Message.case_id == case_id, Message.type == "report_draft").order_by(Message.id.desc())
    return (await db.execute(stmt)).scalars().first()


async def ensure_pdf(db: AsyncSession, case: Case, report: Report) -> tuple[ReportPdf, bool]:
    # report_id를 미리 뽑아 둔다: rollback은 세션의 객체를 모두 expire시키므로,
    # rollback 이후 report.id에 접근하면 동기 지연로딩이 걸려 MissingGreenlet이 난다.
    report_id = report.id
    existing = await pdf_for(db, report_id)
    if existing is not None:
        return existing, False
    data, pages = render_report_pdf(
        case_title=case.title, date_label=kst_date_label(report.created_at),
        version_label=ordinal_label(report.version), sections=report.sections, disclaimer=DISCLAIMER,
    )
    key = f"pdfs/{case.id}/{report_id}.pdf"
    size = await get_storage().put_bytes(key, data)
    pdf = ReportPdf(id=new_id(), report_id=report_id, storage_key=key, filename=report_pdf_filename(case.title, report.created_at), size_bytes=size, created_at=now_utc())
    db.add(pdf)
    if report.page_count != pages:
        report.page_count = pages
    try:
        await db.commit()
    except IntegrityError:
        # 동시에 두 요청이 같은 버전의 PDF를 만들면 report_id 유니크 제약이 걸린다.
        # 진 쪽은 자기 것을 버리고 먼저 커밋된 PDF를 그대로 돌려준다.
        await db.rollback()
        existing = await pdf_for(db, report_id)
        if existing is not None:
            return existing, False
        raise
    return pdf, True


def pdf_response_dict(case: Case, report: Report, pdf: ReportPdf) -> dict:
    return {
        "pdfId": pdf.id, "version": report.version, "filename": pdf.filename, "sizeBytes": pdf.size_bytes,
        "downloadUrl": f"/api/v1/cases/{case.id}/report/versions/{report.version}/pdf", "createdAt": to_kst_iso(pdf.created_at),
    }


async def _action_create_report(db: AsyncSession, case: Case) -> None:
    await start_report(db, case)


register_action("create_report", _action_create_report)
