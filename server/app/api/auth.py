from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas.auth import AuthResponse, EmailAvailableResponse, SignupRequest
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=AuthResponse)
async def signup(body: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    user = await auth_service.signup(db, body)
    access, refresh = await auth_service.issue_tokens(db, user)
    auth_service.set_refresh_cookie(response, refresh)
    return AuthResponse(user=auth_service.user_out(user), access_token=access, expires_in=auth_service.access_expires_in())


@router.get("/email-available", response_model=EmailAvailableResponse)
async def email_available(email: str = Query(...), db: AsyncSession = Depends(get_db)):
    available, reason = await auth_service.email_available(db, email)
    return EmailAvailableResponse(available=available, reason=reason)
