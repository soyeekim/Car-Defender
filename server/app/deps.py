from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.errors import ApiError
from app.models import User
from app.security import decode_access_token


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


async def current_user(
    token: str | None = Depends(bearer_token), db: AsyncSession = Depends(get_db)
) -> User:
    if not token:
        raise ApiError("UNAUTHORIZED")
    user_id = decode_access_token(token)
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError("UNAUTHORIZED")
    return user


async def owned_case(case_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.services.cases import get_owned_case

    return await get_owned_case(db, user, case_id)


def client_ip(request: Request) -> str:
    # X-Forwarded-For 의 마지막 항목만 신뢰한다: 우리 nginx가 프록시로서
    # 실제 접속 IP를 이 위치에 덧붙이므로, 앞쪽 항목은 클라이언트가 스스로
    # 지어낸 값일 수 있어 레이트리밋 우회에 악용될 수 있다.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"
