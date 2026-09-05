# Fairway 백엔드

FastAPI · PostgreSQL · SSE. API 계약은 `20_API명세서_v2.md`, 구조는 `docs/superpowers/specs/2026-09-03-backend-design.md`.

## 로컬 실행

`.env.example`의 기본값이 SQLite(`sqlite+aiosqlite:///./data/dev.db`)라 아래를 그대로
복사해 붙이면 Postgres 없이 돈다.

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env               # 기본값 그대로 두면 SQLite로 돈다
mkdir -p data                      # SQLite 파일과 업로드가 여기 쌓인다
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.seed   # 데모 데이터: demo@fairway.click / demo1234 (종결 사건 1건)
.venv/Scripts/python -m uvicorn app.main:app --reload
```

로컬에서 Postgres로 돌려 보려면 `docker compose up -d db`로 DB만 띄우고 `.env`의
`DATABASE_URL`을 Postgres URL로 바꾼다. 다만 `db` 서비스는 호스트에 5432 포트를 열지
않으므로(이유는 아래 배포 섹션 참고) 호스트에서 붙으려면 `docker-compose.yml`의 `db`
서비스에 임시로 `ports: ["127.0.0.1:5432:5432"]`를 추가해야 한다.

## 테스트

```bash
.venv/Scripts/python -m pytest
```

## 코드 검사

```bash
.venv/Scripts/python -m ruff check .
```

## 배포 (EC2 한 대)

### 서버에서 직접 빌드

인스턴스 메모리가 넉넉할 때만 쓴다.

```bash
cp .env.example .env   # JWT_SECRET을 긴 랜덤 값으로, APP_ENV=prod 로 바꾼다
docker compose up -d --build
curl localhost/api/v1/health
```

### 이미지를 레지스트리로 넘기기 (권장)

t3.micro처럼 메모리 1GB인 인스턴스에서는 빌드가 무겁다(`pip install` 레이어만
196MB). 빌드는 개발 PC에서 하고 서버는 받아서 실행만 한다.

```bash
# 개발 PC
docker build -t <계정>/fairway-app:v1 .
docker push <계정>/fairway-app:v1

# EC2 (.env에 APP_IMAGE=<계정>/fairway-app:v1 를 추가한 뒤)
docker login                      # 비공개 저장소일 때만
docker compose pull
docker compose up -d --no-build
```

`docker-compose.yml`의 `app.image`는 `${APP_IMAGE:-fairway-app:local}`이다.
`.env`에 `APP_IMAGE`가 없으면 로컬 빌드 태그를 쓰고, 있으면 그 이미지를 받아 쓴다.

`Dockerfile`은 의존성 설치를 코드 복사보다 먼저 한다. 그래서 코드만 고친 배포는
5MB짜리 레이어 하나만 다시 만들어 올린다. 순서를 되돌리면 매번 196MB를 다시
올리게 되므로 그대로 둔다. editable 설치가 빌드 시점에 `app` 패키지를 찾아야 해서
빈 `app/__init__.py`를 먼저 만들고 실제 코드로 덮어쓰는 구조다.

### HTTPS까지 한 번에 (EC2 최초 1회)

`deploy/` 스크립트가 준비돼 있다. `docker-compose.prod.yml`이 443 포트와 certbot
컨테이너를 얹고, `app`의 `build:`를 지워 레지스트리 이미지만 쓰게 만든다.

```bash
git clone <저장소> && cd server
./deploy/setup-ec2.sh          # 스왑·도커·.env 준비 (사용자 데이터로 이미 했으면 건너뜀)
nano .env                      # APP_ENV=prod, JWT_SECRET, APP_IMAGE, SMTP_* 채우기
docker login                   # 비공개 저장소일 때
./deploy/issue-cert.sh me@example.com api.fairway.click
./deploy/deploy.sh
```

인증서는 발급 후 certbot 컨테이너가 12시간마다 갱신을 시도하고, nginx는 6시간마다
reload해서 갱신본을 반영한다. 손댈 것이 없다.

이후 배포는 개발 PC에서 push하고 서버에서 한 줄이다.

```bash
./deploy/deploy.sh myid/fairway-app:v2
```

헬스체크가 90초 안에 통과하지 못하면 이전 이미지로 자동 롤백한다.

도메인을 바꾸려면 `deploy/nginx-tls.conf`의 `api.fairway.click` 세 곳과
`deploy/issue-cert.sh`의 기본값을 같이 고친다.

`app` 서비스는 `env_file: .env`로 이 파일을 읽으므로 `.env`가 반드시 있어야 한다.
`DATABASE_URL`은 compose의 `app` 서비스가
`postgresql+asyncpg://fairway:fairway@db:5432/fairway`로 덮어쓰기 때문에
`.env`의 SQLite 기본값은 무시된다. compose 밖에서 앱을 띄운다면 `.env`의
`DATABASE_URL`을 그 Postgres URL로 직접 바꿔야 한다.

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

## 스토리지

`STORAGE_BACKEND=local`(기본값)을 쓴다. S3 백엔드는 인터페이스만 구현돼 있고 실제 버킷에
대고 검증한 적이 없다 — 운영에 쓰기 전에 반드시 확인이 필요하다.

## 영상 재생본 (H.264)

브라우저는 컨테이너와 코덱을 둘 다 지원해야 영상을 연다. 크롬은 mp4v·HEVC 와 `.mov`·`.avi`
컨테이너를 열지 못하므로, 업로드 중에 원본을 ffprobe 로 보고 필요할 때만 재생용 H.264 사본을
만들어 `videos/{caseId}/{videoId}_play.mp4` 에 둔다. 스트리밍은 사본이 있으면 그쪽을,
없으면 원본을 흘린다. **분석은 언제나 원본을 쓴다.** 변환이 실패하거나
`TRANSCODE_TIMEOUT_SECONDS` 를 넘기면 원본을 그대로 보여 준다(업로드는 성공한다).

이미 H.264 + mp4 인 파일은 변환 없이 지나가므로, 실제로 변환이 도는 업로드는 많지 않다.
변환은 업로드 응답 전에 끝나서 201 을 받은 시점에 이미 재생 가능하다.

배포 뒤 이미지의 ffmpeg 가 libx264 를 갖췄는지 한 번 확인해 둔다. 없으면 조용히 원본으로
폴백하기만 하고(로그에 `재생본 변환 실패` 가 남는다) 겉으로는 티가 안 난다.

```bash
docker compose exec app ffmpeg -hide_banner -encoders | grep libx264
```

## 메일 첨부 메모리

발송은 첨부 파일을 메모리로 전부 읽어 MIME으로 base64 인코딩한다. 첨부 합계 상한이
25MB이므로 발송 한 건이 순간적으로 약 60~100MB를 쓴다(원본 + 인코딩본 + 메시지 사본).
`uvicorn --workers 1` 한 프로세스에서 동시 발송이 겹치면 그만큼 곱해지니, 인스턴스
메모리를 잡을 때 감안한다.

## AI 담당 연동

AI 담당은 `app/agent/base.py`의 Protocol을 구현한 파이썬 클래스를 만들고, `AGENT_IMPL` 환경 변수로
그 클래스를 가리키면 된다(기본값은 데모용 `app.agent.mock:MockAgent`). 입출력 계약·필수 필드
(`judge`는 `basis.precedents[].body_text`를 반드시 채워야 한다)·에러 처리 규칙은
`docs/agent-interface.md`에 정리돼 있다.

## 프론트엔드 연동

`docs/frontend-guide.md` 에 세 환경(로컬 · Vercel 임시 도메인 · `fairway.click` 연결 후)의
붙는 방법과 차이가 정리돼 있다. 로컬은 `cp .env.example .env && docker compose up -d` 한 번이면
`http://localhost` 에 뜨고, `http://localhost/docs` 에서 Swagger 로 API 를 눌러 볼 수 있다.
데모 계정은 `demo@fairway.click` / `demo1234` 다.
