from datetime import timedelta

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc, to_kst_iso
from app.config import get_settings
from app.errors import ERROR_CATALOG, ApiError
from app.ids import new_id
from app.models import RefreshToken, User
from app.schemas.auth import AgreementOut, MeResponse, SignupRequest, UserOut
from app.security import (
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    is_email,
    password_policy_ok,
    verify_password,
)

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def find_user_by_email(db: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == normalize_email(email))
    return (await db.execute(stmt)).scalar_one_or_none()


async def email_available(db: AsyncSession, email: str) -> tuple[bool, str | None]:
    if not is_email(email):
        raise ApiError("AUTH_EMAIL_FORMAT", fields={"email": ERROR_CATALOG["AUTH_EMAIL_FORMAT"].message})
    if await find_user_by_email(db, email) is not None:
        return False, ERROR_CATALOG["AUTH_EMAIL_DUPLICATED"].message
    return True, None


async def signup(db: AsyncSession, body: SignupRequest) -> User:
    fields: dict[str, str] = {}
    code: str | None = None

    def fail(c: str, field: str) -> None:
        nonlocal code
        fields[field] = ERROR_CATALOG[c].message
        code = code or c

    if not is_email(body.email):
        fail("AUTH_EMAIL_FORMAT", "email")
    elif await find_user_by_email(db, body.email) is not None:
        fail("AUTH_EMAIL_DUPLICATED", "email")
    if not password_policy_ok(body.password):
        fail("AUTH_PASSWORD_POLICY", "password")
    if body.password != body.password_confirm:
        fail("AUTH_PASSWORD_MISMATCH", "passwordConfirm")
    a = body.agreements
    if not (a.terms_of_service and a.privacy and a.video_consent):
        fail("AGREEMENT_REQUIRED", "agreements")
    if code:
        raise ApiError(code, fields=fields)

    now = now_utc()
    user = User(
        id=new_id(),
        email=normalize_email(body.email),
        password_hash=hash_password(body.password),
        agreed_terms_at=now,
        agreed_privacy_at=now,
        agreed_video_at=now,
        created_at=now,
    )
    db.add(user)
    await db.commit()
    return user


async def issue_tokens(db: AsyncSession, user: User) -> tuple[str, str]:
    settings = get_settings()
    raw = generate_opaque_token()
    db.add(
        RefreshToken(
            id=new_id(),
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
            created_at=now_utc(),
        )
    )
    await db.commit()
    return create_access_token(user.id), raw


def set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        raw,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, onboarded_at=to_kst_iso(user.onboarded_at), is_demo=user.is_demo)


def access_expires_in() -> int:
    return get_settings().access_token_minutes * 60


async def login(db: AsyncSession, email: str, password: str) -> User:
    user = await find_user_by_email(db, email) if is_email(email) else None
    if user is None or not verify_password(password, user.password_hash):
        raise ApiError(
            "AUTH_INVALID_CREDENTIALS",
            fields={"password": ERROR_CATALOG["AUTH_INVALID_CREDENTIALS"].message},
        )
    return user


async def _find_refresh(db: AsyncSession, raw: str | None) -> RefreshToken | None:
    if not raw:
        return None
    stmt = select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
    return (await db.execute(stmt)).scalar_one_or_none()


async def refresh_access(db: AsyncSession, raw: str | None) -> User:
    if not raw:
        raise ApiError("UNAUTHORIZED")
    rt = await _find_refresh(db, raw)
    if rt is None:
        raise ApiError("UNAUTHORIZED")
    now = now_utc()
    expires = rt.expires_at if rt.expires_at.tzinfo else rt.expires_at.replace(tzinfo=now.tzinfo)
    if rt.revoked_at is not None or expires < now:
        raise ApiError("TOKEN_EXPIRED")
    user = await db.get(User, rt.user_id)
    if user is None:
        raise ApiError("UNAUTHORIZED")
    return user


async def revoke_refresh(db: AsyncSession, raw: str | None) -> None:
    rt = await _find_refresh(db, raw)
    if rt is not None and rt.revoked_at is None:
        rt.revoked_at = now_utc()
        await db.commit()


def me_response(user: User) -> MeResponse:
    def ag(dt):
        return AgreementOut(agreed=dt is not None, agreed_at=to_kst_iso(dt))

    return MeResponse(
        id=user.id,
        email=user.email,
        onboarded_at=to_kst_iso(user.onboarded_at),
        is_demo=user.is_demo,
        agreements={
            "termsOfService": ag(user.agreed_terms_at),
            "privacy": ag(user.agreed_privacy_at),
            "videoConsent": ag(user.agreed_video_at),
        },
    )
