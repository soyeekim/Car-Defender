from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import bearer_token
from app.errors import ApiError
from app.models import User
from app.security import decode_access_token
from app.services.cases import get_owned_case
from app.sse.hub import hub

router = APIRouter(tags=["events"])


async def _user_from_header_or_query(
    token: str | None = Depends(bearer_token),
    access_token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    raw = token or access_token
    if not raw:
        raise ApiError("UNAUTHORIZED")
    user = await db.get(User, decode_access_token(raw))
    if user is None:
        raise ApiError("UNAUTHORIZED")
    return user


@router.get("/cases/{case_id}/events")
async def events(
    case_id: str,
    request: Request,
    user: User = Depends(_user_from_header_or_query),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_case(db, user, case_id)
    last_event_id = request.headers.get("last-event-id")
    stream = hub.subscribe(case_id, last_event_id)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)
