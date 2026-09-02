import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import db
from app.agent.loader import load_agent_class
from app.config import get_settings
from app.mail import get_mailer
from app.storage import get_storage

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


async def _check_db() -> bool:
    try:
        async with db.session_scope() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        log.exception("health: db")
        return False


def _check_storage(settings) -> bool:
    return get_storage().healthy()


def _check_agent(settings) -> bool:
    try:
        load_agent_class(settings.agent_impl)
        return True
    except ImportError:
        log.exception("health: agent")
        return False


@router.get("/health")
async def health():
    settings = get_settings()
    checks = {
        "db": "ok" if await _check_db() else "fail",
        "storage": "ok" if _check_storage(settings) else "fail",
        "agent": "ok" if _check_agent(settings) else "fail",
        "mail": "ok" if get_mailer().healthy() else "fail",
    }
    ok = all(v == "ok" for v in checks.values())
    body = {"status": "ok" if ok else "degraded", "version": settings.app_version, "checks": checks}
    return JSONResponse(status_code=200 if ok else 503, content=body)
