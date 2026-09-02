import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Case

log = logging.getLogger(__name__)

ActionFn = Callable[[AsyncSession, Case], Awaitable[None]]
_registry: dict[str, ActionFn] = {}


def register_action(name: str, fn: ActionFn) -> None:
    _registry[name] = fn


def registered() -> list[str]:
    return sorted(_registry)


async def run_action(name: str, db: AsyncSession, case: Case) -> None:
    fn = _registry.get(name)
    if fn is None:
        log.warning("next_action %r 은 등록된 처리기가 없어요 (case %s)", name, case.id)
        return
    await fn(db, case)
