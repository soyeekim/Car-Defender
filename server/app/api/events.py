from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.db import session_scope
from app.deps import bearer_token
from app.errors import ApiError
from app.models import User
from app.security import decode_access_token
from app.services.cases import get_owned_case
from app.sse.hub import hub

router = APIRouter(tags=["events"])


@router.get("/cases/{case_id}/events")
async def events(
    case_id: str,
    request: Request,
    token: str | None = Depends(bearer_token),
    access_token: str | None = Query(default=None),
):
    raw = token or access_token
    if not raw:
        raise ApiError("UNAUTHORIZED")
    # 인증·소유 확인에만 짧게 DB를 쓰고 세션을 닫는다. 스트림은 몇 분씩 살아 있어서
    # Depends(get_db) 로 세션을 잡으면 구독자 수만큼 커넥션 풀이 고갈된다.
    async with session_scope() as db:
        user = await db.get(User, decode_access_token(raw))
        if user is None:
            raise ApiError("UNAUTHORIZED")
        await get_owned_case(db, user, case_id)
    stream = hub.subscribe(case_id, request.headers.get("last-event-id"))
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)
