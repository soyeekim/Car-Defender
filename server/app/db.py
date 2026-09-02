from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_database(url: str) -> None:
    global _engine, _session_factory
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
    _engine = create_async_engine(url, pool_pre_ping=True, **kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def engine() -> AsyncEngine:
    assert _engine is not None, "configure_database() 먼저"
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    assert _session_factory is not None, "configure_database() 먼저"
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


async def create_all() -> None:
    import app.models  # noqa: F401  테이블 등록

    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose() -> None:
    if _engine is not None:
        await _engine.dispose()
