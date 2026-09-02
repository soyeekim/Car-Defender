from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc, to_kst_iso
from app.db import get_db
from app.deps import current_user
from app.models import User
from app.schemas.auth import OnboardingRequest, OnboardingResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me/onboarding", response_model=OnboardingResponse)
async def onboarding(body: OnboardingRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if body.completed or body.skipped:
        user.onboarded_at = now_utc()
        await db.commit()
    return OnboardingResponse(onboarded_at=to_kst_iso(user.onboarded_at))
