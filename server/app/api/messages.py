from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import owned_case
from app.models import Case, Message
from app.schemas.chat import SendMessageRequest
from app.services import chat as chat_service
from app.services.presenters import message_dict

router = APIRouter(prefix="/cases/{case_id}/messages", tags=["chat"])


@router.get("")
async def list_messages(
    before: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1),
    case: Case = Depends(owned_case),
    db: AsyncSession = Depends(get_db),
) -> dict:
    limit = min(limit, 50)
    stmt = select(Message).where(Message.case_id == case.id)
    if before:
        stmt = stmt.where(Message.id < before)
    stmt = stmt.order_by(Message.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return {
        "items": [message_dict(m) for m in rows],
        "hasMore": has_more,
        "nextCursor": rows[0].id if has_more and rows else None,
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def send_message(body: SendMessageRequest, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    msg = await chat_service.send_user_message(db, case, body.text)
    return {"message": message_dict(msg), "assistantPending": True}
