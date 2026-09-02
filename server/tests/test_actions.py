import logging

from asgi_lifespan import LifespanManager

from app.services.actions import register_action, registered, run_action


async def _noop(db, case) -> None:
    return None


def test_registered_lists_names_sorted():
    register_action("create_report", _noop)
    register_action("create_rebuttal", _noop)
    assert registered() == ["create_rebuttal", "create_report"]


async def test_run_action_ignores_unknown_name(app, case_id):
    from app.db import session_scope
    from app.models import Case

    async with session_scope() as db:
        case = await db.get(Case, case_id)
        await run_action("nope", db, case)  # 예외 없이 무시한다


async def test_startup_logs_registered_actions(test_env, caplog):
    from app.main import create_app

    register_action("create_report", _noop)
    caplog.set_level(logging.INFO, logger="app.main")
    async with LifespanManager(create_app()):
        pass
    lines = [r.getMessage() for r in caplog.records if r.name == "app.main"]
    assert any("등록된 액션" in line and "create_report" in line for line in lines), lines
