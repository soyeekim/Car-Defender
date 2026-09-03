# 카-디펜더 백엔드

FastAPI · PostgreSQL · SSE. API 계약은 `20_API명세서_v2.md`, 구조는 `docs/superpowers/specs/2026-09-03-backend-design.md`.

## 로컬 실행

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env            # 값 수정
docker compose up -d db         # Postgres만
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.seed   # 데모 데이터: demo@cardefender.kr / demo1234 (종결 사건 1건)
.venv/Scripts/python -m uvicorn app.main:app --reload
```

`db` 서비스는 호스트에 5432 포트를 열지 않는다(이유는 아래 배포 섹션 참고).
호스트에서 바로 접속하려면 `DATABASE_URL=sqlite+aiosqlite:///./data/dev.db`로 두어
Postgres 없이 돌리거나, `docker-compose.yml`의 `db` 서비스에 임시로
`ports: ["127.0.0.1:5432:5432"]`를 추가한다.

## 테스트

```bash
.venv/Scripts/python -m pytest
```

## 코드 검사

```bash
.venv/Scripts/python -m ruff check .
```

## 배포 (EC2 한 대)

```bash
docker compose up -d --build
curl localhost/api/v1/health
```

`docker-compose.yml`의 `app`, `db` 서비스는 호스트에 포트를 열지 않는다. nginx가
유일한 진입점이어야 레이트리밋(앱 프로세스 메모리 기준)과 `X-Forwarded-For` 기반
접속 IP 신뢰가 의미를 가진다. 로컬에서 `psql`로 직접 DB에 붙어야 한다면
`docker-compose.yml`의 `db` 서비스에 `ports: - "127.0.0.1:5432:5432"`처럼
루프백에만 바인딩해서 임시로 연다.

`Dockerfile`의 `uvicorn --workers 1`은 그냥 기본값이 아니라 필수 조건이다.
레이트리밋(`app/ratelimit.py`)과 SSE 허브가 프로세스 메모리에 상태를 들고 있어서,
워커가 여러 개면 요청마다 다른 워커로 흩어져 레이트리밋이 우회되고 SSE 구독이
끊길 수 있다. 수평 확장이 필요하면 워커 수를 늘리는 대신 컨테이너를 여러 개
띄우고 그 상태를 Redis 같은 공유 저장소로 옮겨야 한다.

## 환경 변수

`.env.example` 참고. `AGENT_IMPL`은 `패키지.모듈:클래스` 형식으로 AI 담당 구현체를 가리킨다. 기본값은 `app.agent.mock:MockAgent`.

## AI 담당 연동

AI 담당은 `app/agent/base.py`의 Protocol을 구현한 파이썬 클래스를 만들고, `AGENT_IMPL` 환경 변수로
그 클래스를 가리키면 된다(기본값은 데모용 `app.agent.mock:MockAgent`). 입출력 계약·필수 필드
(`judge`는 `basis.precedents[].body_text`를 반드시 채워야 한다)·에러 처리 규칙은
`docs/agent-interface.md`에 정리돼 있다.
