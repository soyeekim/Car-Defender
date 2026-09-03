FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# 의존성 설치를 코드 복사보다 먼저 둔다. 코드 한 줄만 바뀌어도 200MB짜리
# pip 레이어를 다시 만들고 다시 push하는 것을 막기 위해서다.
# editable 설치는 빌드 시점에 app 패키지가 있어야 경로를 잡으므로 빈 껍데기를
# 먼저 만들어 두고, 실제 코드는 아래에서 덮어쓴다.
COPY pyproject.toml README.md ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir -e ".[s3]"

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
