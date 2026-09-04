# 카-디펜더 백엔드 1/4 — 골격·에러·DB·인증 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FastAPI 앱 골격, 공통 에러 규격, DB·마이그레이션, 인증 엔드포인트(A-1~A-8 · A-10 · A-11 · 헬스체크)를 테스트와 함께 만든다.

**Architecture:** `app/` 패키지 하나. `api/`는 파싱·직렬화만, 규칙은 `services/`. 에러는 `ApiError(code)` 하나로 던지고 핸들러가 카탈로그에서 문구를 채운다. 테스트는 SQLite(aiosqlite) 파일 DB + httpx ASGI 클라이언트.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2 async · asyncpg/aiosqlite · Alembic · Pydantic v2 · PyJWT · bcrypt · python-ulid · pytest-asyncio · httpx · asgi-lifespan

**Spec:** `docs/superpowers/specs/2026-09-03-backend-design.md` (설계) · `../금융 ai 디자인/20_API명세서_v2.md` (API 계약, 정본)

## Global Constraints

- Base URL `/api/v1`. 성공 응답은 봉투 없이 리소스 본문. 목록만 `{items, hasMore, nextCursor}`.
- 에러 응답은 `{"error": {code, title, message, retryable, actions, fields}}` 여섯 키 항상 존재 (명세 §2.3).
- JSON 키는 camelCase. 시각은 ISO 8601 + `+09:00`. ID는 ULID 26자.
- Access JWT 30분 · Refresh 쿠키 14일 `httpOnly · Secure(prod) · SameSite=Lax · Path=/api/v1/auth`.
- 커밋: `<type>(<scope>): <한국어 요약>`. **Co-Authored-By · Claude-Session 트레일러 금지.**
- 저장소 루트 `C:\finance ai challenge\server`. 모든 명령은 그 안에서 실행. 파이썬은 `.venv/Scripts/python`.
- 모델은 Postgres·SQLite 공통 타입만 (`String · Integer · Boolean · DateTime(timezone=True) · JSON · Text`).

---

## 파일 구조 (이 계획에서 만드는 것)

| 파일 | 책임 |
|---|---|
| `pyproject.toml` · `.env.example` | 의존성 · pytest · ruff · 환경 변수 목록 |
| `app/config.py` | `Settings` · `get_settings()` |
| `app/main.py` | `create_app()` · lifespan |
| `app/ids.py` · `app/clock.py` | ULID · KST 변환 |
| `app/errors.py` | `ApiError` · `ERROR_CATALOG` · 핸들러 |
| `app/db.py` | `Base` · `configure_database()` · `get_db()` · `session_scope()` |
| `app/models/__init__.py` · `user.py` · `auth_token.py` | users · refresh_tokens · password_reset_tokens |
| `app/schemas/base.py` · `auth.py` | `CamelModel` · 인증 요청/응답 |
| `app/security.py` | 비밀번호 해시 · JWT · 불투명 토큰 |
| `app/deps.py` | `current_user` |
| `app/ratelimit.py` | 메모리 토큰 버킷 |
| `app/mail/base.py` · `mock.py` · `smtp.py` · `__init__.py` | `Mailer` · 구현체 · `get_mailer()` |
| `app/services/auth.py` | 회원가입·로그인·토큰·재설정 규칙 |
| `app/api/auth.py` · `users.py` · `legal.py` · `health.py` | 라우터 |
| `app/content/legal.py` | 약관 3종 |
| `app/agent/loader.py` | `AGENT_IMPL` import (헬스체크용) |
| `alembic.ini` · `alembic/` | 마이그레이션 |
| `Dockerfile` · `docker-compose.yml` · `deploy/nginx.conf` · `README.md` | 배포 |
| `tests/conftest.py` · `tests/api/test_*.py` · `tests/test_*.py` | 테스트 |

---

### Task 1: 프로젝트 골격과 헬스체크 스텁

**Files:**
- Create: `pyproject.toml`, `.env.example`, `app/__init__.py`, `app/config.py`, `app/main.py`, `app/api/__init__.py`, `app/api/health.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/api/__init__.py`, `tests/api/test_health.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI` · `get_settings() -> Settings` · 테스트 픽스처 `client: httpx.AsyncClient` (base_url `http://test/api/v1`), `app`

- [ ] **Step 1: pyproject.toml 작성**

```toml
[project]
name = "cardefender-server"
version = "2.0.0"
description = "카-디펜더 백엔드"
requires-python = ">=3.11,<3.14"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "sqlalchemy[asyncio]>=2.0",
  "asyncpg>=0.29",
  "aiosqlite>=0.20",
  "alembic>=1.13",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "pyjwt>=2.8",
  "bcrypt>=4.1",
  "python-ulid>=2.7",
  "python-multipart>=0.0.9",
  "aiosmtplib>=3.0",
  "fpdf2>=2.7",
  "email-validator>=2.1",
]

[project.optional-dependencies]
s3 = ["boto3>=1.34"]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "httpx>=0.27",
  "asgi-lifespan>=2.1",
  "ruff>=0.5",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
filterwarnings = ["ignore::DeprecationWarning"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 2: .env.example 작성**

```
APP_ENV=dev
DATABASE_URL=postgresql+asyncpg://cardefender:cardefender@localhost:5432/cardefender
JWT_SECRET=change-me-to-a-long-random-string
CORS_ORIGINS=http://localhost:5173
FRONT_BASE_URL=http://localhost:5173
AGENT_IMPL=app.agent.mock:MockAgent
STORAGE_BACKEND=local
STORAGE_LOCAL_DIR=./data
S3_BUCKET=
S3_REGION=ap-northeast-2
MAIL_BACKEND=mock
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_STARTTLS=true
MAIL_FROM=no-reply@cardefender.kr
FFPROBE_BIN=ffprobe
```

- [ ] **Step 3: app/config.py 작성**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"  # dev | test | prod
    app_version: str = "2.0.0"
    database_url: str = "sqlite+aiosqlite:///./data/dev.db"
    jwt_secret: str = "change-me"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    stream_token_minutes: int = 10
    cors_origins: str = "http://localhost:5173"
    front_base_url: str = "http://localhost:5173"
    agent_impl: str = "app.agent.mock:MockAgent"
    storage_backend: str = "local"  # local | s3
    storage_local_dir: str = "./data"
    s3_bucket: str | None = None
    s3_region: str | None = None
    mail_backend: str = "mock"  # mock | smtp
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    mail_from: str = "no-reply@cardefender.kr"
    ffprobe_bin: str = "ffprobe"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: app/api/health.py 스텁과 app/main.py 작성**

`app/api/health.py`:

```python
from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "version": settings.app_version, "checks": {}}
```

`app/main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="카-디펜더 API", version=settings.app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    app.include_router(health.router, prefix="/api/v1")
    return app


app = create_app()
```

- [ ] **Step 5: tests/conftest.py 작성**

```python
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
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
```

- [ ] **Step 6: 헬스 테스트 작성** — `tests/api/test_health.py`

```python
async def test_health_returns_ok(client):
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["version"] == "2.0.0"
    assert "checks" in body
```

- [ ] **Step 7: 가상환경 만들고 설치, 테스트 실행**

```bash
cd "C:/finance ai challenge/server"
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -U pip
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests/api/test_health.py -v
```
Expected: PASS (스텁이라 바로 통과. 이 Task는 골격과 테스트 배선 확인이 목적)

- [ ] **Step 8: 커밋**

```bash
git add pyproject.toml .env.example app tests
git commit -m "chore: 프로젝트 골격과 헬스체크 엔드포인트"
```

---

### Task 2: 공통 ID·시각 유틸과 에러 카탈로그

**Files:**
- Create: `app/ids.py`, `app/clock.py`, `app/errors.py`, `tests/test_clock.py`, `tests/test_errors.py`
- Modify: `app/main.py` (핸들러 등록)

**Interfaces:**
- Produces: `new_id() -> str` · `now_utc() -> datetime` · `to_kst(dt)` · `to_kst_iso(dt) -> str | None` · `kst_date_label(dt) -> "MM-DD"` · `kst_datetime_label(dt) -> "MM-DD HH:MM"` · `kst_yyyymmdd(dt)`
- Produces: `ApiError(code, *, fields=None, actions=None, message=None)` · `ERROR_CATALOG: dict[str, ErrorSpec]` · `error_body(code, ...) -> dict` · `error_response(code, ...) -> JSONResponse` · `register_error_handlers(app)`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_clock.py`:

```python
from datetime import datetime, timezone

from app.clock import kst_date_label, kst_datetime_label, now_utc, to_kst_iso
from app.ids import new_id


def test_new_id_is_ulid_26_chars():
    a, b = new_id(), new_id()
    assert len(a) == 26 and a != b


def test_to_kst_iso_converts_utc():
    dt = datetime(2026, 8, 22, 9, 11, 4, tzinfo=timezone.utc)
    assert to_kst_iso(dt) == "2026-08-22T18:11:04+09:00"


def test_to_kst_iso_treats_naive_as_utc():
    dt = datetime(2026, 8, 22, 9, 11, 4)
    assert to_kst_iso(dt) == "2026-08-22T18:11:04+09:00"


def test_to_kst_iso_none():
    assert to_kst_iso(None) is None


def test_labels():
    dt = datetime(2026, 8, 25, 5, 32, tzinfo=timezone.utc)
    assert kst_date_label(dt) == "08-25"
    assert kst_datetime_label(dt) == "08-25 14:32"


def test_now_utc_is_aware():
    assert now_utc().tzinfo is not None
```

`tests/test_errors.py`:

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.errors import ERROR_CATALOG, ApiError


def test_catalog_has_22_codes():
    assert len(ERROR_CATALOG) == 22


async def test_api_error_response_has_six_keys(app, client):
    router = APIRouter()

    @router.get("/boom")
    async def boom():
        raise ApiError("REBUTTAL_LOCKED", fields={"missing": "report"})

    app.include_router(router, prefix="/api/v1")
    res = await client.get("/boom")
    assert res.status_code == 409
    err = res.json()["error"]
    assert set(err) == {"code", "title", "message", "retryable", "actions", "fields"}
    assert err["code"] == "REBUTTAL_LOCKED"
    assert err["title"] == "아직 보낼 수 없어요"
    assert err["retryable"] is False
    assert err["actions"] == [{"label": "사건경위서 먼저 만들기", "type": "create_report"}]
    assert err["fields"] == {"missing": "report"}


async def test_validation_error_maps_to_validation_failed(app, client):
    router = APIRouter()

    class Body(BaseModel):
        title: str

    @router.post("/echo")
    async def echo(body: Body):
        return body

    app.include_router(router, prefix="/api/v1")
    res = await client.post("/echo", json={})
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "VALIDATION_FAILED"
    assert "title" in err["fields"]


async def test_unknown_exception_maps_to_internal_error(app, client):
    router = APIRouter()

    @router.get("/crash")
    async def crash():
        raise RuntimeError("x")

    app.include_router(router, prefix="/api/v1")
    client._transport.raise_app_exceptions = False
    res = await client.get("/crash")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"
    assert res.json()["error"]["retryable"] is True


async def test_404_route_maps_to_not_found(client):
    res = await client.get("/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_clock.py tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.clock'`

- [ ] **Step 3: app/ids.py · app/clock.py 작성**

`app/ids.py`:

```python
from ulid import ULID


def new_id() -> str:
    return str(ULID())
```

`app/clock.py`:

```python
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def to_kst(dt: datetime) -> datetime:
    return _aware(dt).astimezone(KST)


def to_kst_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return to_kst(dt).isoformat(timespec="seconds")


def kst_date_label(dt: datetime) -> str:
    return to_kst(dt).strftime("%m-%d")


def kst_datetime_label(dt: datetime) -> str:
    return to_kst(dt).strftime("%m-%d %H:%M")


def kst_yyyymmdd(dt: datetime) -> str:
    return to_kst(dt).strftime("%Y%m%d")
```

- [ ] **Step 4: app/errors.py 작성**

```python
import logging
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorSpec:
    http: int
    title: str
    message: str
    retryable: bool = False
    actions: list[dict] = field(default_factory=list)


def _a(label: str, type_: str) -> dict:
    return {"label": label, "type": type_}


ERROR_CATALOG: dict[str, ErrorSpec] = {
    "INTERNAL_ERROR": ErrorSpec(500, "문제가 생겼어요", "잠시 후 다시 시도해 주세요. 계속 안 되면 화면을 새로고침해 주세요.", True),
    "UNAUTHORIZED": ErrorSpec(401, "로그인이 필요해요", "다시 로그인해 주세요.", False, [_a("로그인하기", "go_login")]),
    "TOKEN_EXPIRED": ErrorSpec(401, "로그인이 만료됐어요", "다시 로그인해 주세요.", False, [_a("로그인하기", "go_login")]),
    "FORBIDDEN": ErrorSpec(403, "볼 수 없는 사건이에요", "내 사건 목록에서 다시 선택해 주세요.", False, [_a("내 사건 목록으로", "go_case_list")]),
    "NOT_FOUND": ErrorSpec(404, "찾을 수 없어요", "삭제됐거나 주소가 잘못됐어요.", False, [_a("내 사건 목록으로", "go_case_list")]),
    "VALIDATION_FAILED": ErrorSpec(422, "입력을 확인해 주세요", "입력한 내용을 다시 확인해 주세요."),
    "IDEMPOTENCY_KEY_REQUIRED": ErrorSpec(400, "요청을 처리하지 못했어요", "잠시 후 다시 시도해 주세요."),
    "AUTH_INVALID_CREDENTIALS": ErrorSpec(401, "로그인하지 못했어요", "이메일 또는 비밀번호가 맞지 않아요. 다시 입력해 주세요."),
    "AUTH_EMAIL_DUPLICATED": ErrorSpec(409, "가입하지 못했어요", "이미 가입된 이메일이에요. 로그인해 주세요.", False, [_a("이 이메일로 로그인하기", "go_login")]),
    "AUTH_PASSWORD_POLICY": ErrorSpec(422, "가입하지 못했어요", "비밀번호가 짧아요. 8자 이상, 숫자를 섞어 다시 입력해 주세요."),
    "AUTH_PASSWORD_MISMATCH": ErrorSpec(422, "가입하지 못했어요", "비밀번호 확인이 달라요. 한 번 더 확인해 주세요."),
    "AUTH_EMAIL_FORMAT": ErrorSpec(422, "가입하지 못했어요", "이메일 주소가 아니에요. name@company.co.kr 처럼 입력해 주세요."),
    "AGREEMENT_REQUIRED": ErrorSpec(422, "가입하지 못했어요", "필수 3가지에 모두 체크하면 가입할 수 있어요."),
    "RESET_TOKEN_INVALID": ErrorSpec(410, "링크가 만료됐어요", "비밀번호 찾기를 다시 시작해 주세요.", False, [_a("비밀번호 찾기 다시 하기", "go_password_reset")]),
    "REPORT_VERDICT_REQUIRED": ErrorSpec(409, "아직 만들 수 없어요", "과실비율 판정이 끝나면 경위서를 만들 수 있어요."),
    "REBUTTAL_LOCKED": ErrorSpec(409, "아직 보낼 수 없어요", "반박의견서에는 사건경위서가 첨부돼요. 먼저 경위서를 만들면 보낼 수 있어요.", False, [_a("사건경위서 먼저 만들기", "create_report")]),
    "RECIPIENT_INVALID": ErrorSpec(422, "보내지 못했어요", "이메일 주소가 아니에요. name@company.co.kr 처럼 고치면 보내기가 열려요."),
    "CLAIM_NUMBER_REQUIRED": ErrorSpec(422, "보내지 못했어요", "접수번호를 넣어야 보험사가 사건을 찾을 수 있어요. 보험사 접수 문자나 메일에 있어요."),
    "MAIL_SEND_FAILED": ErrorSpec(502, "보내지 못했어요", "메일 서버가 응답하지 않았어요. 작성한 내용과 첨부는 그대로 있으니, 잠시 후 다시 시도해 주세요.", True, [_a("다시 시도", "retry_send")]),
    "REBUTTAL_ALREADY_SENT": ErrorSpec(409, "이미 보낸 문서예요", "보낸 문서는 수정할 수 없어요. 다시 보내려면 새 문서로 만들어 주세요."),
    "JOB_ALREADY_RUNNING": ErrorSpec(409, "이미 진행 중이에요", "지금 하던 작업이 끝나면 다시 할 수 있어요."),
    "RATE_LIMITED": ErrorSpec(429, "잠시만요", "요청이 너무 많아요. 잠시 후 다시 시도해 주세요.", True),
}


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        *,
        fields: dict | None = None,
        actions: list[dict] | None = None,
        message: str | None = None,
    ):
        if code not in ERROR_CATALOG:
            raise ValueError(f"unknown error code {code}")
        self.code = code
        self.fields = fields
        self.actions = actions
        self.message = message
        super().__init__(code)


def error_body(code: str, *, fields=None, actions=None, message=None) -> dict:
    spec = ERROR_CATALOG[code]
    return {
        "error": {
            "code": code,
            "title": spec.title,
            "message": message or spec.message,
            "retryable": spec.retryable,
            "actions": spec.actions if actions is None else actions,
            "fields": fields,
        }
    }


def error_response(code: str, **kw) -> JSONResponse:
    return JSONResponse(status_code=ERROR_CATALOG[code].http, content=error_body(code, **kw))


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.code, fields=exc.fields, actions=exc.actions, message=exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields: dict[str, str] = {}
        for e in exc.errors():
            loc = [str(p) for p in e.get("loc", []) if p not in ("body", "query", "path")]
            name = ".".join(loc) or "body"
            fields.setdefault(name, "입력을 확인해 주세요.")
        return error_response("VALIDATION_FAILED", fields=fields)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        mapping = {404: "NOT_FOUND", 405: "NOT_FOUND", 401: "UNAUTHORIZED", 403: "FORBIDDEN"}
        code = mapping.get(exc.status_code)
        if code is None:
            log.warning("unmapped HTTPException %s: %s", exc.status_code, exc.detail)
            code = "INTERNAL_ERROR"
        return error_response(code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return error_response("INTERNAL_ERROR")
```

- [ ] **Step 5: app/main.py에 핸들러 등록**

`create_app()`에서 `app = FastAPI(...)` 바로 다음 줄에 `register_error_handlers(app)` 호출을 넣고 상단에 `from app.errors import register_error_handlers`를 추가한다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_clock.py tests/test_errors.py -v`
Expected: PASS (6 + 5)

- [ ] **Step 7: 커밋**

```bash
git add app/ids.py app/clock.py app/errors.py app/main.py tests/test_clock.py tests/test_errors.py
git commit -m "feat: ULID·KST 유틸과 에러 코드 카탈로그 22종"
```

---

### Task 3: DB 연결, User·토큰 모델, Alembic

**Files:**
- Create: `app/db.py`, `app/models/__init__.py`, `app/models/user.py`, `app/models/auth_token.py`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_users_and_tokens.py`, `tests/test_db.py`
- Modify: `app/main.py` (lifespan에서 DB 구성), `tests/conftest.py`

**Interfaces:**
- Produces: `Base` · `configure_database(url) -> None` · `get_db() -> AsyncIterator[AsyncSession]` (FastAPI 의존성) · `session_scope() -> AsyncContextManager[AsyncSession]` (Job용) · `create_all()` / `dispose()`
- Produces 모델: `User(id, email, password_hash, agreed_terms_at, agreed_privacy_at, agreed_video_at, onboarded_at, is_demo, created_at)` · `RefreshToken(id, user_id, token_hash, expires_at, revoked_at, created_at)` · `PasswordResetToken(id, user_id, token_hash, expires_at, used_at, created_at)`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_db.py`

```python
from sqlalchemy import select

from app.db import session_scope
from app.ids import new_id
from app.models import User
from app.clock import now_utc


async def test_can_insert_and_read_user(app):
    async with session_scope() as db:
        user = User(id=new_id(), email="a@b.co", password_hash="x", created_at=now_utc())
        db.add(user)
        await db.commit()
    async with session_scope() as db:
        got = (await db.execute(select(User).where(User.email == "a@b.co"))).scalar_one()
        assert got.id == user.id
        assert got.is_demo is False
        assert got.onboarded_at is None
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: app.db`

- [ ] **Step 3: app/db.py 작성**

```python
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
```

- [ ] **Step 4: 모델 작성**

`app/models/user.py`:

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    agreed_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agreed_privacy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agreed_video_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/auth_token.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/__init__.py`:

```python
from app.models.auth_token import PasswordResetToken, RefreshToken
from app.models.user import User

__all__ = ["User", "RefreshToken", "PasswordResetToken"]
```

- [ ] **Step 5: lifespan에서 DB 구성** — `app/main.py`의 `lifespan`을 교체

```python
import os

from app import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        path = settings.database_url.split("///", 1)[-1]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db.configure_database(settings.database_url)
    if settings.app_env == "test":
        await db.create_all()
    yield
    await db.dispose()
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 7: Alembic 설정**

```bash
.venv/Scripts/python -m alembic init -t async alembic
```

`alembic.ini`에서 `sqlalchemy.url = ` 줄을 빈 값으로 두고, `alembic/env.py`를 다음으로 교체:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models  # noqa: F401
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"}, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(config.get_section(config.config_ini_section, {}),
                                           prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 8: 첫 마이그레이션 생성** — Postgres가 없으므로 임시 SQLite에 대고 autogenerate

```bash
mkdir -p data
DATABASE_URL=sqlite+aiosqlite:///./data/alembic-scratch.db .venv/Scripts/python -m alembic revision --autogenerate -m "users and tokens" --rev-id 0001
DATABASE_URL=sqlite+aiosqlite:///./data/alembic-scratch.db .venv/Scripts/python -m alembic upgrade head
rm data/alembic-scratch.db
```

생성된 `alembic/versions/0001_users_and_tokens.py`를 열어 `users` · `refresh_tokens` · `password_reset_tokens` 세 테이블의 `create_table`과 인덱스가 들어 있는지 확인한다. (PowerShell이면 `$env:DATABASE_URL="sqlite+aiosqlite:///./data/alembic-scratch.db"; .venv/Scripts/python -m alembic ...` 로 실행)

- [ ] **Step 9: 전체 테스트 후 커밋**

Run: `.venv/Scripts/python -m pytest -q`
Expected: 모두 PASS

```bash
git add app/db.py app/models app/main.py alembic.ini alembic tests/test_db.py
git commit -m "feat(db): 비동기 DB 연결과 User·토큰 모델, Alembic 초기 마이그레이션"
```

---

### Task 4: 비밀번호 해시와 JWT

**Files:**
- Create: `app/security.py`, `tests/test_security.py`

**Interfaces:**
- Produces: `hash_password(raw) -> str` · `verify_password(raw, hashed) -> bool` · `create_access_token(user_id) -> str` · `decode_access_token(token) -> str(user_id)` (만료 `TOKEN_EXPIRED`, 그 외 `UNAUTHORIZED`) · `create_stream_token(video_id) -> str` · `decode_stream_token(token) -> str(video_id)` · `generate_opaque_token() -> str` · `hash_token(raw) -> str` · `password_policy_ok(raw) -> bool` · `is_email(s) -> bool`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_security.py`

```python
import time

import jwt
import pytest

from app.errors import ApiError
from app.security import (
    create_access_token,
    create_stream_token,
    decode_access_token,
    decode_stream_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    is_email,
    password_policy_ok,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("carguard12")
    assert h != "carguard12"
    assert verify_password("carguard12", h)
    assert not verify_password("wrong", h)


def test_password_policy():
    assert password_policy_ok("carguard12")
    assert not password_policy_ok("short1")
    assert not password_policy_ok("nodigitsatall")


def test_is_email():
    assert is_email("hyun@example.com")
    assert not is_email("hyun@")
    assert not is_email("")


def test_access_token_roundtrip(test_env):
    token = create_access_token("01JU1A2B3C4D5E6F7G8H9I0JKL")
    assert decode_access_token(token) == "01JU1A2B3C4D5E6F7G8H9I0JKL"


def test_expired_access_token(test_env):
    from app.config import get_settings

    payload = {"sub": "u", "type": "access", "exp": int(time.time()) - 1}
    token = jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(ApiError) as ei:
        decode_access_token(token)
    assert ei.value.code == "TOKEN_EXPIRED"


def test_garbage_token(test_env):
    with pytest.raises(ApiError) as ei:
        decode_access_token("garbage")
    assert ei.value.code == "UNAUTHORIZED"


def test_stream_token_is_not_accepted_as_access(test_env):
    token = create_stream_token("vid1")
    assert decode_stream_token(token) == "vid1"
    with pytest.raises(ApiError):
        decode_access_token(token)


def test_opaque_token_hash():
    raw = generate_opaque_token()
    assert len(raw) >= 32
    assert hash_token(raw) == hash_token(raw)
    assert hash_token(raw) != hash_token(generate_opaque_token())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_security.py -v`
Expected: FAIL — `ModuleNotFoundError: app.security`

- [ ] **Step 3: app/security.py 작성**

```python
import hashlib
import re
import secrets
from datetime import timedelta

import bcrypt
import jwt

from app.clock import now_utc
from app.config import get_settings
from app.errors import ApiError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_email(value: str | None) -> bool:
    return bool(value) and EMAIL_RE.match(value) is not None


def password_policy_ok(raw: str | None) -> bool:
    return bool(raw) and len(raw) >= 8 and any(c.isdigit() for c in raw)


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def _encode(payload: dict, minutes: int) -> str:
    settings = get_settings()
    now = now_utc()
    payload = {**payload, "iat": int(now.timestamp()), "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode(token: str, expected_type: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise ApiError("TOKEN_EXPIRED") from e
    except jwt.PyJWTError as e:
        raise ApiError("UNAUTHORIZED") from e
    if payload.get("type") != expected_type:
        raise ApiError("UNAUTHORIZED")
    return payload


def create_access_token(user_id: str) -> str:
    return _encode({"sub": user_id, "type": "access"}, get_settings().access_token_minutes)


def decode_access_token(token: str) -> str:
    return _decode(token, "access")["sub"]


def create_stream_token(video_id: str) -> str:
    return _encode({"vid": video_id, "type": "stream"}, get_settings().stream_token_minutes)


def decode_stream_token(token: str) -> str:
    return _decode(token, "stream")["vid"]


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_security.py -v`
Expected: PASS (8)

- [ ] **Step 5: 커밋**

```bash
git add app/security.py tests/test_security.py
git commit -m "feat(auth): 비밀번호 해시·JWT·스트림 토큰 유틸"
```

---

### Task 5: 회원가입 · 이메일 중복 확인

**Files:**
- Create: `app/schemas/__init__.py`, `app/schemas/base.py`, `app/schemas/auth.py`, `app/services/__init__.py`, `app/services/auth.py`, `app/api/auth.py`, `tests/api/test_auth_signup.py`
- Modify: `app/main.py` (라우터 등록)

**Interfaces:**
- Produces: `CamelModel` (alias_generator=to_camel, populate_by_name) · `UserOut` · `AuthResponse(user, access_token, expires_in)`
- Produces 서비스: `signup(db, body: SignupRequest) -> User` (검증 전부 수행) · `email_available(db, email) -> tuple[bool, str | None]` · `issue_tokens(db, user) -> tuple[str access, str refresh_raw]` · `set_refresh_cookie(response, raw)` · `clear_refresh_cookie(response)`
- 쿠키 이름 `refresh_token`, 경로 `/api/v1/auth`

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_auth_signup.py`

```python
VALID = {
    "email": "hyun@example.com",
    "password": "carguard12",
    "passwordConfirm": "carguard12",
    "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True},
}


async def test_signup_success_sets_cookie_and_returns_token(client):
    res = await client.post("/auth/signup", json=VALID)
    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "hyun@example.com"
    assert body["user"]["isDemo"] is False
    assert body["user"]["onboardedAt"] is None
    assert len(body["user"]["id"]) == 26
    assert body["expiresIn"] == 1800
    assert body["accessToken"]
    cookie = res.headers["set-cookie"]
    assert "refresh_token=" in cookie and "HttpOnly" in cookie and "Path=/api/v1/auth" in cookie
    assert "SameSite=lax" in cookie.lower()


async def test_signup_duplicate_and_bad_password_collects_all_fields(client):
    await client.post("/auth/signup", json=VALID)
    res = await client.post("/auth/signup", json={**VALID, "password": "short", "passwordConfirm": "short"})
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "AUTH_EMAIL_DUPLICATED"
    assert err["actions"] == [{"label": "이 이메일로 로그인하기", "type": "go_login"}]
    assert set(err["fields"]) == {"email", "password"}
    assert err["fields"]["password"].startswith("비밀번호가 짧아요")


async def test_signup_email_format_first_priority(client):
    res = await client.post("/auth/signup", json={**VALID, "email": "nope", "password": "short", "passwordConfirm": "x"})
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "AUTH_EMAIL_FORMAT"
    assert set(err["fields"]) == {"email", "password", "passwordConfirm"}


async def test_signup_password_mismatch(client):
    res = await client.post("/auth/signup", json={**VALID, "passwordConfirm": "carguard13"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AUTH_PASSWORD_MISMATCH"
    assert res.json()["error"]["fields"] == {"passwordConfirm": "비밀번호 확인이 달라요. 한 번 더 확인해 주세요."}


async def test_signup_agreement_required(client):
    body = {**VALID, "agreements": {"termsOfService": True, "privacy": False, "videoConsent": True}}
    res = await client.post("/auth/signup", json=body)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AGREEMENT_REQUIRED"
    assert "agreements" in res.json()["error"]["fields"]


async def test_signup_email_is_case_insensitive(client):
    await client.post("/auth/signup", json=VALID)
    res = await client.post("/auth/signup", json={**VALID, "email": "HYUN@example.com"})
    assert res.json()["error"]["code"] == "AUTH_EMAIL_DUPLICATED"


async def test_email_available(client):
    res = await client.get("/auth/email-available", params={"email": "new@example.com"})
    assert res.status_code == 200
    assert res.json() == {"available": True, "reason": None}
    await client.post("/auth/signup", json=VALID)
    res = await client.get("/auth/email-available", params={"email": "hyun@example.com"})
    assert res.json() == {"available": False, "reason": "이미 가입된 이메일이에요. 로그인해 주세요."}


async def test_email_available_bad_format(client):
    res = await client.get("/auth/email-available", params={"email": "nope"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AUTH_EMAIL_FORMAT"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_auth_signup.py -v`
Expected: FAIL — 404 `NOT_FOUND`

- [ ] **Step 3: 스키마 작성**

`app/schemas/base.py`:

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
```

`app/schemas/auth.py`:

```python
from app.schemas.base import CamelModel


class Agreements(CamelModel):
    terms_of_service: bool = False
    privacy: bool = False
    video_consent: bool = False


class SignupRequest(CamelModel):
    email: str
    password: str
    password_confirm: str
    agreements: Agreements


class LoginRequest(CamelModel):
    email: str
    password: str


class UserOut(CamelModel):
    id: str
    email: str
    onboarded_at: str | None
    is_demo: bool


class AuthResponse(CamelModel):
    user: UserOut
    access_token: str
    expires_in: int


class RefreshResponse(CamelModel):
    access_token: str
    expires_in: int


class AgreementOut(CamelModel):
    agreed: bool
    agreed_at: str | None


class MeResponse(CamelModel):
    id: str
    email: str
    onboarded_at: str | None
    is_demo: bool
    agreements: dict[str, AgreementOut]


class EmailAvailableResponse(CamelModel):
    available: bool
    reason: str | None


class PasswordResetRequest(CamelModel):
    email: str


class PasswordResetConfirmRequest(CamelModel):
    token: str
    password: str
    password_confirm: str


class MessageResponse(CamelModel):
    message: str


class OnboardingRequest(CamelModel):
    completed: bool = True
    skipped: bool = False


class OnboardingResponse(CamelModel):
    onboarded_at: str | None
```

- [ ] **Step 4: 서비스 작성** — `app/services/auth.py` (이 Task에서 쓰는 부분. 로그인·refresh는 Task 6에서 같은 파일에 추가)

```python
from datetime import timedelta

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc, to_kst_iso
from app.config import get_settings
from app.errors import ERROR_CATALOG, ApiError
from app.ids import new_id
from app.models import RefreshToken, User
from app.schemas.auth import SignupRequest, UserOut
from app.security import (
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    is_email,
    password_policy_ok,
)

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def find_user_by_email(db: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == normalize_email(email))
    return (await db.execute(stmt)).scalar_one_or_none()


async def email_available(db: AsyncSession, email: str) -> tuple[bool, str | None]:
    if not is_email(email):
        raise ApiError("AUTH_EMAIL_FORMAT", fields={"email": ERROR_CATALOG["AUTH_EMAIL_FORMAT"].message})
    if await find_user_by_email(db, email) is not None:
        return False, ERROR_CATALOG["AUTH_EMAIL_DUPLICATED"].message
    return True, None


async def signup(db: AsyncSession, body: SignupRequest) -> User:
    fields: dict[str, str] = {}
    code: str | None = None

    def fail(c: str, field: str) -> None:
        nonlocal code
        fields[field] = ERROR_CATALOG[c].message
        code = code or c

    if not is_email(body.email):
        fail("AUTH_EMAIL_FORMAT", "email")
    elif await find_user_by_email(db, body.email) is not None:
        fail("AUTH_EMAIL_DUPLICATED", "email")
    if not password_policy_ok(body.password):
        fail("AUTH_PASSWORD_POLICY", "password")
    if body.password != body.password_confirm:
        fail("AUTH_PASSWORD_MISMATCH", "passwordConfirm")
    a = body.agreements
    if not (a.terms_of_service and a.privacy and a.video_consent):
        fail("AGREEMENT_REQUIRED", "agreements")
    if code:
        raise ApiError(code, fields=fields)

    now = now_utc()
    user = User(
        id=new_id(),
        email=normalize_email(body.email),
        password_hash=hash_password(body.password),
        agreed_terms_at=now,
        agreed_privacy_at=now,
        agreed_video_at=now,
        created_at=now,
    )
    db.add(user)
    await db.commit()
    return user


async def issue_tokens(db: AsyncSession, user: User) -> tuple[str, str]:
    settings = get_settings()
    raw = generate_opaque_token()
    db.add(
        RefreshToken(
            id=new_id(),
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
            created_at=now_utc(),
        )
    )
    await db.commit()
    return create_access_token(user.id), raw


def set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        raw,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, onboarded_at=to_kst_iso(user.onboarded_at), is_demo=user.is_demo)


def access_expires_in() -> int:
    return get_settings().access_token_minutes * 60
```

- [ ] **Step 5: 라우터 작성** — `app/api/auth.py`

```python
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas.auth import AuthResponse, EmailAvailableResponse, SignupRequest
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=AuthResponse)
async def signup(body: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    user = await auth_service.signup(db, body)
    access, refresh = await auth_service.issue_tokens(db, user)
    auth_service.set_refresh_cookie(response, refresh)
    return AuthResponse(user=auth_service.user_out(user), access_token=access, expires_in=auth_service.access_expires_in())


@router.get("/email-available", response_model=EmailAvailableResponse)
async def email_available(email: str = Query(...), db: AsyncSession = Depends(get_db)):
    available, reason = await auth_service.email_available(db, email)
    return EmailAvailableResponse(available=available, reason=reason)
```

`app/main.py`의 `create_app()`에 `from app.api import auth` 후 `app.include_router(auth.router, prefix="/api/v1")` 추가.

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_auth_signup.py -v`
Expected: PASS (8)

- [ ] **Step 7: 커밋**

```bash
git add app/schemas app/services app/api/auth.py app/main.py tests/api/test_auth_signup.py
git commit -m "feat(auth): 회원가입과 이메일 중복 확인 API"
```

---

### Task 6: 로그인 · 토큰 갱신 · 로그아웃 · 내 정보 · 속도 제한

**Files:**
- Create: `app/ratelimit.py`, `app/deps.py`, `tests/api/test_auth_login.py`, `tests/test_ratelimit.py`
- Modify: `app/services/auth.py`, `app/api/auth.py`

**Interfaces:**
- Produces: `RateLimiter.check(key, limit, per_seconds)` (초과 시 `ApiError("RATE_LIMITED")`) · 전역 인스턴스 `limiter`
- Produces 의존성: `current_user(...) -> User` (Bearer 검사) · `bearer_token(...) -> str | None`
- Produces 서비스: `login(db, email, password) -> User` · `refresh_access(db, raw_cookie) -> User` · `revoke_refresh(db, raw_cookie)` · `me_response(user) -> MeResponse`
- 테스트 픽스처 `auth_headers(client) -> dict` 를 `tests/conftest.py`에 추가 (가입 후 Bearer 헤더 반환)

- [ ] **Step 1: 실패하는 테스트**

`tests/test_ratelimit.py`:

```python
import pytest

from app.errors import ApiError
from app.ratelimit import RateLimiter


def test_rate_limiter_blocks_after_limit():
    rl = RateLimiter()
    for _ in range(3):
        rl.check("k", limit=3, per_seconds=60)
    with pytest.raises(ApiError) as ei:
        rl.check("k", limit=3, per_seconds=60)
    assert ei.value.code == "RATE_LIMITED"


def test_rate_limiter_window_expires():
    now = [1000.0]
    rl = RateLimiter(clock=lambda: now[0])
    rl.check("k", limit=1, per_seconds=10)
    now[0] += 11
    rl.check("k", limit=1, per_seconds=10)
```

`tests/api/test_auth_login.py`:

```python
import time

import jwt

VALID = {
    "email": "hyun@example.com",
    "password": "carguard12",
    "passwordConfirm": "carguard12",
    "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True},
}


async def signup(client):
    return await client.post("/auth/signup", json=VALID)


async def test_login_success(client):
    await signup(client)
    res = await client.post("/auth/login", json={"email": "Hyun@Example.com", "password": "carguard12"})
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "hyun@example.com"
    assert "refresh_token=" in res.headers["set-cookie"]


async def test_login_wrong_password_and_unknown_email_look_same(client):
    await signup(client)
    a = await client.post("/auth/login", json={"email": "hyun@example.com", "password": "nope1234"})
    b = await client.post("/auth/login", json={"email": "ghost@example.com", "password": "nope1234"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert a.json()["error"]["fields"] == {"password": "이메일 또는 비밀번호가 맞지 않아요. 다시 입력해 주세요."}


async def test_me_requires_bearer(client):
    res = await client.get("/auth/me")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_me_returns_agreements(client):
    token = (await signup(client)).json()["accessToken"]
    res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "hyun@example.com"
    assert body["agreements"]["termsOfService"]["agreed"] is True
    assert body["agreements"]["videoConsent"]["agreedAt"].endswith("+09:00")


async def test_expired_token_gives_token_expired(client, test_env):
    from app.config import get_settings

    await signup(client)
    token = jwt.encode({"sub": "x", "type": "access", "exp": int(time.time()) - 5}, get_settings().jwt_secret, algorithm="HS256")
    res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_refresh_returns_new_access_token(client):
    await signup(client)  # httpx 클라이언트가 쿠키를 보관한다
    res = await client.post("/auth/refresh")
    assert res.status_code == 200
    assert res.json()["expiresIn"] == 1800
    assert res.json()["accessToken"]


async def test_refresh_without_cookie(client):
    res = await client.post("/auth/refresh")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_logout_revokes_refresh(client):
    token = (await signup(client)).json()["accessToken"]
    res = await client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 204
    res = await client.post("/auth/refresh")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_login_rate_limited(client):
    await signup(client)
    for _ in range(10):
        await client.post("/auth/login", json={"email": "hyun@example.com", "password": "bad00000"})
    res = await client.post("/auth/login", json={"email": "hyun@example.com", "password": "bad00000"})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_ratelimit.py tests/api/test_auth_login.py -v`
Expected: FAIL

- [ ] **Step 3: app/ratelimit.py 작성**

```python
import time
from collections import defaultdict, deque
from collections.abc import Callable

from app.errors import ApiError


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, per_seconds: int) -> None:
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0] > per_seconds:
            q.popleft()
        if len(q) >= limit:
            raise ApiError("RATE_LIMITED")
        q.append(now)

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()
```

- [ ] **Step 4: app/deps.py 작성**

```python
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.errors import ApiError
from app.models import User
from app.security import decode_access_token


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


async def current_user(
    token: str | None = Depends(bearer_token), db: AsyncSession = Depends(get_db)
) -> User:
    if not token:
        raise ApiError("UNAUTHORIZED")
    user_id = decode_access_token(token)
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError("UNAUTHORIZED")
    return user


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
```

- [ ] **Step 5: 서비스에 로그인·refresh·로그아웃·me 추가** — `app/services/auth.py` 끝에 덧붙임

```python
from app.models import RefreshToken  # 이미 import 됨
from app.schemas.auth import AgreementOut, MeResponse
from app.security import verify_password


async def login(db: AsyncSession, email: str, password: str) -> User:
    user = await find_user_by_email(db, email) if is_email(email) else None
    if user is None or not verify_password(password, user.password_hash):
        raise ApiError(
            "AUTH_INVALID_CREDENTIALS",
            fields={"password": ERROR_CATALOG["AUTH_INVALID_CREDENTIALS"].message},
        )
    return user


async def _find_refresh(db: AsyncSession, raw: str | None) -> RefreshToken | None:
    if not raw:
        return None
    stmt = select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
    return (await db.execute(stmt)).scalar_one_or_none()


async def refresh_access(db: AsyncSession, raw: str | None) -> User:
    if not raw:
        raise ApiError("UNAUTHORIZED")
    rt = await _find_refresh(db, raw)
    if rt is None:
        raise ApiError("UNAUTHORIZED")
    now = now_utc()
    expires = rt.expires_at if rt.expires_at.tzinfo else rt.expires_at.replace(tzinfo=now.tzinfo)
    if rt.revoked_at is not None or expires < now:
        raise ApiError("TOKEN_EXPIRED")
    user = await db.get(User, rt.user_id)
    if user is None:
        raise ApiError("UNAUTHORIZED")
    return user


async def revoke_refresh(db: AsyncSession, raw: str | None) -> None:
    rt = await _find_refresh(db, raw)
    if rt is not None and rt.revoked_at is None:
        rt.revoked_at = now_utc()
        await db.commit()


def me_response(user: User) -> MeResponse:
    def ag(dt):
        return AgreementOut(agreed=dt is not None, agreed_at=to_kst_iso(dt))

    return MeResponse(
        id=user.id,
        email=user.email,
        onboarded_at=to_kst_iso(user.onboarded_at),
        is_demo=user.is_demo,
        agreements={
            "termsOfService": ag(user.agreed_terms_at),
            "privacy": ag(user.agreed_privacy_at),
            "videoConsent": ag(user.agreed_video_at),
        },
    )
```

- [ ] **Step 6: 라우터에 엔드포인트 추가** — `app/api/auth.py`

```python
from fastapi import Cookie, Request

from app.deps import client_ip, current_user
from app.models import User
from app.ratelimit import limiter
from app.schemas.auth import LoginRequest, MeResponse, RefreshResponse


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    limiter.check(f"login:{client_ip(request)}:{body.email.lower()}", limit=10, per_seconds=60)
    user = await auth_service.login(db, body.email, body.password)
    access, refresh = await auth_service.issue_tokens(db, user)
    auth_service.set_refresh_cookie(response, refresh)
    return AuthResponse(user=auth_service.user_out(user), access_token=access, expires_in=auth_service.access_expires_in())


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    refresh_token: str | None = Cookie(default=None, alias=auth_service.REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
):
    user = await auth_service.refresh_access(db, refresh_token)
    from app.security import create_access_token

    return RefreshResponse(access_token=create_access_token(user.id), expires_in=auth_service.access_expires_in())


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    user: User = Depends(current_user),
    refresh_token: str | None = Cookie(default=None, alias=auth_service.REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
):
    await auth_service.revoke_refresh(db, refresh_token)
    auth_service.clear_refresh_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(current_user)):
    return auth_service.me_response(user)
```

`email-available`에도 속도 제한을 건다: 함수 첫 줄에 `limiter.check(f"email:{client_ip(request)}", limit=30, per_seconds=60)` (시그니처에 `request: Request` 추가).

- [ ] **Step 7: conftest에 픽스처 추가와 limiter 초기화** — `tests/conftest.py`

```python
@pytest.fixture(autouse=True)
def _reset_limiter():
    from app.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()


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
```

- [ ] **Step 8: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_ratelimit.py tests/api/test_auth_login.py -v`
Expected: PASS (2 + 9)

- [ ] **Step 9: 커밋**

```bash
git add app/ratelimit.py app/deps.py app/services/auth.py app/api/auth.py tests/conftest.py tests/test_ratelimit.py tests/api/test_auth_login.py
git commit -m "feat(auth): 로그인·토큰 갱신·로그아웃·내 정보와 속도 제한"
```

---

### Task 7: 메일 인터페이스와 비밀번호 재설정

**Files:**
- Create: `app/mail/__init__.py`, `app/mail/base.py`, `app/mail/mock.py`, `app/mail/smtp.py`, `tests/api/test_auth_reset.py`, `tests/test_mail.py`
- Modify: `app/services/auth.py`, `app/api/auth.py`

**Interfaces:**
- Produces: `MailMessage(to, subject, body_text, reply_to=None, sender_email=None, display_name=None, attachments: list[MailAttachment]=[])` · `MailAttachment(filename, content: bytes, mime_type)` · `Mailer.send(msg) -> str(provider_message_id)` · `MailSendError(Exception)` · `MockMailer.sent: list[MailMessage]` · `get_mailer() -> Mailer` (설정 기반 싱글턴, `reset_mailer()`)
- Produces 서비스: `request_password_reset(db, email) -> None` · `confirm_password_reset(db, token, password, confirm) -> None`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_mail.py`:

```python
from app.mail import get_mailer, reset_mailer
from app.mail.base import MailMessage
from app.mail.mock import MockMailer


async def test_mock_mailer_records_messages(test_env):
    reset_mailer()
    mailer = get_mailer()
    assert isinstance(mailer, MockMailer)
    mid = await mailer.send(MailMessage(to="a@b.co", subject="s", body_text="hi"))
    assert mid.startswith("mock-")
    assert mailer.sent[0].to == "a@b.co"
```

`tests/api/test_auth_reset.py`:

```python
from app.mail import get_mailer
from tests.conftest import SIGNUP_BODY


async def test_reset_request_sends_mail_with_link(client):
    await client.post("/auth/signup", json=SIGNUP_BODY)
    res = await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    assert res.status_code == 202
    assert res.json() == {"message": "비밀번호 재설정 링크를 보냈어요. 메일함을 확인해 주세요."}
    sent = get_mailer().sent
    assert len(sent) == 1 and "reset?token=" in sent[0].body_text


async def test_reset_request_unknown_email_still_202_and_no_mail(client):
    res = await client.post("/auth/password-reset", json={"email": "ghost@example.com"})
    assert res.status_code == 202
    assert get_mailer().sent == []


async def test_reset_request_bad_format(client):
    res = await client.post("/auth/password-reset", json={"email": "nope"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AUTH_EMAIL_FORMAT"


async def test_reset_request_rate_limited_3_per_minute(client):
    for _ in range(3):
        await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    res = await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    assert res.status_code == 429


async def test_reset_confirm_changes_password_once(client):
    await client.post("/auth/signup", json=SIGNUP_BODY)
    await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    body = get_mailer().sent[0].body_text
    token = body.split("reset?token=")[1].split()[0]

    res = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "newpass2026", "passwordConfirm": "newpass2026"})
    assert res.status_code == 200
    assert res.json() == {"message": "비밀번호를 바꿨어요. 새 비밀번호로 로그인해 주세요."}

    login = await client.post("/auth/login", json={"email": "hyun@example.com", "password": "newpass2026"})
    assert login.status_code == 200

    again = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "another2026", "passwordConfirm": "another2026"})
    assert again.status_code == 410
    assert again.json()["error"]["code"] == "RESET_TOKEN_INVALID"


async def test_reset_confirm_policy_and_mismatch(client):
    await client.post("/auth/signup", json=SIGNUP_BODY)
    await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    token = get_mailer().sent[0].body_text.split("reset?token=")[1].split()[0]
    res = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "short", "passwordConfirm": "short"})
    assert res.json()["error"]["code"] == "AUTH_PASSWORD_POLICY"
    res = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "newpass2026", "passwordConfirm": "x"})
    assert res.json()["error"]["code"] == "AUTH_PASSWORD_MISMATCH"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_mail.py tests/api/test_auth_reset.py -v`
Expected: FAIL — `ModuleNotFoundError: app.mail`

- [ ] **Step 3: 메일 모듈 작성**

`app/mail/base.py`:

```python
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class MailAttachment:
    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"


@dataclass
class MailMessage:
    to: str
    subject: str
    body_text: str
    reply_to: str | None = None
    sender_email: str | None = None
    display_name: str | None = None
    attachments: list[MailAttachment] = field(default_factory=list)


class MailSendError(Exception):
    pass


class Mailer(Protocol):
    async def send(self, msg: MailMessage) -> str: ...

    def healthy(self) -> bool: ...
```

`app/mail/mock.py`:

```python
import logging

from app.ids import new_id
from app.mail.base import MailMessage

log = logging.getLogger(__name__)


class MockMailer:
    def __init__(self) -> None:
        self.sent: list[MailMessage] = []

    async def send(self, msg: MailMessage) -> str:
        self.sent.append(msg)
        log.info("[mock mail] to=%s subject=%s attachments=%d", msg.to, msg.subject, len(msg.attachments))
        return f"mock-{new_id()}"

    def healthy(self) -> bool:
        return True
```

`app/mail/smtp.py`:

```python
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import aiosmtplib

from app.config import Settings
from app.mail.base import MailMessage, MailSendError


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def healthy(self) -> bool:
        return bool(self.s.smtp_host and self.s.mail_from)

    async def send(self, msg: MailMessage) -> str:
        em = EmailMessage()
        display = msg.display_name or "카-디펜더"
        em["From"] = formataddr((display, self.s.mail_from))
        em["To"] = msg.to
        em["Subject"] = msg.subject
        if msg.sender_email:
            em["Sender"] = msg.sender_email
        if msg.reply_to:
            em["Reply-To"] = msg.reply_to
        message_id = make_msgid(domain=self.s.mail_from.split("@")[-1])
        em["Message-ID"] = message_id
        em.set_content(msg.body_text)
        for att in msg.attachments:
            maintype, _, subtype = att.mime_type.partition("/")
            em.add_attachment(att.content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=att.filename)
        try:
            await aiosmtplib.send(
                em,
                hostname=self.s.smtp_host,
                port=self.s.smtp_port,
                username=self.s.smtp_user or None,
                password=self.s.smtp_password or None,
                start_tls=self.s.smtp_starttls,
                timeout=30,
            )
        except (aiosmtplib.SMTPException, OSError) as e:
            raise MailSendError(str(e)) from e
        return message_id
```

`app/mail/__init__.py`:

```python
from app.config import get_settings
from app.mail.base import Mailer, MailMessage, MailAttachment, MailSendError

_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        settings = get_settings()
        if settings.mail_backend == "smtp":
            from app.mail.smtp import SmtpMailer

            _mailer = SmtpMailer(settings)
        else:
            from app.mail.mock import MockMailer

            _mailer = MockMailer()
    return _mailer


def reset_mailer() -> None:
    global _mailer
    _mailer = None


__all__ = ["Mailer", "MailMessage", "MailAttachment", "MailSendError", "get_mailer", "reset_mailer"]
```

`tests/conftest.py`의 `test_env` 픽스처에서 `get_settings.cache_clear()` 뒤에 `from app.mail import reset_mailer; reset_mailer()`를 yield 앞뒤로 호출한다.

- [ ] **Step 4: 서비스에 재설정 추가** — `app/services/auth.py` 끝에

```python
from app.mail import MailMessage, get_mailer
from app.models import PasswordResetToken

RESET_TOKEN_MINUTES = 30


async def request_password_reset(db: AsyncSession, email: str) -> None:
    if not is_email(email):
        raise ApiError("AUTH_EMAIL_FORMAT", fields={"email": ERROR_CATALOG["AUTH_EMAIL_FORMAT"].message})
    user = await find_user_by_email(db, email)
    if user is None:
        return
    raw = generate_opaque_token()
    db.add(
        PasswordResetToken(
            id=new_id(),
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=now_utc() + timedelta(minutes=RESET_TOKEN_MINUTES),
            created_at=now_utc(),
        )
    )
    await db.commit()
    link = f"{get_settings().front_base_url.rstrip('/')}/reset?token={raw}"
    body = (
        "카-디펜더 비밀번호 재설정 링크예요. 30분 안에 아래 주소를 열어 새 비밀번호를 정해 주세요.\n\n"
        f"{link}\n\n"
        "본인이 요청하지 않았다면 이 메일은 무시해도 돼요."
    )
    await get_mailer().send(MailMessage(to=user.email, subject="[카-디펜더] 비밀번호 재설정", body_text=body))


async def confirm_password_reset(db: AsyncSession, token: str, password: str, confirm: str) -> None:
    stmt = select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token or ""))
    prt = (await db.execute(stmt)).scalar_one_or_none()
    now = now_utc()
    if prt is None or prt.used_at is not None:
        raise ApiError("RESET_TOKEN_INVALID")
    expires = prt.expires_at if prt.expires_at.tzinfo else prt.expires_at.replace(tzinfo=now.tzinfo)
    if expires < now:
        raise ApiError("RESET_TOKEN_INVALID")
    if not password_policy_ok(password):
        raise ApiError("AUTH_PASSWORD_POLICY", fields={"password": ERROR_CATALOG["AUTH_PASSWORD_POLICY"].message})
    if password != confirm:
        raise ApiError("AUTH_PASSWORD_MISMATCH", fields={"passwordConfirm": ERROR_CATALOG["AUTH_PASSWORD_MISMATCH"].message})
    user = await db.get(User, prt.user_id)
    if user is None:
        raise ApiError("RESET_TOKEN_INVALID")
    user.password_hash = hash_password(password)
    prt.used_at = now
    await db.commit()
```

- [ ] **Step 5: 라우터 추가** — `app/api/auth.py`

```python
from app.schemas.auth import MessageResponse, PasswordResetConfirmRequest, PasswordResetRequest


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED, response_model=MessageResponse)
async def password_reset(body: PasswordResetRequest, request: Request, db: AsyncSession = Depends(get_db)):
    limiter.check(f"reset:{body.email.lower()}", limit=3, per_seconds=60)
    await auth_service.request_password_reset(db, body.email)
    return MessageResponse(message="비밀번호 재설정 링크를 보냈어요. 메일함을 확인해 주세요.")


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def password_reset_confirm(body: PasswordResetConfirmRequest, db: AsyncSession = Depends(get_db)):
    await auth_service.confirm_password_reset(db, body.token, body.password, body.password_confirm)
    return MessageResponse(message="비밀번호를 바꿨어요. 새 비밀번호로 로그인해 주세요.")
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_mail.py tests/api/test_auth_reset.py -v`
Expected: PASS (1 + 6)

- [ ] **Step 7: 커밋**

```bash
git add app/mail app/services/auth.py app/api/auth.py tests/conftest.py tests/test_mail.py tests/api/test_auth_reset.py
git commit -m "feat(auth): 메일 인터페이스와 비밀번호 재설정 API"
```

---

### Task 8: 온보딩 기록 · 약관 전문

**Files:**
- Create: `app/api/users.py`, `app/api/legal.py`, `app/content/__init__.py`, `app/content/legal.py`, `tests/api/test_users_legal.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces: `LEGAL_DOCS: dict[str, dict]` (키 `terms` · `privacy` · `video-consent`, 값 `{docType, title, version, bodyMarkdown}`)

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_users_legal.py`

```python
async def test_onboarding_sets_timestamp(client, auth_headers):
    res = await client.patch("/users/me/onboarding", json={"completed": True, "skipped": False}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["onboardedAt"].endswith("+09:00")
    me = await client.get("/auth/me", headers=auth_headers)
    assert me.json()["onboardedAt"] == res.json()["onboardedAt"]


async def test_onboarding_skipped_also_sets_timestamp(client, auth_headers):
    res = await client.patch("/users/me/onboarding", json={"completed": False, "skipped": True}, headers=auth_headers)
    assert res.json()["onboardedAt"] is not None


async def test_onboarding_requires_auth(client):
    res = await client.patch("/users/me/onboarding", json={"completed": True})
    assert res.status_code == 401


async def test_legal_docs(client):
    for doc_type in ("terms", "privacy", "video-consent"):
        res = await client.get(f"/legal/{doc_type}")
        assert res.status_code == 200
        body = res.json()
        assert body["docType"] == doc_type
        assert body["title"] and body["version"] and body["bodyMarkdown"].startswith("## ")
    res = await client.get("/legal/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_users_legal.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 약관 콘텐츠** — `app/content/legal.py`

```python
LEGAL_VERSION = "2026-08-01"

LEGAL_DOCS: dict[str, dict] = {
    "terms": {
        "docType": "terms",
        "title": "이용약관",
        "version": LEGAL_VERSION,
        "bodyMarkdown": (
            "## 1. 목적\n이 약관은 카-디펜더(이하 서비스)가 제공하는 교통사고 과실비율 분석·문서 작성 보조 서비스의 이용 조건을 정합니다.\n\n"
            "## 2. 서비스의 성격\n서비스가 제시하는 과실비율·문서는 참고용이며, 최종 과실비율은 보험사·분쟁심의위원회 결정에 따릅니다.\n\n"
            "## 3. 이용자의 의무\n이용자는 본인이 당사자인 사고의 자료만 올려야 하며, 타인의 권리를 침해하는 자료를 올리지 않습니다.\n\n"
            "## 4. 서비스 변경·중단\n서비스는 사전 고지 후 내용을 바꾸거나 중단할 수 있습니다.\n\n"
            "## 5. 책임의 한계\n서비스는 분석 결과의 정확성을 보증하지 않으며, 이를 근거로 한 결정의 결과에 책임지지 않습니다."
        ),
    },
    "privacy": {
        "docType": "privacy",
        "title": "개인정보 처리방침",
        "version": LEGAL_VERSION,
        "bodyMarkdown": (
            "## 1. 수집 항목\n이메일, 비밀번호(암호화 저장), 사고 설명, 작성 문서, 발송 기록.\n\n"
            "## 2. 이용 목적\n회원 식별, 사건 분석·문서 작성, 반박의견서 발송, 문의 응대.\n\n"
            "## 3. 보관 기간\n회원 탈퇴 또는 사건 삭제 시 지체 없이 파기합니다. 발송 기록은 분쟁 대응을 위해 1년 보관합니다.\n\n"
            "## 4. 제3자 제공\n이용자가 반박의견서를 보낼 때 지정한 수신자에게만 문서와 첨부를 전달합니다.\n\n"
            "## 5. 이용자의 권리\n이용자는 언제든 자신의 정보 열람·정정·삭제를 요청할 수 있습니다."
        ),
    },
    "video-consent": {
        "docType": "video-consent",
        "title": "블랙박스 영상(개인영상정보) 수집·이용 동의",
        "version": LEGAL_VERSION,
        "bodyMarkdown": (
            "## 1. 수집 항목\n블랙박스 영상 파일과 영상에 담긴 촬영 시각·차량 정보.\n\n"
            "## 2. 이용 목적\n사고 상황 분석, 과실비율 산출, 사건경위서·반박의견서 첨부.\n\n"
            "## 3. 보관·파기\n영상은 이 사건 처리에만 쓰이며, 사건을 지우면 함께 지워집니다.\n\n"
            "## 4. 제3자 제공\n이용자가 반박의견서에 첨부해 보내기로 선택한 경우에만 지정 수신자에게 전달됩니다.\n\n"
            "## 5. 동의 거부\n동의하지 않으면 영상 분석 기능을 이용할 수 없습니다."
        ),
    },
}
```

- [ ] **Step 4: 라우터 작성**

`app/api/legal.py`:

```python
from fastapi import APIRouter

from app.content.legal import LEGAL_DOCS
from app.errors import ApiError

router = APIRouter(prefix="/legal", tags=["legal"])


@router.get("/{doc_type}")
async def legal(doc_type: str) -> dict:
    doc = LEGAL_DOCS.get(doc_type)
    if doc is None:
        raise ApiError("NOT_FOUND")
    return doc
```

`app/api/users.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc, to_kst_iso
from app.db import get_db
from app.deps import current_user
from app.models import User
from app.schemas.auth import OnboardingRequest, OnboardingResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me/onboarding", response_model=OnboardingResponse)
async def onboarding(body: OnboardingRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if body.completed or body.skipped:
        user.onboarded_at = now_utc()
        await db.commit()
    return OnboardingResponse(onboarded_at=to_kst_iso(user.onboarded_at))
```

`app/main.py`에 `users.router` · `legal.router`를 `/api/v1` prefix로 등록.

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_users_legal.py -v`
Expected: PASS (4)

- [ ] **Step 6: 커밋**

```bash
git add app/api/users.py app/api/legal.py app/content app/main.py tests/api/test_users_legal.py
git commit -m "feat(auth): 온보딩 기록과 약관 전문 API"
```

---

### Task 9: 헬스체크 완성 · Docker · README

**Files:**
- Create: `app/agent/__init__.py`, `app/agent/loader.py`, `Dockerfile`, `docker-compose.yml`, `deploy/nginx.conf`, `README.md`, `tests/test_agent_loader.py`
- Modify: `app/api/health.py`, `tests/api/test_health.py`

**Interfaces:**
- Produces: `load_agent_class(path: str) -> type` (`"pkg.mod:Class"` 형식, 실패 시 `ImportError`) · `get_agent()` 는 계획 2에서 추가
- 헬스 `checks`: `db` · `storage` · `agent` · `mail` 각 `"ok"` | `"fail"`. 하나라도 fail이면 503 + `status: "degraded"`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_agent_loader.py`:

```python
import pytest

from app.agent.loader import load_agent_class


def test_load_agent_class():
    assert load_agent_class("builtins:object") is object


def test_load_agent_class_bad_path():
    with pytest.raises(ImportError):
        load_agent_class("no.such.module:Thing")
    with pytest.raises(ImportError):
        load_agent_class("builtins:NoSuchClass")
```

`tests/api/test_health.py`에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_agent_loader.py tests/api/test_health.py -v`
Expected: FAIL

- [ ] **Step 3: app/agent/loader.py**

```python
import importlib


def load_agent_class(path: str) -> type:
    if ":" not in path:
        raise ImportError(f"AGENT_IMPL 형식은 'pkg.module:Class' 이어야 해요: {path!r}")
    module_name, _, class_name = path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        raise ImportError(f"AGENT_IMPL 모듈을 찾을 수 없어요: {module_name}") from e
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ImportError(f"AGENT_IMPL 클래스를 찾을 수 없어요: {path}")
    return cls
```

`app/agent/__init__.py`는 빈 파일.

- [ ] **Step 4: 헬스체크 완성** — `app/api/health.py` 교체

```python
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
    try:
        os.makedirs(settings.storage_local_dir, exist_ok=True)
        probe = os.path.join(settings.storage_local_dir, ".health")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        log.exception("health: storage")
        return False


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
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 6: Docker · nginx · README**

`Dockerfile`:

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir -e ".[s3]"

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
```

`docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: cardefender
      POSTGRES_PASSWORD: cardefender
      POSTGRES_DB: cardefender
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cardefender"]
      interval: 5s
      timeout: 3s
      retries: 10

  app:
    build: .
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://cardefender:cardefender@db:5432/cardefender
      STORAGE_LOCAL_DIR: /data
    volumes:
      - ./data:/data
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"

  nginx:
    image: nginx:1.27-alpine
    volumes:
      - ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "80:80"
    depends_on:
      - app

volumes:
  pgdata:
```

`deploy/nginx.conf`:

```nginx
server {
    listen 80;
    client_max_body_size 256m;

    location /api/ {
        proxy_pass http://app:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_buffering off;
        proxy_cache off;
        proxy_request_buffering off;
    }
}
```

`README.md`:

```markdown
# 카-디펜더 백엔드

FastAPI · PostgreSQL · SSE. API 계약은 `20_API명세서_v2.md`, 구조는 `docs/superpowers/specs/2026-09-03-backend-design.md`.

## 로컬 실행

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env            # 값 수정
docker compose up -d db         # Postgres만
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --reload
```

`DATABASE_URL`을 `sqlite+aiosqlite:///./data/dev.db`로 두면 Postgres 없이도 돈다.

## 테스트

```bash
.venv/Scripts/python -m pytest
```

## 배포 (EC2 한 대)

```bash
docker compose up -d --build
curl localhost/api/v1/health
```

## 환경 변수

`.env.example` 참고. `AGENT_IMPL`은 `패키지.모듈:클래스` 형식으로 AI 담당 구현체를 가리킨다. 기본값은 `app.agent.mock:MockAgent`.
```

- [ ] **Step 7: 커밋**

```bash
git add app/agent app/api/health.py Dockerfile docker-compose.yml deploy README.md tests/test_agent_loader.py tests/api/test_health.py
git commit -m "feat: 헬스체크 4종 점검과 Docker·nginx 배포 설정"
```

---

## 계획 1 완료 기준

- `pytest -q` 전부 통과.
- `uvicorn app.main:app` 실행 후 `GET /api/v1/health`가 `{"status":"ok", ...}`.
- 회원가입 → 로그인 → me → refresh → 로그아웃이 curl로 동작.
- 계획 2(`2026-09-03-02-cases-sse-jobs.md`)로 이어진다.
