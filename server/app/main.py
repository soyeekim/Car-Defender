import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import (
    auth,
    cases,
    events,
    health,
    legal,
    messages,
    precedents,
    report,
    users,
    verdict,
    videos,
)
from app.config import get_settings
from app.errors import register_error_handlers
from app.jobs.runner import runner
from app.middleware import CatchAllErrorMiddleware
from app.services import actions, chat

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        path = settings.database_url.split("///", 1)[-1]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db.configure_database(settings.database_url)
    if settings.app_env == "test":
        await db.create_all()
    await runner.cleanup_stale()
    # 등록된 next_action 처리기를 남긴다: 문서 생성·반박 액션이 붙었는지 기동 로그로 확인한다.
    log.info("등록된 액션: %s", ", ".join(actions.registered()) or "(없음)")
    yield
    await chat.wait_all(timeout=settings.shutdown_wait_seconds)
    await runner.wait_all(timeout=settings.shutdown_wait_seconds)
    await db.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="카-디펜더 API", version=settings.app_version, lifespan=lifespan)
    register_error_handlers(app)
    app.add_middleware(CatchAllErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(legal.router, prefix="/api/v1")
    app.include_router(cases.router, prefix="/api/v1")
    app.include_router(messages.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(videos.router, prefix="/api/v1")
    app.include_router(verdict.router, prefix="/api/v1")
    app.include_router(precedents.router, prefix="/api/v1")
    app.include_router(report.router, prefix="/api/v1")
    return app


app = create_app()
