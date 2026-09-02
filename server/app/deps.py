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


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
