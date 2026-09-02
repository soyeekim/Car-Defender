import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-test-secret-test-secret-32b")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_IMPL", "builtins:object")
    monkeypatch.setenv("MAIL_BACKEND", "mock")
    monkeypatch.setenv("CORS_ORIGINS", "http://test")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def app(test_env):
    from app.main import create_app

    application = create_app()
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c
