import asyncio
import json
import logging
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

log = logging.getLogger(__name__)

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


FILENAME_MAX = 255
MIME_MAX = 64


def normalize_mime(raw: str | None) -> str:
    """클라이언트가 준 Content-Type은 믿지 않는다. 아는 영상 타입이 아니면 mp4로 고정."""
    return raw if raw in EXT_BY_MIME else "video/mp4"


async def upload_video(db: AsyncSession, case: Case, upload: UploadFile) -> tuple[Video, Job | None, bool]:
    mime = normalize_mime(upload.content_type)
    video_id = new_id()
    key = video_key(case.id, video_id, mime)

    tmp = tempfile.NamedTemporaryFile(suffix=f".{EXT_BY_MIME.get(mime, 'mp4')}", delete=False)
    tmp_path = Path(tmp.name)
    try:
        await asyncio.to_thread(shutil.copyfileobj, upload.file, tmp, 1024 * 1024)
        tmp.close()
        duration_sec, recorded_at = await asyncio.to_thread(probe_video, tmp_path)
        size = await get_storage().put_file(key, tmp_path)
    finally:
        tmp.close()
        tmp_path.unlink(missing_ok=True)

    old = await case_service.get_video(db, case.id)
    old_key = old.storage_key if old is not None else None
    if old is not None:
        await db.delete(old)
        await db.flush()

    video = Video(
        id=video_id, case_id=case.id, filename=(upload.filename or "video.mp4")[:FILENAME_MAX], size_bytes=size,
        duration_sec=duration_sec, mime_type=mime[:MIME_MAX], recorded_at=recorded_at, meta=None,
        storage_key=key, created_at=now_utc(),
    )
    db.add(video)
    case_service.touch(case)
    await db.commit()

    if old_key is not None:
        try:
            await get_storage().delete(old_key)
        except Exception:
            log.exception("교체된 영상 스토리지 정리 실패: %s", old_key)

    await case_service.add_message(db, case.id, "user", "video_attachment", attachment_payload(video))

    if await has_user_text(db, case.id):
        from app.jobs.analysis import start_analysis

        job = await start_analysis(db, case)
        return video, job, False

    await case_service.assistant_text(db, case.id, NEED_DESCRIPTION_TEXT)
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
        start, end = size - length, size - 1
    else:
        start = int(start_s)
        if start >= size:
            return None
        end = min(int(end_s), size - 1) if end_s else size - 1
    if start > end:
        return None  # 역전된 범위(bytes=500-100)나 길이 0(bytes=-0)은 없는 것으로 본다
    return start, end
