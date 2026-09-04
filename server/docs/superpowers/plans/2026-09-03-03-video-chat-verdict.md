# 카-디펜더 백엔드 3/4 — 스토리지·영상·채팅·판정 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스토리지 인터페이스(local/s3), 영상 업로드·메타·스트리밍(D-1~D-3), 분석 Job, 채팅 메시지 보내기(C-2)와 §1.1 처리 순서, 판정 Job, 판정 조회(E-1), 심의사례 설명(E-2)을 만든다. 계획 2가 끝난 상태에서 시작한다.

**Architecture:** `storage/`는 키 기반 인터페이스. `services/video.py`가 업로드·교체·분석 자동 시작을, `services/chat.py`가 유저 메시지 → Agent → `next_action` 분기를 담당한다. `next_action`은 `services/actions.py`의 레지스트리로 실행해 계획 4가 `create_report`·`create_rebuttal`을 나중에 등록한다. 판정 계산은 `services/verdict.py::perform_verdict`에 두고 판정 Job과 분석 Job(질문 없을 때) 둘 다 이것을 부른다.

**Tech Stack:** 계획 1·2와 동일. 테스트에서 ffprobe는 monkeypatch로 대체한다.

**Spec:** `docs/superpowers/specs/2026-09-03-backend-design.md` · `../금융 ai 디자인/20_API명세서_v2.md`

## Global Constraints

- 계획 1·2의 Global Constraints 전부 적용.
- 업로드는 서버 경유 멀티파트 1단계. 서버는 형식·용량을 거절하지 않는다 (명세 §8.2).
- 채팅에서 시작된 동작은 HTTP 에러 대신 **카드**로 응답한다 (명세 §1.1).
- `text` 카드의 `cta`는 assistant 메시지를 저장할 때 사건에 영상이 없으면 `upload_video`, 그 외 `null` (명세 §4.1).
- 스토리지 키: 영상 `videos/{caseId}/{videoId}.{ext}` · PDF `pdfs/{caseId}/{reportId}.pdf`.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `app/storage/__init__.py` · `base.py` · `local.py` · `s3.py` | `StorageBackend` · 구현 · `get_storage()` |
| `app/services/video.py` | ffprobe · 업로드 · 교체 · 메타 응답 · Range 파싱 |
| `app/api/videos.py` | D-1 · D-2 · D-3 |
| `app/jobs/analysis.py` | 분석 Job 핸들러 |
| `app/services/verdict.py` · `app/jobs/verdict.py` | `perform_verdict` · 판정 Job 핸들러 |
| `app/services/actions.py` | `next_action` 레지스트리 |
| `app/services/chat.py` | C-2 처리 · 백그라운드 워커 |
| `app/api/messages.py` (수정) · `app/api/verdict.py` · `app/api/precedents.py` | C-2 · E-1 · E-2 |
| `tests/...` | 각 Task |

---

### Task 1: 스토리지 인터페이스 (local · s3)

**Files:**
- Create: `app/storage/__init__.py`, `app/storage/base.py`, `app/storage/local.py`, `app/storage/s3.py`, `tests/test_storage.py`

**Interfaces:**
- `StorageBackend` Protocol:
  - `async put_file(key: str, src_path: Path) -> int` (바이트 수)
  - `async put_bytes(key: str, data: bytes) -> int`
  - `async size(key) -> int` (없으면 `FileNotFoundError`)
  - `read_range(key, start: int, end: int) -> AsyncIterator[bytes]` (end 포함)
  - `async read_bytes(key) -> bytes`
  - `async delete(key) -> None` (없어도 조용히)
  - `local_path(key) -> AsyncContextManager[Path]` (local은 원본 경로, s3는 임시 파일)
  - `healthy() -> bool`
- `get_storage() -> StorageBackend` · `reset_storage()`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_storage.py`

```python
import pytest

from app.storage import get_storage, reset_storage
from app.storage.local import LocalStorage


async def test_local_storage_roundtrip(tmp_path):
    st = LocalStorage(tmp_path / "store")
    src = tmp_path / "in.bin"
    src.write_bytes(b"0123456789")
    assert await st.put_file("videos/c/v.mp4", src) == 10
    assert await st.size("videos/c/v.mp4") == 10
    chunks = [c async for c in st.read_range("videos/c/v.mp4", 2, 5)]
    assert b"".join(chunks) == b"2345"
    assert await st.read_bytes("videos/c/v.mp4") == b"0123456789"
    async with st.local_path("videos/c/v.mp4") as p:
        assert p.read_bytes() == b"0123456789"
    await st.delete("videos/c/v.mp4")
    with pytest.raises(FileNotFoundError):
        await st.size("videos/c/v.mp4")
    await st.delete("videos/c/v.mp4")  # 두 번 지워도 조용


async def test_local_storage_put_bytes_and_key_traversal_blocked(tmp_path):
    st = LocalStorage(tmp_path / "store")
    await st.put_bytes("pdfs/c/r.pdf", b"%PDF")
    assert await st.read_bytes("pdfs/c/r.pdf") == b"%PDF"
    with pytest.raises(ValueError):
        await st.put_bytes("../escape", b"x")


def test_get_storage_returns_local_by_default(test_env):
    reset_storage()
    assert isinstance(get_storage(), LocalStorage)
    assert get_storage().healthy()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_storage.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: 코드 작성**

`app/storage/base.py`:

```python
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

CHUNK = 1024 * 1024


class StorageBackend(Protocol):
    async def put_file(self, key: str, src_path: Path) -> int: ...
    async def put_bytes(self, key: str, data: bytes) -> int: ...
    async def size(self, key: str) -> int: ...
    def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]: ...
    async def read_bytes(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    def local_path(self, key: str) -> AbstractAsyncContextManager[Path]: ...
    def healthy(self) -> bool: ...


def validate_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"잘못된 스토리지 키: {key!r}")
    return key
```

`app/storage/local.py`:

```python
import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.storage.base import CHUNK, validate_key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    async def put_file(self, key: str, src_path: Path) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, src_path, dst)
        return dst.stat().st_size

    async def put_bytes(self, key: str, data: bytes) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dst.write_bytes, data)
        return len(data)

    async def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        remaining = end - start + 1
        with open(self._path(key), "rb") as f:
            f.seek(start)
            while remaining > 0:
                data = await asyncio.to_thread(f.read, min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    async def read_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            await asyncio.to_thread(p.unlink)

    @asynccontextmanager
    async def local_path(self, key: str):
        yield self._path(key)

    def healthy(self) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False
```

`app/storage/s3.py`:

```python
import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.storage.base import CHUNK, validate_key


class S3Storage:
    def __init__(self, bucket: str, region: str | None) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    async def put_file(self, key: str, src_path: Path) -> int:
        validate_key(key)
        await asyncio.to_thread(self.client.upload_file, str(src_path), self.bucket, key)
        return src_path.stat().st_size

    async def put_bytes(self, key: str, data: bytes) -> int:
        validate_key(key)
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data)
        return len(data)

    async def size(self, key: str) -> int:
        try:
            head = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as e:
            raise FileNotFoundError(key) from e
        return int(head["ContentLength"])

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}")
        body = obj["Body"]
        while True:
            data = await asyncio.to_thread(body.read, CHUNK)
            if not data:
                break
            yield data

    async def read_bytes(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    @asynccontextmanager
    async def local_path(self, key: str):
        suffix = Path(key).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            await asyncio.to_thread(self.client.download_file, self.bucket, key, str(tmp_path))
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)

    def healthy(self) -> bool:
        return bool(self.bucket)
```

`app/storage/__init__.py`:

```python
from pathlib import Path

from app.config import get_settings
from app.storage.base import StorageBackend

_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            from app.storage.s3 import S3Storage

            _storage = S3Storage(s.s3_bucket or "", s.s3_region)
        else:
            from app.storage.local import LocalStorage

            _storage = LocalStorage(Path(s.storage_local_dir))
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None


__all__ = ["StorageBackend", "get_storage", "reset_storage"]
```

`tests/conftest.py`의 `test_env`에 `from app.storage import reset_storage; reset_storage()`를 yield 앞뒤에 추가한다. `app/api/health.py`의 `_check_storage`는 `get_storage().healthy()`로 교체한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_storage.py tests/api/test_health.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/storage app/api/health.py tests/conftest.py tests/test_storage.py
git commit -m "feat(storage): 로컬·S3 스토리지 인터페이스"
```

---

### Task 2: 영상 업로드 · 메타 조회 · 스트리밍 (D-1 ~ D-3)

**Files:**
- Create: `app/services/video.py`, `app/api/videos.py`, `tests/api/test_videos.py`
- Modify: `app/main.py`, `app/services/cases.py` (삭제 시 스토리지 정리), `tests/conftest.py`

**Interfaces:**
- `probe_video(path: Path) -> tuple[int | None, datetime | None]` (duration_sec, recorded_at). ffprobe 실패 시 `(None, None)`
- `upload_video(db, case, upload: UploadFile) -> tuple[Video, Job | None, bool]` — (video, analysis_job, needs_description). 분석 Job 시작은 `start_analysis(db, case)`를 통해 (Task 3에서 핸들러 연결. 이 Task에서는 `app/jobs/analysis.py`에 **핸들러 자리**를 두지 않고 `start_analysis`가 `run_analysis`를 import한다 — Task 3 전까지 Task 2 테스트는 설명 없이 올리는 경우만 검사)
- `video_detail(video, case_id) -> dict` (D-2) · `duration_label(sec)` · `impact_label(sec)`
- `parse_range(header: str | None, size: int) -> tuple[int, int] | None`
- 픽스처 `fake_probe` (autouse): `app.services.video.probe_video`를 `(42, 2026-08-22T14:02:17+09:00)`로 대체
- 픽스처 `upload(client, auth_headers, case_id, filename="blackbox_0822.mp4", content=b"...")`

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_videos.py`

```python
from app.services.video import duration_label, parse_range


def test_parse_range():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=90-", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=0-500", 100) == (0, 99)
    assert parse_range("bytes=200-300", 100) is None
    assert parse_range("garbage", 100) is None


def test_duration_label():
    assert duration_label(42) == "42초"
    assert duration_label(72) == "1분 12초"
    assert duration_label(None) == ""


async def test_upload_without_description_asks_for_it(client, auth_headers, case_id, sse):
    events = await sse(case_id)
    files = {"file": ("blackbox_0822.mp4", b"\x00" * 2048, "video/mp4")}
    res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["video"]["filename"] == "blackbox_0822.mp4"
    assert body["video"]["sizeBytes"] == 2048 and body["video"]["sizeLabel"] == "2KB"
    assert body["video"]["durationSec"] == 42
    assert body["video"]["recordedAt"] == "2026-08-22T14:02:17+09:00"
    assert body["analysis"] == {"started": False, "jobId": None}
    assert body["needsDescription"] is True

    frames = await events.take(2)
    assert "video_attachment" in frames[0] and '"role": "user"' in frames[0]
    assert "영상 잘 받았어요" in frames[1]

    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["video"]["filename"] == "blackbox_0822.mp4"
    assert detail["subtitle"].endswith("· 블랙박스 1건")
    assert detail["status"] == "intake"


async def test_upload_replaces_existing_video(client, auth_headers, case_id, upload):
    v1 = (await upload(filename="a.mp4")).json()["video"]["id"]
    v2 = (await upload(filename="b.mp4")).json()["video"]["id"]
    assert v1 != v2
    assert (await client.get(f"/videos/{v1}", headers=auth_headers)).status_code == 404
    assert (await client.get(f"/videos/{v2}", headers=auth_headers)).status_code == 200


async def test_video_meta_and_stream(client, auth_headers, case_id, upload):
    content = bytes(range(256)) * 8
    video_id = (await upload(content=content)).json()["video"]["id"]
    res = await client.get(f"/videos/{video_id}", headers=auth_headers)
    assert res.status_code == 200
    meta = res.json()
    assert meta["caseId"] == case_id and meta["durationLabel"] == "42초" and meta["meta"] is None
    assert meta["notice"] == "영상은 이 사건 처리에만 쓰이며, 사건을 지우면 함께 지워집니다."
    assert meta["streamUrl"].startswith(f"/api/v1/videos/{video_id}/stream?t=")

    stream_url = meta["streamUrl"].removeprefix("/api/v1")
    res = await client.get(stream_url, headers={"Range": "bytes=0-1023"})
    assert res.status_code == 206
    assert res.headers["content-range"] == f"bytes 0-1023/{len(content)}"
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["cache-control"] == "private, no-store"
    assert res.content == content[:1024]

    res = await client.get(stream_url)
    assert res.status_code == 200 and res.content == content

    res = await client.get(f"/videos/{video_id}/stream", params={"t": "bad"})
    assert res.status_code == 401


async def test_video_forbidden_for_other_user(client, auth_headers, case_id, upload):
    video_id = (await upload()).json()["video"]["id"]
    other = await client.post("/auth/signup", json={
        "email": "other@example.com", "password": "carguard12", "passwordConfirm": "carguard12",
        "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True}})
    h = {"Authorization": f"Bearer {other.json()['accessToken']}"}
    assert (await client.get(f"/videos/{video_id}", headers=h)).status_code == 403


async def test_delete_case_removes_video_file(client, auth_headers, case_id, upload):
    from app.storage import get_storage

    video_id = (await upload()).json()["video"]["id"]
    key = f"videos/{case_id}/{video_id}.mp4"
    assert await get_storage().size(key) > 0
    await client.delete(f"/cases/{case_id}", headers=auth_headers)
    import pytest

    with pytest.raises(FileNotFoundError):
        await get_storage().size(key)
```

`tests/conftest.py`에 추가:

```python
import asyncio
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def fake_probe(monkeypatch):
    from app.services import video as video_service

    monkeypatch.setattr(video_service, "probe_video", lambda path: (42, datetime(2026, 8, 22, 14, 2, 17, tzinfo=KST)))


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
async def sse():
    taps = []

    async def _open(case_id):
        tap = await EventTap(case_id).start()
        taps.append(tap)
        return tap

    yield _open
    for t in taps:
        await t.close()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_videos.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: app/services/video.py**

```python
import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc, to_kst_iso
from app.config import get_settings
from app.content.texts import NEED_DESCRIPTION_TEXT, VIDEO_NOTICE
from app.ids import new_id
from app.models import Case, Job, Message, Video
from app.security import create_stream_token
from app.services import cases as case_service
from app.services.presenters import size_label
from app.storage import get_storage

EXT_BY_MIME = {"video/mp4": "mp4", "video/quicktime": "mov", "video/x-msvideo": "avi"}


def probe_video(path: Path) -> tuple[int | None, datetime | None]:
    """ffprobe로 길이(초)와 촬영 시각을 뽑는다. 실패하면 (None, None)."""
    bin_ = get_settings().ffprobe_bin
    try:
        out = subprocess.run(
            [bin_, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
        fmt = json.loads(out or "{}").get("format", {})
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None, None
    duration = fmt.get("duration")
    duration_sec = int(float(duration)) if duration else None
    recorded_at = None
    raw = (fmt.get("tags") or {}).get("creation_time")
    if raw:
        try:
            recorded_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            recorded_at = None
    return duration_sec, recorded_at


def duration_label(sec: int | None) -> str:
    if sec is None:
        return ""
    m, s = divmod(int(sec), 60)
    return f"{m}분 {s}초" if m else f"{s}초"


def impact_label(sec: int | None) -> str | None:
    if sec is None:
        return None
    m, s = divmod(int(sec), 60)
    return f"충돌 시점으로 이동 ({m}:{s:02d})"


def video_key(case_id: str, video_id: str, mime: str) -> str:
    return f"videos/{case_id}/{video_id}.{EXT_BY_MIME.get(mime, 'mp4')}"


def attachment_payload(video: Video) -> dict:
    meta = video.meta
    return {
        "videoId": video.id,
        "filename": video.filename,
        "durationSec": video.duration_sec,
        "sizeLabel": size_label(video.size_bytes),
        "recordedAt": to_kst_iso(video.recorded_at),
        "meta": {"speedKph": meta.get("speedKph"), "impactAtSec": meta.get("impactAtSec")} if meta else None,
    }


async def has_user_text(db: AsyncSession, case_id: str) -> bool:
    stmt = select(Message.id).where(Message.case_id == case_id, Message.role == "user", Message.type == "text").limit(1)
    return (await db.execute(stmt)).first() is not None


async def upload_video(db: AsyncSession, case: Case, upload: UploadFile) -> tuple[Video, Job | None, bool]:
    mime = upload.content_type or "video/mp4"
    video_id = new_id()
    key = video_key(case.id, video_id, mime)

    with tempfile.NamedTemporaryFile(suffix=f".{EXT_BY_MIME.get(mime, 'mp4')}", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        await asyncio.to_thread(shutil.copyfileobj, upload.file, tmp, 1024 * 1024)
    try:
        duration_sec, recorded_at = await asyncio.to_thread(probe_video, tmp_path)
        size = await get_storage().put_file(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    old = await case_service.get_video(db, case.id)
    if old is not None:
        await get_storage().delete(old.storage_key)
        await db.delete(old)
        await db.flush()

    video = Video(
        id=video_id, case_id=case.id, filename=upload.filename or "video.mp4", size_bytes=size,
        duration_sec=duration_sec, mime_type=mime, recorded_at=recorded_at, meta=None,
        storage_key=key, created_at=now_utc(),
    )
    db.add(video)
    case_service.touch(case)
    await db.commit()

    await case_service.add_message(db, case.id, "user", "video_attachment", attachment_payload(video))

    if await has_user_text(db, case.id):
        from app.jobs.analysis import start_analysis

        job = await start_analysis(db, case)
        return video, job, False

    await case_service.add_message(db, case.id, "assistant", "text", {"text": NEED_DESCRIPTION_TEXT, "cta": None})
    await case_service.publish_case_updated(db, case.id)
    return video, None, True


def video_detail(video: Video) -> dict:
    meta = video.meta or None
    return {
        "id": video.id,
        "caseId": video.case_id,
        "filename": video.filename,
        "sizeLabel": size_label(video.size_bytes),
        "durationSec": video.duration_sec,
        "durationLabel": duration_label(video.duration_sec),
        "recordedAt": to_kst_iso(video.recorded_at),
        "meta": {
            "speedKph": meta.get("speedKph"),
            "impactAtSec": meta.get("impactAtSec"),
            "impactLabel": impact_label(meta.get("impactAtSec")),
        } if meta else None,
        "streamUrl": f"/api/v1/videos/{video.id}/stream?t={create_stream_token(video.id)}",
        "notice": VIDEO_NOTICE,
    }


RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header:
        return None
    m = RANGE_RE.match(header.strip())
    if not m or size <= 0:
        return None
    start_s, end_s = m.groups()
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        length = min(int(end_s), size)
        return size - length, size - 1
    start = int(start_s)
    if start >= size:
        return None
    end = min(int(end_s), size - 1) if end_s else size - 1
    return start, end
```

`app/services/cases.py`의 `delete_case`를 스토리지 정리 포함으로 교체:

```python
async def delete_case(db: AsyncSession, case: Case) -> None:
    from app.storage import get_storage

    storage = get_storage()
    video = await get_video(db, case.id)
    if video is not None:
        await storage.delete(video.storage_key)
    for report in (await db.execute(select(Report).where(Report.case_id == case.id))).scalars().all():
        pdf = (await db.execute(select(ReportPdf).where(ReportPdf.report_id == report.id))).scalar_one_or_none()
        if pdf is not None:
            await storage.delete(pdf.storage_key)
    await db.delete(case)
    await db.commit()
```

(`ReportPdf`를 `app.models`에서 import에 추가.)

- [ ] **Step 4: app/jobs/analysis.py의 start_analysis (핸들러 본문은 Task 3)**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.runner import runner
from app.models import Case, Job
from app.services import cases as case_service


async def start_analysis(db: AsyncSession, case: Case) -> Job:
    case_service.set_status(case, "analyzing")
    await db.commit()
    return await runner.start(db, case.id, "analysis", run_analysis)


async def run_analysis(db: AsyncSession, case_id: str, job_id: str) -> None:
    raise NotImplementedError("Task 3에서 구현")
```

- [ ] **Step 5: 라우터** — `app/api/videos.py`

```python
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_kst_iso
from app.db import get_db
from app.deps import current_user, owned_case
from app.errors import ApiError
from app.models import Case, User, Video
from app.security import decode_stream_token
from app.services import video as video_service
from app.services.presenters import size_label
from app.storage import get_storage

router = APIRouter(tags=["videos"])


@router.post("/cases/{case_id}/videos", status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile = File(...), case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    video, job, needs_description = await video_service.upload_video(db, case, file)
    return {
        "video": {
            "id": video.id, "filename": video.filename, "sizeBytes": video.size_bytes,
            "sizeLabel": size_label(video.size_bytes), "durationSec": video.duration_sec,
            "mimeType": video.mime_type, "recordedAt": to_kst_iso(video.recorded_at),
        },
        "analysis": {"started": job is not None, "jobId": job.id if job else None},
        "needsDescription": needs_description,
    }


async def _owned_video(video_id: str, user: User, db: AsyncSession) -> Video:
    video = await db.get(Video, video_id)
    if video is None:
        raise ApiError("NOT_FOUND")
    case = await db.get(Case, video.case_id)
    if case is None or case.user_id != user.id:
        raise ApiError("FORBIDDEN")
    return video


@router.get("/videos/{video_id}")
async def video_meta(video_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return video_service.video_detail(await _owned_video(video_id, user, db))


@router.get("/videos/{video_id}/stream")
async def stream(video_id: str, request: Request, t: str = Query(...), db: AsyncSession = Depends(get_db)):
    if decode_stream_token(t) != video_id:
        raise ApiError("FORBIDDEN")
    video = await db.get(Video, video_id)
    if video is None:
        raise ApiError("NOT_FOUND")
    storage = get_storage()
    try:
        size = await storage.size(video.storage_key)
    except FileNotFoundError as e:
        raise ApiError("NOT_FOUND") from e
    rng = video_service.parse_range(request.headers.get("range"), size)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
    if rng is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(storage.read_range(video.storage_key, 0, size - 1), media_type=video.mime_type, headers=headers)
    start, end = rng
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(storage.read_range(video.storage_key, start, end), status_code=206, media_type=video.mime_type, headers=headers)
```

`app/main.py`에 `videos.router` 등록.

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_videos.py tests/api/test_cases.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/services/video.py app/services/cases.py app/api/videos.py app/jobs/analysis.py app/main.py tests/conftest.py tests/api/test_videos.py
git commit -m "feat(videos): 영상 업로드·교체·메타 조회·Range 스트리밍"
```

---

### Task 3: 판정 계산과 분석 Job

**Files:**
- Create: `app/services/verdict.py`, `app/jobs/verdict.py`, `tests/test_analysis_job.py`
- Modify: `app/jobs/analysis.py`

**Interfaces:**
- `services/verdict.py`:
  - `recent_turns(db, case_id, limit=40) -> list[ChatTurn]` (text 카드만, 오래된 → 최신)
  - `snapshot(verdict: Verdict | None) -> VerdictSnapshot | None`
  - `perform_verdict(db, case_id) -> Verdict` — Agent `judge` → 이전 비활성화 → INSERT(version+1) → 상태 `judged` → `verdict` 카드 → `case.updated`
  - `merge_facts(db, case_id, updates: dict) -> None`
- `jobs/verdict.py`: `run_verdict(db, case_id, job_id)` (perform_verdict 호출) · `start_verdict(db, case) -> Job`
- `jobs/analysis.py`: `run_analysis(db, case_id, job_id)` 완성

- [ ] **Step 1: 실패하는 테스트** — `tests/test_analysis_job.py`

```python
from sqlalchemy import select

from app.db import session_scope
from app.jobs.runner import runner
from app.models import Analysis, Case, Message, Verdict, Video
from app.services.cases import add_message
from app.services.verdict import perform_verdict


async def test_analysis_job_full_path(client, auth_headers, case_id, upload, sse):
    async with session_scope() as db:
        await add_message(db, case_id, "user", "text", {"text": "교차로에서 오토바이가 박았어요"}, publish=False)
    tap = await sse(case_id)
    res = await upload()
    assert res.json()["analysis"]["started"] is True
    await runner.wait_all()

    frames = await tap.take(7)
    kinds = [(f.split("event: ")[1].split("\n")[0]) for f in frames]
    # video_attachment 카드 · case.updated(analyzing, Job 시작) · 요약 text · 질문 text · message.updated(meta)
    # · case.updated(needs_review, 핸들러 안 — Job은 아직 running) · case.updated(Job 성공 후 — activeJob null)
    assert kinds == ["message.created", "case.updated", "message.created", "message.created", "message.updated", "case.updated", "case.updated"]
    assert '"status": "analyzing"' in frames[1] and '"kind": "analysis"' in frames[1]
    assert "영상을 분석했어요" in frames[2] and '"cta": null' in frames[2]
    assert "1/2" in frames[3]
    assert '"speedKph": 48' in frames[4]
    assert '"status": "needs_review"' in frames[5] and '"title": "교차로 직진 충돌 · 08-22"' in frames[5]
    assert '"status": "needs_review"' in frames[6] and '"activeJob": null' in frames[6]

    async with session_scope() as db:
        a = await db.get(Analysis, case_id)
        assert a.facts["opponent_signal"] == "red" and a.questions
        v = (await db.execute(select(Video).where(Video.case_id == case_id))).scalar_one()
        assert v.meta == {"speedKph": 48, "impactAtSec": 31}
        c = await db.get(Case, case_id)
        assert c.status == "needs_review"


async def test_analysis_without_questions_goes_straight_to_verdict(client, auth_headers, case_id, upload, monkeypatch):
    from app.agent.base import AnalyzeResult, VideoMeta
    from app.agent.loader import get_agent

    async def analyze(inp):
        return AnalyzeResult(summary_text="요약", facts={"impact_part": "x", "my_signal": "green"}, questions=[], title="바로 판정 · 08-22", video_meta=VideoMeta())

    monkeypatch.setattr(get_agent()._impl, "analyze", analyze)
    async with session_scope() as db:
        await add_message(db, case_id, "user", "text", {"text": "설명"}, publish=False)
    await upload()
    await runner.wait_all()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged" and detail["verdict"]["ratio"] == {"mine": 0, "other": 100}
    assert detail["stages"]["fault_ratio"] == {"state": "done"}


async def test_perform_verdict_versions_and_cards(client, auth_headers, case_id, sse):
    tap = await sse(case_id)
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={}, questions=[], created_at=__import__("app.clock", fromlist=["now_utc"]).now_utc(), updated_at=__import__("app.clock", fromlist=["now_utc"]).now_utc()))
        await db.commit()
        v1 = await perform_verdict(db, case_id)
        assert v1.version == 1 and v1.is_active
        a = await db.get(Analysis, case_id)
        a.facts = {"opponent_signal": "yellow"}
        await db.commit()
        v2 = await perform_verdict(db, case_id)
        assert v2.version == 2 and v2.change_reason and (v2.ratio_mine, v2.ratio_other) == (20, 80)
        await db.refresh(v1)
        assert v1.is_active is False
        assert v2.basis["precedents"][0]["body_text"]

    frames = await tap.take(4)
    assert "event: message.created" in frames[0] and '"type": "verdict"' in frames[0] and '"version": 1' in frames[0]
    assert "body_text" not in frames[0] and '"canCreateReport": true' in frames[0]
    assert '"status": "judged"' in frames[1]
    assert '"version": 2' in frames[2] and '"changeReason": "상대' in frames[2]
    assert '"ratio": {"mine": 20, "other": 80}' in frames[3]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_analysis_job.py -v`
Expected: FAIL

- [ ] **Step 3: app/services/verdict.py**

```python
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import ChatTurn, JudgeInput, VerdictSnapshot
from app.agent.loader import get_agent
from app.clock import now_utc
from app.ids import new_id
from app.models import Analysis, Case, Message, Verdict
from app.services import cases as case_service
from app.services.presenters import verdict_payload


async def recent_turns(db: AsyncSession, case_id: str, limit: int = 40, exclude_id: str | None = None) -> list[ChatTurn]:
    stmt = select(Message).where(Message.case_id == case_id, Message.type == "text").order_by(Message.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    rows = [m for m in rows if m.id != exclude_id][:limit]
    rows.reverse()
    return [ChatTurn(role=m.role, text=m.payload.get("text", "")) for m in rows]


def snapshot(v: Verdict | None) -> VerdictSnapshot | None:
    if v is None:
        return None
    return VerdictSnapshot(version=v.version, ratio_mine=v.ratio_mine, ratio_other=v.ratio_other, summary=v.summary, opponent_claim=v.opponent_claim, basis=v.basis or {})


async def merge_facts(db: AsyncSession, case_id: str, updates: dict) -> None:
    if not updates:
        return
    analysis = await db.get(Analysis, case_id)
    if analysis is None:
        now = now_utc()
        analysis = Analysis(case_id=case_id, summary_text="", facts={}, questions=[], created_at=now, updated_at=now)
        db.add(analysis)
    analysis.facts = {**(analysis.facts or {}), **updates}
    analysis.updated_at = now_utc()
    await db.commit()


async def perform_verdict(db: AsyncSession, case_id: str) -> Verdict:
    case = await db.get(Case, case_id)
    analysis = await db.get(Analysis, case_id)
    previous = await case_service.active_verdict(db, case_id)
    result = await get_agent().judge(JudgeInput(
        messages=await recent_turns(db, case_id),
        facts=(analysis.facts if analysis else {}),
        previous_verdict=snapshot(previous),
    ))
    await db.execute(update(Verdict).where(Verdict.case_id == case_id).values(is_active=False))
    verdict = Verdict(
        id=new_id(), case_id=case_id, version=(previous.version + 1) if previous else 1,
        ratio_mine=result.ratio_mine, ratio_other=result.ratio_other, summary=result.summary,
        change_reason=result.change_reason if previous else None,
        opponent_claim=result.opponent_claim.model_dump() if result.opponent_claim else None,
        basis=result.basis.model_dump(), is_active=True, created_at=now_utc(),
    )
    db.add(verdict)
    case_service.set_status(case, "judged")
    await db.commit()
    await case_service.add_message(db, case_id, "assistant", "verdict", verdict_payload(verdict))
    await case_service.publish_case_updated(db, case_id)
    return verdict
```

- [ ] **Step 4: app/jobs/verdict.py**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.runner import runner
from app.models import Case, Job
from app.services.verdict import perform_verdict


async def run_verdict(db: AsyncSession, case_id: str, job_id: str) -> None:
    await perform_verdict(db, case_id)


async def start_verdict(db: AsyncSession, case: Case) -> Job:
    return await runner.start(db, case.id, "verdict", run_verdict)
```

- [ ] **Step 5: app/jobs/analysis.py의 run_analysis 완성**

```python
from sqlalchemy import select

from app.agent.base import AnalyzeInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.models import Analysis, Message
from app.services.verdict import perform_verdict
from app.services.video import attachment_payload
from app.storage import get_storage


async def run_analysis(db: AsyncSession, case_id: str, job_id: str) -> None:
    case = await db.get(Case, case_id)
    video = await case_service.get_video(db, case_id)
    if case is None or video is None:
        raise RuntimeError("분석할 영상이 없어요")

    stmt = select(Message).where(Message.case_id == case_id, Message.role == "user", Message.type == "text").order_by(Message.id)
    description = "\n".join(m.payload.get("text", "") for m in (await db.execute(stmt)).scalars().all())

    async with get_storage().local_path(video.storage_key) as path:
        result = await get_agent().analyze(AnalyzeInput(video_path=str(path), video_mime=video.mime_type, description=description))

    now = now_utc()
    analysis = await db.get(Analysis, case_id)
    if analysis is None:
        analysis = Analysis(case_id=case_id, summary_text=result.summary_text, facts=result.facts, questions=result.questions, created_at=now, updated_at=now)
        db.add(analysis)
    else:
        analysis.summary_text = result.summary_text
        analysis.facts = {**(analysis.facts or {}), **result.facts}
        analysis.questions = result.questions
        analysis.updated_at = now
    case.title = result.title[:60] if result.title else case.title
    if result.video_meta:
        video.meta = {"speedKph": result.video_meta.speed_kph, "impactAtSec": result.video_meta.impact_at_sec}
    await db.commit()

    await case_service.add_message(db, case_id, "assistant", "text", {"text": result.summary_text, "cta": None})
    if result.questions:
        await case_service.add_message(db, case_id, "assistant", "text", {"text": result.questions[0], "cta": None})

    stmt = select(Message).where(Message.case_id == case_id, Message.type == "video_attachment").order_by(Message.id.desc())
    card = (await db.execute(stmt)).scalars().first()
    if card is not None and card.payload.get("videoId") == video.id:
        await case_service.update_message(db, card, attachment_payload(video))

    if result.questions:
        case_service.set_status(case, "needs_review")
        await db.commit()
        await case_service.publish_case_updated(db, case_id)
    else:
        case_service.set_status(case, "needs_review")
        await db.commit()
        await perform_verdict(db, case_id)
```

(파일 상단 import에 `Case`, `case_service`가 이미 있음.)

- [ ] **Step 5b: Job 성공 후에도 `case.updated`를 보내도록 runner 수정**

핸들러 안에서 보낸 `case.updated`는 Job 행이 아직 `running`이라 `activeJob`이 채워져 있다. 프론트가 로딩을 지우려면 Job이 `succeeded`로 바뀐 **뒤** `activeJob: null`인 `case.updated`가 한 번 더 와야 한다. `app/jobs/runner.py`의 `_run` 성공 경로를 다음으로 바꾼다:

```python
            async with session_scope() as db:
                await db.execute(update(Job).where(Job.id == job_id).values(status="succeeded", ended_at=now_utc()))
                await db.commit()
                try:
                    await publish_case_updated(db, case_id)
                except ApiError:
                    pass  # 사건이 삭제된 경우
```

`tests/test_job_runner.py::test_runner_runs_handler_and_marks_succeeded`에는 성공 후 프레임이 하나 더 온다. 기존 단언은 그대로 통과하지만, `await runner.wait_all()` 뒤에 다음 두 줄을 추가해 명시적으로 확인한다:

```python
    done_frame = await it.__anext__()
    assert "event: case.updated" in done_frame and '"activeJob": null' in done_frame
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_analysis_job.py tests/test_job_runner.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/services/verdict.py app/jobs tests/test_analysis_job.py tests/test_job_runner.py
git commit -m "feat(verdict): 판정 계산·버전 누적과 분석 Job 핸들러"
```

---

### Task 4: 채팅 메시지 보내기 (C-2) · next_action 레지스트리

**Files:**
- Create: `app/services/actions.py`, `app/services/chat.py`, `app/schemas/chat.py`, `tests/api/test_chat.py`
- Modify: `app/api/messages.py`, `app/main.py` (종료 시 `chat.wait_all`), `tests/conftest.py` (`settle` 픽스처)

**Interfaces:**
- `actions.py`: `ActionFn = Callable[[AsyncSession, Case], Awaitable[None]]` · `register_action(name, fn)` · `run_action(name, db, case)` (미등록이면 로그만) · `registered() -> list[str]`
- `chat.py`: `send_user_message(db, case, text) -> Message` (Job 검사 → 저장·송출 → 백그라운드 등록) · `process_user_message(case_id, message_id)` · `wait_all()` · 사건별 `asyncio.Lock`
- `POST /cases/{caseId}/messages` → `202 {message, assistantPending: true}`
- 픽스처 `settle()`: chat·runner 태스크가 모두 끝날 때까지 반복 대기

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_chat.py`

```python
from app.db import session_scope
from app.services.actions import register_action


async def send(client, h, case_id, text):
    return await client.post(f"/cases/{case_id}/messages", json={"text": text}, headers=h)


async def test_send_validation(client, auth_headers, case_id):
    assert (await send(client, auth_headers, case_id, "")).status_code == 422
    assert (await send(client, auth_headers, case_id, "x" * 2001)).status_code == 422


async def test_message_before_video_gets_upload_cta(client, auth_headers, case_id, sse, settle):
    tap = await sse(case_id)
    res = await send(client, auth_headers, case_id, "어제 교차로에서 오토바이가 박았어요")
    assert res.status_code == 202
    body = res.json()
    assert body["assistantPending"] is True and body["message"]["role"] == "user" and body["message"]["payload"] == {"text": "어제 교차로에서 오토바이가 박았어요"}
    await settle()
    frames = await tap.take(2)
    assert '"role": "user"' in frames[0]
    assert '"cta": {"type": "upload_video", "label": "영상 올리기"}' in frames[1]


async def test_video_then_description_starts_analysis(client, auth_headers, case_id, upload, settle):
    await upload()
    await settle()
    await send(client, auth_headers, case_id, "교차로에서 오토바이가 박았어요")
    await settle()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "needs_review" and detail["title"] == "교차로 직진 충돌 · 08-22"


async def test_full_conversation_to_verdict_and_rejudge(client, auth_headers, case_id, upload, settle):
    await send(client, auth_headers, case_id, "교차로에서 오토바이가 박았어요")
    await settle()
    await upload()
    await settle()

    r = await send(client, auth_headers, case_id, "우측 앞펜더요.")
    assert r.status_code == 202
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert "2/2" in msgs[-1]["payload"]["text"]

    await send(client, auth_headers, case_id, "초록불이었어요.")
    await settle()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged" and detail["verdict"]["version"] == 1
    verdict = (await client.get(f"/cases/{case_id}/verdict", headers=auth_headers)).json()
    assert verdict["ratio"] == {"mine": 0, "other": 100} and verdict["basis"]["precedents"][0]["id"] == "2019-018856"

    await send(client, auth_headers, case_id, "다시 보니까 상대 신호가 황색이었던 것 같아요.")
    await settle()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged" and detail["verdict"]["version"] == 2 and detail["verdict"]["ratio"] == {"mine": 20, "other": 80}
    msgs = (await client.get(f"/cases/{case_id}/messages", params={"limit": 50}, headers=auth_headers)).json()["items"]
    verdict_cards = [m for m in msgs if m["type"] == "verdict"]
    assert [v["payload"]["version"] for v in verdict_cards] == [1, 2]
    assert verdict_cards[1]["payload"]["changeReason"]


async def test_send_blocked_while_analysis_running(client, auth_headers, case_id, upload, settle, monkeypatch):
    import asyncio

    from app.agent.loader import get_agent

    gate = asyncio.Event()
    original = get_agent()._impl.analyze

    async def slow(inp):
        await gate.wait()
        return await original(inp)

    monkeypatch.setattr(get_agent()._impl, "analyze", slow)
    await send(client, auth_headers, case_id, "설명")
    await settle()
    await upload()
    res = await send(client, auth_headers, case_id, "또 보냄")
    assert res.status_code == 409 and res.json()["error"]["code"] == "JOB_ALREADY_RUNNING"
    gate.set()
    await settle()


async def test_unregistered_action_is_noop_and_api_error_becomes_text_card(client, auth_headers, case_id, settle, monkeypatch):
    # 영상을 올리지 않는다: 영상이 있고 분석이 없으면 chat 대신 분석 Job이 돌기 때문 (§1.1 ②′)
    from app.agent.base import ChatResult
    from app.agent.loader import get_agent
    from app.errors import ApiError

    async def chat(inp):
        return ChatResult(reply="경위서 만들게요", next_action="create_report")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)

    async def failing(db, case):
        raise ApiError("REPORT_VERDICT_REQUIRED")

    register_action("create_report", failing)
    await send(client, auth_headers, case_id, "경위서")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["payload"]["text"] == "과실비율 판정이 끝나면 경위서를 만들 수 있어요."


async def test_rebuttal_locked_error_becomes_locked_card(client, auth_headers, case_id, settle, monkeypatch):
    from app.agent.base import ChatResult
    from app.agent.loader import get_agent
    from app.errors import ApiError

    async def chat(inp):
        return ChatResult(reply="반박 준비", next_action="create_rebuttal")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)

    async def locked(db, case):
        raise ApiError("REBUTTAL_LOCKED", fields={"missing": "verdict,report"})

    register_action("create_rebuttal", locked)
    await send(client, auth_headers, case_id, "반박")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    card = msgs[-1]
    assert card["type"] == "rebuttal_locked" and card["payload"]["missing"] == ["verdict", "report"]
    assert card["payload"]["buttonHint"] == "경위서를 만들면 열려요"


async def test_agent_crash_sends_internal_error_text(client, auth_headers, case_id, settle, monkeypatch):
    from app.agent.loader import get_agent

    async def chat(inp):
        raise RuntimeError("llm down")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)
    await send(client, auth_headers, case_id, "hi")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["payload"]["text"].startswith("잠시 후 다시 시도해 주세요")
```

`tests/conftest.py`에 추가:

```python
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
    return _settle
```

`tests/conftest.py`의 `_reset_hub` 옆에 액션 레지스트리 초기화도 추가:

```python
@pytest.fixture(autouse=True)
def _reset_actions():
    from app.services import actions

    saved = dict(actions._registry)
    yield
    actions._registry.clear()
    actions._registry.update(saved)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_chat.py -v`
Expected: FAIL

- [ ] **Step 3: app/services/actions.py**

```python
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Case

log = logging.getLogger(__name__)

ActionFn = Callable[[AsyncSession, Case], Awaitable[None]]
_registry: dict[str, ActionFn] = {}


def register_action(name: str, fn: ActionFn) -> None:
    _registry[name] = fn


def registered() -> list[str]:
    return sorted(_registry)


async def run_action(name: str, db: AsyncSession, case: Case) -> None:
    fn = _registry.get(name)
    if fn is None:
        log.warning("next_action %r 은 등록된 처리기가 없어요 (case %s)", name, case.id)
        return
    await fn(db, case)
```

- [ ] **Step 4: app/schemas/chat.py**

```python
from pydantic import Field

from app.schemas.base import CamelModel


class SendMessageRequest(CamelModel):
    text: str = Field(min_length=1, max_length=2000)
```

- [ ] **Step 5: app/services/chat.py**

```python
import asyncio
import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import ChatInput
from app.agent.loader import get_agent
from app.content.texts import REBUTTAL_LOCKED_CARD, UPLOAD_CTA
from app.db import session_scope
from app.errors import ERROR_CATALOG, ApiError
from app.jobs.analysis import start_analysis
from app.jobs.verdict import start_verdict
from app.models import Case, Message
from app.services import cases as case_service
from app.services.actions import register_action, run_action
from app.services.verdict import merge_facts, recent_turns, snapshot

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def pending() -> int:
    return len(_tasks)


async def wait_all() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def send_user_message(db: AsyncSession, case: Case, text: str) -> Message:
    job = await case_service.active_job(db, case.id)
    if job is not None and job.kind in ("analysis", "verdict"):
        raise ApiError("JOB_ALREADY_RUNNING")
    msg = await case_service.add_message(db, case.id, "user", "text", {"text": text})
    task = asyncio.create_task(process_user_message(case.id, msg.id), name=f"chat:{case.id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return msg


async def _assistant_text(db: AsyncSession, case_id: str, text: str) -> None:
    has_video = await case_service.get_video(db, case_id) is not None
    await case_service.add_message(db, case_id, "assistant", "text", {"text": text, "cta": None if has_video else dict(UPLOAD_CTA)})


async def process_user_message(case_id: str, message_id: str) -> None:
    async with _locks[case_id]:
        async with session_scope() as db:
            try:
                await _process(db, case_id, message_id)
            except Exception:  # noqa: BLE001
                log.exception("chat processing failed for case %s", case_id)
                try:
                    await _assistant_text(db, case_id, ERROR_CATALOG["INTERNAL_ERROR"].message)
                except Exception:  # noqa: BLE001
                    log.exception("could not even send the error card")


async def _process(db: AsyncSession, case_id: str, message_id: str) -> None:
    case = await db.get(Case, case_id)
    if case is None:
        return
    video = await case_service.get_video(db, case_id)
    analysis = await case_service.get_analysis(db, case_id)
    if video is not None and analysis is None:
        try:
            await start_analysis(db, case)
        except ApiError as e:
            log.info("analysis not started: %s", e.code)
        return

    new_msg = await db.get(Message, message_id)
    verdict = await case_service.active_verdict(db, case_id)
    result = await get_agent().chat(ChatInput(
        messages=await recent_turns(db, case_id, exclude_id=message_id),
        new_message=new_msg.payload.get("text", "") if new_msg else "",
        facts=analysis.facts if analysis else {},
        questions=analysis.questions if analysis else [],
        verdict=snapshot(verdict),
        has_video=video is not None,
        has_report=await case_service.latest_report(db, case_id) is not None,
    ))
    await merge_facts(db, case_id, result.fact_updates)
    await _assistant_text(db, case_id, result.reply)

    if result.next_action == "none":
        return
    try:
        if result.next_action in ("verdict", "rejudge"):
            await start_verdict(db, case)
        else:
            await run_action(result.next_action, db, case)
    except ApiError as e:
        if e.code == "REBUTTAL_LOCKED":
            missing = (e.fields or {}).get("missing", "report")
            payload = {**REBUTTAL_LOCKED_CARD, "missing": [m for m in str(missing).split(",") if m]}
            await case_service.add_message(db, case_id, "assistant", "rebuttal_locked", payload)
        else:
            await _assistant_text(db, case_id, e.message or ERROR_CATALOG[e.code].message)
```

- [ ] **Step 6: 라우터** — `app/api/messages.py`에 추가

```python
from fastapi import status

from app.schemas.chat import SendMessageRequest
from app.services import chat as chat_service


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def send_message(body: SendMessageRequest, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    msg = await chat_service.send_user_message(db, case, body.text)
    return {"message": message_dict(msg), "assistantPending": True}
```

`app/main.py`의 lifespan `yield` 뒤에 `await chat.wait_all()`을 `runner.wait_all()` 앞에 추가 (`from app.services import chat`).

- [ ] **Step 7: 판정 조회 라우터 (E-1)** — `app/api/verdict.py` (Task 4 테스트가 `/verdict`를 부른다)

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.texts import VERDICT_PLACEHOLDER
from app.db import get_db
from app.deps import owned_case
from app.models import Case
from app.services import cases as case_service
from app.services.presenters import verdict_payload

router = APIRouter(tags=["verdict"])


@router.get("/cases/{case_id}/verdict")
async def get_verdict(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    v = await case_service.active_verdict(db, case.id)
    if v is None:
        return {"verdict": None, "placeholder": VERDICT_PLACEHOLDER}
    return verdict_payload(v)
```

`app/main.py`에 등록.

- [ ] **Step 8: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_chat.py -v` 후 `.venv/Scripts/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 9: 커밋**

```bash
git add app/services/actions.py app/services/chat.py app/schemas/chat.py app/api/messages.py app/api/verdict.py app/main.py tests/conftest.py tests/api/test_chat.py
git commit -m "feat(chat): 메시지 보내기와 Agent 응답·nextAction 분기, 판정 조회"
```

---

### Task 5: 심의사례 설명 (E-2)

**Files:**
- Create: `app/api/precedents.py`, `tests/api/test_precedents.py`
- Modify: `app/main.py`

**Interfaces:**
- `GET /precedents/{precedentId}?caseId=` → `{precedentId, title: "심의사례 {id}", bodyText}`. `caseId`가 있으면 그 사건의 활성 판정 `basis.precedents`에서 찾고, 없으면 사용자의 모든 활성 판정에서 찾는다. 없으면 `NOT_FOUND`

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_precedents.py`

```python
from app.clock import now_utc
from app.db import session_scope
from app.models import Analysis
from app.services.verdict import perform_verdict


async def test_precedent_from_case(client, auth_headers, case_id):
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
        await perform_verdict(db, case_id)
    res = await client.get("/precedents/2019-018856", params={"caseId": case_id}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["precedentId"] == "2019-018856" and body["title"] == "심의사례 2019-018856"
    assert "\n\n" in body["bodyText"]

    res = await client.get("/precedents/2019-018856", headers=auth_headers)
    assert res.status_code == 200

    res = await client.get("/precedents/9999-000000", params={"caseId": case_id}, headers=auth_headers)
    assert res.status_code == 404


async def test_precedent_without_any_verdict(client, auth_headers, case_id):
    res = await client.get("/precedents/2019-018856", params={"caseId": case_id}, headers=auth_headers)
    assert res.status_code == 404
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_precedents.py -v` → FAIL 404 (라우트 없음. 첫 단언 200이 실패)

- [ ] **Step 3: 라우터** — `app/api/precedents.py`

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user
from app.errors import ApiError
from app.models import Case, User, Verdict
from app.services.cases import active_verdict, get_owned_case

router = APIRouter(tags=["verdict"])


def _find(verdicts: list[Verdict], precedent_id: str) -> dict | None:
    for v in verdicts:
        for p in (v.basis or {}).get("precedents", []):
            if p.get("id") == precedent_id:
                return p
    return None


@router.get("/precedents/{precedent_id}")
async def precedent(
    precedent_id: str,
    case_id: str | None = Query(default=None, alias="caseId"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if case_id:
        await get_owned_case(db, user, case_id)
        v = await active_verdict(db, case_id)
        verdicts = [v] if v else []
    else:
        stmt = select(Verdict).join(Case, Case.id == Verdict.case_id).where(Case.user_id == user.id, Verdict.is_active.is_(True)).order_by(Verdict.created_at.desc())
        verdicts = list((await db.execute(stmt)).scalars().all())
    found = _find(verdicts, precedent_id)
    if found is None:
        raise ApiError("NOT_FOUND")
    return {"precedentId": precedent_id, "title": f"심의사례 {precedent_id}", "bodyText": found.get("body_text", "")}
```

`app/main.py`에 등록.

- [ ] **Step 4: 통과 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_precedents.py -v` → PASS

- [ ] **Step 5: 커밋**

```bash
git add app/api/precedents.py app/main.py tests/api/test_precedents.py
git commit -m "feat(verdict): 유사 심의사례 설명문 조회 API"
```

---

## 계획 3 완료 기준

- `pytest -q` 전부 통과.
- curl 시나리오: 사건 생성 → 설명 전송 → 영상 업로드 → SSE로 분석 요약·질문 → 답변 2회 → 판정 카드 → 정정 → 재판정 카드(version 2).
- 계획 4(`2026-09-03-04-report-rebuttal.md`)로 이어진다.
