import logging
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import db
from app.agent.loader import load_agent_class
from app.config import get_settings
from app.mail import get_mailer

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
    if settings.storage_backend == "s3":
        return bool(settings.s3_bucket)
    probe = os.path.join(settings.storage_local_dir, ".health")
    try:
        os.makedirs(settings.storage_local_dir, exist_ok=True)
        with open(probe, "w") as f:
            f.write("ok")
        return True
    except OSError:
        log.exception("health: storage")
        return False
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


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
