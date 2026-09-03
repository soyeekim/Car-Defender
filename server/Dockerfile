FROM python:3.11-slim

# 빌드 컨텍스트는 저장소 루트다(docker-compose.yml 의 context: ..).
# server/ 와 ai/ 를 한 이미지에 담아야 AGENT_IMPL 이 AI 구현체를 import 할 수 있다.

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# 의존성 설치를 코드 복사보다 먼저 둔다. 코드 한 줄만 바뀌어도 200MB짜리
# pip 레이어를 다시 만들고 다시 push하는 것을 막기 위해서다.
# editable 설치는 빌드 시점에 app 패키지가 있어야 경로를 잡으므로 빈 껍데기를
# 먼저 만들어 두고, 실제 코드는 아래에서 덮어쓴다.
COPY server/pyproject.toml server/README.md ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir -e ".[s3]"

# AI 담당 구현체가 쓰는 라이브러리. 코드보다 먼저 설치해 레이어를 재사용한다.
COPY ai/requirements.txt ./ai-requirements.txt
RUN pip install --no-cache-dir -r ai-requirements.txt

COPY server/app ./app
COPY server/alembic.ini ./
COPY server/alembic ./alembic

# WORKDIR 이 sys.path 에 들어가므로 /srv/ai 가 그대로 `import ai...` 로 잡힌다.
# AGENT_IMPL=ai.모듈:클래스 로 지정하면 백엔드가 같은 프로세스 안에서 불러 쓴다.
COPY ai ./ai

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
