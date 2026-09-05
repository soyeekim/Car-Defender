import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

KST = timezone(timedelta(hours=9))


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-test-secret-test-secret-32b")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_IMPL", "app.agent.mock:MockAgent")
    monkeypatch.setenv("MAIL_BACKEND", "mock")
    monkeypatch.setenv("CORS_ORIGINS", "http://test")
    from app.agent.loader import reset_agent
    from app.config import get_settings
    from app.mail import reset_mailer
    from app.storage import reset_storage

    get_settings.cache_clear()
    reset_mailer()
    reset_agent()
    reset_storage()
    yield
    get_settings.cache_clear()
    reset_mailer()
    reset_agent()
    reset_storage()


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


@pytest.fixture(autouse=True)
async def _drain_jobs():
    yield
    from app.jobs.runner import runner
    from app.services import chat

    await runner.wait_all()
    await runner.reset()
    await chat.wait_all()
    await chat.reset()


@pytest.fixture(autouse=True)
def _reset_actions():
    import app.services.rebuttal  # noqa: F401  import 시점에 등록되는 실제 액션을 스냅샷에 포함시킨다
    import app.services.report  # noqa: F401  import 시점에 등록되는 실제 액션을 스냅샷에 포함시킨다
    from app.services import actions

    saved = dict(actions._registry)
    yield
    actions._registry.clear()
    actions._registry.update(saved)


@pytest.fixture
def settle():
    async def _settle():
        from app.jobs.runner import runner
        from app.services import chat

        for _ in range(20):
            await chat.wait_all()
            await runner.wait_all()
            await asyncio.sleep(0)
            if not chat.pending() and not runner._tasks:
                return
        pytest.fail("background work did not drain")
    return _settle


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


@pytest.fixture(autouse=True)
def fake_probe(monkeypatch):
    """기본은 이미 브라우저가 읽는 파일 — 재생본 변환 없이 지나간다."""
    from app.services import video as video_service

    probe = video_service.Probe(
        duration_sec=42,
        recorded_at=datetime(2026, 8, 22, 14, 2, 17, tzinfo=KST),
        video_codec="h264",
        audio_codec="aac",
        format_names=frozenset({"mov", "mp4", "m4a"}),
        major_brand="isom",
    )
    monkeypatch.setattr(video_service, "probe_video", lambda path: probe)


@pytest.fixture
def upload(client, auth_headers, case_id):
    async def _upload(filename="blackbox_0822.mp4", content=b"\x00" * 2048, mime="video/mp4"):
        files = {"file": (filename, content, mime)}
        res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
        assert res.status_code == 201, res.text
        return res

    return _upload


class EventTap:
    """허브를 직접 구독해 프레임을 모은다."""

    def __init__(self, case_id):
        from app.sse.hub import hub

        self._it = hub.subscribe(case_id)
        self.frames: list[str] = []

    async def start(self):
        await self._it.__anext__()  # connected
        return self

    async def take(self, n: int, timeout: float = 5.0) -> list[str]:
        out = []
        for _ in range(n):
            frame = await asyncio.wait_for(self._it.__anext__(), timeout=timeout)
            while frame.startswith(":"):
                frame = await asyncio.wait_for(self._it.__anext__(), timeout=timeout)
            out.append(frame)
        self.frames.extend(out)
        return out

    async def close(self):
        await self._it.aclose()


@pytest.fixture
async def judged_case(client, auth_headers, case_id, upload, settle):
    async def say(text):
        r = await client.post(f"/cases/{case_id}/messages", json={"text": text}, headers=auth_headers)
        assert r.status_code == 202, r.text
        await settle()

    await say("교차로에서 오토바이가 박았어요")
    await upload()
    await settle()
    await say("우측 앞펜더요.")
    await say("초록불이었어요.")
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged", detail
    return case_id


@pytest.fixture
async def reported_case(client, auth_headers, judged_case, settle):
    r = await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    assert r.status_code == 202, r.text
    await settle()
    return judged_case


@pytest.fixture
async def sse():
    taps = []

    async def _open(case_id):
        tap = await EventTap(case_id).start()
        taps.append(tap)
        return tap

    yield _open
    for t in taps:
        await t.close()
