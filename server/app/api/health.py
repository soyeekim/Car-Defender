from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "version": settings.app_version, "checks": {}}
