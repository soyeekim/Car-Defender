from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, owned_case
from app.errors import ApiError
from app.models import Case, User
from app.schemas.rebuttal import RebuttalPatch
from app.services import cases as case_service
from app.services import rebuttal as rebuttal_service

router = APIRouter(prefix="/cases/{case_id}/rebuttal", tags=["rebuttal"])

MAX_IDEMPOTENCY_KEY_LENGTH = 64  # send_logs.idempotency_key 칼럼 길이


async def _rebuttal_or_404(db, case_id):
    r = await case_service.get_rebuttal(db, case_id)
    if r is None:
        raise ApiError("NOT_FOUND")
    return r


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    job = await rebuttal_service.start_rebuttal(db, case)
    return {"jobId": job.id, "kind": "rebuttal", "status": job.status}


@router.get("")
async def get(case: Case = Depends(owned_case), user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await rebuttal_service.view(db, case, await _rebuttal_or_404(db, case.id), user)


@router.patch("")
async def patch(body: RebuttalPatch, case: Case = Depends(owned_case), user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    rebuttal = await rebuttal_service.apply_patch(db, await _rebuttal_or_404(db, case.id), body)
    return await rebuttal_service.view(db, case, rebuttal, user)


@router.post("/send")
async def send(
    case: Case = Depends(owned_case), user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    # send_logs.idempotency_key는 varchar(64)다. 더 긴 키는 기록 시점에 write가 통째로 실패하니 여기서 막는다.
    if idempotency_key is not None and len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApiError("VALIDATION_FAILED", fields={"Idempotency-Key": "키가 너무 길어요 (최대 64자)."})
    rebuttal = await _rebuttal_or_404(db, case.id)
    return await rebuttal_service.send(db, case, rebuttal, user, idempotency_key)


@router.get("/sends")
async def sends(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return await rebuttal_service.send_logs(db, case.id)
