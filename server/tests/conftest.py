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
    from app.mail import reset_mailer

    get_settings.cache_clear()
    reset_mailer()
    yield
    get_settings.cache_clear()
    reset_mailer()


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


@pytest.fixture(autouse=True)
def _reset_limiter():
    from app.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def _reset_hub():
    from app.sse.hub import hub

    hub.reset()
    yield
    hub.reset()


SIGNUP_BODY = {
    "email": "hyun@example.com",
    "password": "carguard12",
    "passwordConfirm": "carguard12",
    "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True},
}


@pytest.fixture
async def auth_headers(client):
    res = await client.post("/auth/signup", json=SIGNUP_BODY)
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['accessToken']}"}


@pytest.fixture
async def case_id(client, auth_headers):
    res = await client.post("/cases", headers=auth_headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]
