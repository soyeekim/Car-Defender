from fastapi import APIRouter

from app.content.legal import LEGAL_DOCS
from app.errors import ApiError

router = APIRouter(prefix="/legal", tags=["legal"])


@router.get("/{doc_type}")
async def legal(doc_type: str) -> dict:
    doc = LEGAL_DOCS.get(doc_type)
    if doc is None:
        raise ApiError("NOT_FOUND")
    return doc
