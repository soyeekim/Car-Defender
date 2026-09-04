from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user
from app.errors import ApiError
from app.models import Case, User, Verdict
from app.services.cases import active_verdict, get_owned_case

router = APIRouter(tags=["verdict"])


def _find(verdicts: list[Verdict], precedent_id: str) -> dict | None:
    for v in verdicts:
        for p in (v.basis or {}).get("precedents", []):
            if p.get("id") == precedent_id:
                return p
    return None


@router.get("/precedents/{precedent_id}")
async def precedent(
    precedent_id: str,
    case_id: str | None = Query(default=None, alias="caseId"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if case_id:
        await get_owned_case(db, user, case_id)
        v = await active_verdict(db, case_id)
        verdicts = [v] if v else []
    else:
        stmt = select(Verdict).join(Case, Case.id == Verdict.case_id).where(Case.user_id == user.id, Verdict.is_active.is_(True)).order_by(Verdict.created_at.desc())
        verdicts = list((await db.execute(stmt)).scalars().all())
    found = _find(verdicts, precedent_id)
    if found is None:
        raise ApiError("NOT_FOUND")
    return {"precedentId": precedent_id, "title": f"심의사례 {precedent_id}", "bodyText": found.get("body_text", "")}
