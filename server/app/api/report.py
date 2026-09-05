from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import bearer_token, current_user, owned_case
from app.errors import ApiError
from app.models import Case, Report
from app.schemas.report import RevisionRequest
from app.security import decode_download_token
from app.services import report as report_service
from app.services.cases import get_owned_case
from app.storage import get_storage

router = APIRouter(prefix="/cases/{case_id}/report", tags=["report"])


async def report_for_download(
    case_id: str, version: str, t: str | None = Query(None), token: str | None = Depends(bearer_token), db: AsyncSession = Depends(get_db)
) -> Report:
    """브라우저가 <a href>/window.open 으로 여는 요청에는 Authorization 헤더가 실리지 않는다.

    그래서 downloadUrl 에 담긴 단기 토큰(?t=)을 대신 받는다 — 영상 stream 과 같은 방식이다.
    토큰은 report 하나에만 묶여 있어 다른 버전·다른 사건의 PDF 는 열 수 없다. API 클라이언트는 여전히 Bearer 로 온다.
    """
    if t is not None:
        report_id = decode_download_token(t)
        report = await report_service.report_by_version(db, case_id, version)
        if report.id != report_id:
            raise ApiError("FORBIDDEN")
        return report
    user = await current_user(token, db)
    case = await get_owned_case(db, user, case_id)
    return await report_service.report_by_version(db, case.id, version)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_report(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    job, to_version, _ = await report_service.start_report(db, case)
    return {"jobId": job.id, "kind": "report", "status": job.status, "version": to_version}


@router.post("/revisions", status_code=status.HTTP_202_ACCEPTED)
async def revise(body: RevisionRequest, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    job, to_version, from_version = await report_service.start_report(db, case, body.request)
    return {"jobId": job.id, "kind": "report", "status": job.status, "fromVersion": from_version, "toVersion": to_version}


@router.get("/versions")
async def versions(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return await report_service.versions_list(db, case.id)


@router.get("/versions/{version}")
async def full(version: str, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return report_service.full_text(await report_service.report_by_version(db, case.id, version))


@router.post("/versions/{version}/pdf")
async def create_pdf(version: str, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)):
    report = await report_service.report_by_version(db, case.id, version)
    pdf, created = await report_service.ensure_pdf(db, case, report)
    return JSONResponse(status_code=201 if created else 200, content=report_service.pdf_response_dict(case, report, pdf))


@router.get("/versions/{version}/pdf")
async def download_pdf(report: Report = Depends(report_for_download), db: AsyncSession = Depends(get_db)):
    pdf = await report_service.pdf_for(db, report.id)
    if pdf is None:
        raise ApiError("NOT_FOUND")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(pdf.filename)}",
        "Content-Length": str(pdf.size_bytes),
    }
    return StreamingResponse(get_storage().read_range(pdf.storage_key, 0, pdf.size_bytes - 1), media_type="application/pdf", headers=headers)
