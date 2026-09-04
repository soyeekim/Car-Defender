import logging
from datetime import timedelta

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import ensure_aware, now_utc, to_kst_iso
from app.config import get_settings
from app.errors import ERROR_CATALOG, ApiError
from app.ids import new_id
from app.mail import MailMessage, MailSendError, get_mailer
from app.models import PasswordResetToken, RefreshToken, User
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

log = logging.getLogger(__name__)

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
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
    )


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, onboarded_at=to_kst_iso(user.onboarded_at), is_demo=user.is_demo)


def access_expires_in() -> int:
    return get_settings().access_token_minutes * 60


_DUMMY_HASH = hash_password("fairway-dummy-password-0")


async def login(db: AsyncSession, email: str, password: str) -> User:
    user = await find_user_by_email(db, email) if is_email(email) else None
    hashed = user.password_hash if user is not None else _DUMMY_HASH
    ok = verify_password(password, hashed)
    if user is None or not ok:
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
    expires = ensure_aware(rt.expires_at)
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


RESET_TOKEN_MINUTES = 30


async def request_password_reset(db: AsyncSession, email: str) -> None:
    if not is_email(email):
        raise ApiError("AUTH_EMAIL_FORMAT", fields={"email": ERROR_CATALOG["AUTH_EMAIL_FORMAT"].message})
    user = await find_user_by_email(db, email)
    if user is None:
        return
    raw = generate_opaque_token()
    db.add(
        PasswordResetToken(
            id=new_id(),
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=now_utc() + timedelta(minutes=RESET_TOKEN_MINUTES),
            created_at=now_utc(),
        )
    )
    await db.commit()
    link = f"{get_settings().front_base_url.rstrip('/')}/reset?token={raw}"
    body = (
        "Fairway 비밀번호 재설정 링크예요. 30분 안에 아래 주소를 열어 새 비밀번호를 정해 주세요.\n\n"
        f"{link}\n\n"
        "본인이 요청하지 않았다면 이 메일은 무시해도 돼요."
    )
    try:
        await get_mailer().send(MailMessage(to=user.email, subject="[Fairway] 비밀번호 재설정", body_text=body))
    except MailSendError:
        log.exception("password reset mail send failed for user_id=%s", user.id)


async def confirm_password_reset(db: AsyncSession, token: str, password: str, confirm: str) -> None:
    stmt = select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token or ""))
    prt = (await db.execute(stmt)).scalar_one_or_none()
    now = now_utc()
    if prt is None or prt.used_at is not None:
        raise ApiError("RESET_TOKEN_INVALID")
    expires = ensure_aware(prt.expires_at)
    if expires < now:
        raise ApiError("RESET_TOKEN_INVALID")
    if not password_policy_ok(password):
        raise ApiError("AUTH_PASSWORD_POLICY", fields={"password": ERROR_CATALOG["AUTH_PASSWORD_POLICY"].message})
    if password != confirm:
        raise ApiError("AUTH_PASSWORD_MISMATCH", fields={"passwordConfirm": ERROR_CATALOG["AUTH_PASSWORD_MISMATCH"].message})
    user = await db.get(User, prt.user_id)
    if user is None:
        raise ApiError("RESET_TOKEN_INVALID")
    user.password_hash = hash_password(password)
    prt.used_at = now
    revoke_stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(revoke_stmt)
    await db.commit()
