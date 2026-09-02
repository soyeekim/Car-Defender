import builtins
import os


async def test_health_returns_ok(client):
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["version"] == "2.0.0"
    assert "checks" in body


async def test_health_checks_all_ok(client):
    body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["checks"] == {"db": "ok", "storage": "ok", "agent": "ok", "mail": "ok"}


async def test_health_degraded_when_agent_missing(test_env, monkeypatch):
    monkeypatch.setenv("AGENT_IMPL", "no.such:Thing")
    from app.config import get_settings

    get_settings.cache_clear()
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test/api/v1") as c:
            res = await c.get("/health")
    assert res.status_code == 503
    assert res.json()["status"] == "degraded"
    assert res.json()["checks"]["agent"] == "fail"


def test_check_storage_cleans_up_probe_on_write_failure(test_env, monkeypatch):
    from app.api.health import _check_storage
    from app.config import get_settings

    settings = get_settings()
    probe = os.path.join(settings.storage_local_dir, ".health")
    real_open = builtins.open

    def boom_open(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        if os.fspath(path) == probe and "w" in mode:

            class _Boom:
                def __enter__(self):
                    return self

                def __exit__(self, *exc_info):
                    f.close()
                    return False

                def write(self, data):
                    raise OSError("boom")

            return _Boom()
        return f

    monkeypatch.setattr(builtins, "open", boom_open)

    assert _check_storage(settings) is False
    assert not os.path.exists(probe)
