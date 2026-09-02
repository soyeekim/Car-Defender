from fastapi import APIRouter, Cookie, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import client_ip, current_user
from app.models import User
from app.ratelimit import limiter
from app.schemas.auth import (
    AuthResponse,
    EmailAvailableResponse,
    LoginRequest,
    MeResponse,
    MessageResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshResponse,
    SignupRequest,
)
from app.security import create_access_token
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=AuthResponse)
async def signup(body: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    user = await auth_service.signup(db, body)
    access, refresh = await auth_service.issue_tokens(db, user)
    auth_service.set_refresh_cookie(response, refresh)
    return AuthResponse(user=auth_service.user_out(user), access_token=access, expires_in=auth_service.access_expires_in())


@router.get("/email-available", response_model=EmailAvailableResponse)
async def email_available(request: Request, email: str = Query(...), db: AsyncSession = Depends(get_db)):
    limiter.check(f"email:{client_ip(request)}", limit=30, per_seconds=60)
    available, reason = await auth_service.email_available(db, email)
    return EmailAvailableResponse(available=available, reason=reason)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    # 이메일별 버킷만으로는 이메일을 바꿔가며 무한정 시도하는 것을 막지 못하므로,
    # 같은 IP를 기준으로 한 더 넓은 버킷을 먼저 확인한다.
    limiter.check(f"login-ip:{ip}", limit=30, per_seconds=60)
    limiter.check(f"login:{ip}:{body.email.lower()}", limit=10, per_seconds=60)
    user = await auth_service.login(db, body.email, body.password)
    access, refresh = await auth_service.issue_tokens(db, user)
    auth_service.set_refresh_cookie(response, refresh)
    return AuthResponse(user=auth_service.user_out(user), access_token=access, expires_in=auth_service.access_expires_in())


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    refresh_token: str | None = Cookie(default=None, alias=auth_service.REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
):
    user = await auth_service.refresh_access(db, refresh_token)
    return RefreshResponse(access_token=create_access_token(user.id), expires_in=auth_service.access_expires_in())


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    user: User = Depends(current_user),
    refresh_token: str | None = Cookie(default=None, alias=auth_service.REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
):
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    await auth_service.revoke_refresh(db, refresh_token)
    auth_service.clear_refresh_cookie(resp)
    return resp


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(current_user)):
    return auth_service.me_response(user)


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED, response_model=MessageResponse)
async def password_reset(body: PasswordResetRequest, request: Request, db: AsyncSession = Depends(get_db)):
    limiter.check(f"reset:{body.email.lower()}", limit=3, per_seconds=60)
    await auth_service.request_password_reset(db, body.email)
    return MessageResponse(message="비밀번호 재설정 링크를 보냈어요. 메일함을 확인해 주세요.")


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def password_reset_confirm(body: PasswordResetConfirmRequest, db: AsyncSession = Depends(get_db)):
    await auth_service.confirm_password_reset(db, body.token, body.password, body.password_confirm)
    return MessageResponse(message="비밀번호를 바꿨어요. 새 비밀번호로 로그인해 주세요.")
