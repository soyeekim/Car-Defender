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
