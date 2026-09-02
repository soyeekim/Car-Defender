from urllib.parse import quote

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import owned_case
from app.errors import ApiError
from app.models import Case
from app.schemas.report import RevisionRequest
from app.services import report as report_service
from app.storage import get_storage

router = APIRouter(prefix="/cases/{case_id}/report", tags=["report"])


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
async def download_pdf(version: str, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)):
    report = await report_service.report_by_version(db, case.id, version)
    pdf = await report_service.pdf_for(db, report.id)
    if pdf is None:
        raise ApiError("NOT_FOUND")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(pdf.filename)}",
        "Content-Length": str(pdf.size_bytes),
    }
    return StreamingResponse(get_storage().read_range(pdf.storage_key, 0, pdf.size_bytes - 1), media_type="application/pdf", headers=headers)
