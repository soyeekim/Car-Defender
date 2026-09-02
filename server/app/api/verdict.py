from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.texts import VERDICT_PLACEHOLDER
from app.db import get_db
from app.deps import owned_case
from app.models import Case
from app.services import cases as case_service
from app.services.presenters import verdict_payload

router = APIRouter(tags=["verdict"])


@router.get("/cases/{case_id}/verdict")
async def get_verdict(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    v = await case_service.active_verdict(db, case.id)
    if v is None:
        return {"verdict": None, "placeholder": VERDICT_PLACEHOLDER}
    return verdict_payload(v)
