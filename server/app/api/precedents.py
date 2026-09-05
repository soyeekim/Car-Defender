from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user
from app.errors import ApiError
from app.models import Case, User, Verdict
from app.security import decode_precedent_image_token
from app.services.cases import active_verdict, get_owned_case
from app.services.precedent_images import IMAGE_CAPTION, image_file, image_url

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
    url = image_url(precedent_id)
    return {
        "precedentId": precedent_id,
        "title": f"심의사례 {precedent_id}",
        "bodyText": found.get("body_text", ""),
        # 원본 PDF 의 '사례 개요' 표(심의사례) 또는 도표(인정기준)를 잘라 둔 PNG. 없으면 null → 글만 보여 준다.
        "imageUrl": url,
        "imageCaption": IMAGE_CAPTION if url else None,
    }


@router.get("/precedents/{precedent_id}/image")
async def precedent_image(precedent_id: str, t: str = Query(...)):
    """<img src> 로 열리므로 Authorization 헤더 대신 URL 의 서명(t)으로 확인한다 (영상 스트림과 같은 방식, 10분 유효)."""
    if decode_precedent_image_token(t) != precedent_id:
        raise ApiError("FORBIDDEN")
    path = image_file(precedent_id)
    if path is None:
        raise ApiError("NOT_FOUND")
    return FileResponse(
        path, media_type="image/png",
        headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"},
    )
