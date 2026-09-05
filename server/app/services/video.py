import asyncio
import contextlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc, to_kst_iso
from app.config import get_settings
from app.content.texts import NEED_DESCRIPTION_TEXT, VIDEO_NOTICE
from app.errors import ApiError
from app.ids import new_id
from app.models import Case, Job, Message, Video
from app.security import create_stream_token
from app.services import cases as case_service
from app.services.presenters import size_label
from app.storage import get_storage

log = logging.getLogger(__name__)

EXT_BY_MIME = {"video/mp4": "mp4", "video/quicktime": "mov", "video/x-msvideo": "avi"}

# 크롬이 읽는 조합. 영상은 H.264(MPEG-4 Part 10)만, 소리는 AAC·MP3만 재생한다.
BROWSER_VIDEO_CODEC = "h264"
BROWSER_AUDIO_CODECS = frozenset({"aac", "mp3"})


@dataclass(frozen=True)
class Probe:
    duration_sec: int | None = None
    recorded_at: datetime | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    format_names: frozenset[str] = field(default_factory=frozenset)
    major_brand: str = ""

    @property
    def browser_playable(self) -> bool:
        """브라우저가 이 파일을 그대로 열 수 있나. 아니면 재생본을 따로 만들어야 한다.

        .mp4 와 .mov 는 같은 demuxer 라 format_name 이 둘 다 "mov,mp4,m4a,3gp,3g2,mj2" 로
        똑같이 나온다. 아이폰이 찍는 .mov(major_brand "qt  ")는 안이 H.264 여도 크롬이
        컨테이너부터 열지 못하므로 ftyp 브랜드로 갈라야 한다. 브랜드를 못 읽었으면
        .mp4 라고 확신할 수 없으니 변환하는 쪽으로 판단한다.
        """
        return (
            self.video_codec == BROWSER_VIDEO_CODEC
            and "mp4" in self.format_names
            and bool(self.major_brand)
            and not self.major_brand.startswith("qt")
            and (self.audio_codec is None or self.audio_codec in BROWSER_AUDIO_CODECS)
        )


def parse_probe(payload: dict) -> Probe:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []

    def first_codec(kind: str) -> str | None:
        return next((s.get("codec_name") for s in streams if s.get("codec_type") == kind), None)

    duration = fmt.get("duration")
    try:
        duration_sec = int(float(duration)) if duration else None
    except (TypeError, ValueError):
        duration_sec = None

    tags = fmt.get("tags") or {}
    recorded_at = None
    raw = tags.get("creation_time")
    if raw:
        try:
            recorded_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            recorded_at = None

    return Probe(
        duration_sec=duration_sec,
        recorded_at=recorded_at,
        video_codec=first_codec("video"),
        audio_codec=first_codec("audio"),
        format_names=frozenset((fmt.get("format_name") or "").split(",")),
        major_brand=str(tags.get("major_brand") or "").strip(),
    )


def probe_video(path: Path) -> Probe:
    """ffprobe로 길이·촬영 시각·코덱을 한 번에 뽑는다. 실패하면 빈 Probe(변환 대상)."""
    bin_ = get_settings().ffprobe_bin
    try:
        out = subprocess.run(
            [bin_, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
        return parse_probe(json.loads(out or "{}"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return Probe()


def transcode_to_h264(src: Path, dst: Path) -> bool:
    """재생용 H.264 사본을 만든다. 만들었으면 True, 실패하면 False(원본을 그대로 쓴다)."""
    settings = get_settings()
    cmd = [
        settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-vf", "scale='min(1280,iw)':-2",
        "-c:a", "aac",
        "-movflags", "+faststart",  # 재생 정보(moov)를 앞으로. 없으면 첫 프레임이 늦게 뜬다
        str(dst),
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=settings.transcode_timeout_seconds, check=False)
    except (OSError, subprocess.SubprocessError):
        log.warning("재생본 변환 실패(실행 불가·시간 초과): %s", src.name)
        dst.unlink(missing_ok=True)
        return False
    if done.returncode != 0:
        log.warning("재생본 변환 실패(ffmpeg %s): %s", done.returncode, done.stderr.decode(errors="replace")[:500])
        dst.unlink(missing_ok=True)
        return False
    return dst.exists() and dst.stat().st_size > 0


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


def playback_video_key(case_id: str, video_id: str) -> str:
    """재생본은 원본과 따로 둔다. 분석은 원본을, 뷰어는 이쪽을 쓴다."""
    return f"videos/{case_id}/{video_id}_play.mp4"


def attachment_payload(video: Video) -> dict:
    meta = video.meta
    return {
        "videoId": video.id,
        "filename": video.filename,
        "durationSec": video.duration_sec,
        "sizeLabel": size_label(video.size_bytes),
        "recordedAt": to_kst_iso(video.recorded_at),
        # speedKph·impactAtSec 는 AI 영상 분석의 추정치다. durationSec·recordedAt(파일 실측)과 구분되게 표시한다.
        "meta": {"speedKph": meta.get("speedKph"), "impactAtSec": meta.get("impactAtSec"), "estimated": True} if meta else None,
    }


async def has_user_text(db: AsyncSession, case_id: str) -> bool:
    stmt = select(Message.id).where(Message.case_id == case_id, Message.role == "user", Message.type == "text").limit(1)
    return (await db.execute(stmt)).first() is not None


FILENAME_MAX = 255
MIME_MAX = 64


def normalize_mime(raw: str | None) -> str:
    """클라이언트가 준 Content-Type은 믿지 않는다. 아는 영상 타입이 아니면 mp4로 고정."""
    return raw if raw in EXT_BY_MIME else "video/mp4"


async def store_playback_copy(case_id: str, video_id: str, src: Path, dst: Path) -> str | None:
    """재생용 H.264 사본을 만들어 저장한다.

    어느 단계에서 실패하든 None 을 돌려준다 — 뷰어는 원본을 그대로 받고,
    프론트가 '미리 볼 수 없는 형식' 안내를 띄운다. 분석에는 영향이 없다.
    """
    if not await asyncio.to_thread(transcode_to_h264, src, dst):
        return None
    key = playback_video_key(case_id, video_id)
    try:
        await get_storage().put_file(key, dst)
    except Exception:
        log.exception("재생본 저장 실패: %s", key)
        with contextlib.suppress(Exception):
            await get_storage().delete(key)
        return None
    return key


async def upload_video(db: AsyncSession, case: Case, upload: UploadFile) -> tuple[Video, Job | None, bool]:
    # 다른 Job이 도는 중이면 아무것도 건드리지 않는다. 스풀링 뒤에 검사하면 이미 영상이 교체된 뒤다.
    if await case_service.active_job(db, case.id) is not None:
        raise ApiError("JOB_ALREADY_RUNNING")
    mime = normalize_mime(upload.content_type)
    video_id = new_id()
    key = video_key(case.id, video_id, mime)
    playback_key = None

    tmp = tempfile.NamedTemporaryFile(suffix=f".{EXT_BY_MIME.get(mime, 'mp4')}", delete=False)
    tmp_path = Path(tmp.name)
    play_path = tmp_path.with_name(f"{tmp_path.stem}_play.mp4")
    try:
        await asyncio.to_thread(shutil.copyfileobj, upload.file, tmp, 1024 * 1024)
        tmp.close()
        probe = await asyncio.to_thread(probe_video, tmp_path)
        size = await get_storage().put_file(key, tmp_path)
        # 이미 브라우저가 읽는 조합이면 여기서 걸러져 변환 없이 지나간다.
        if not probe.browser_playable:
            playback_key = await store_playback_copy(case.id, video_id, tmp_path, play_path)
    finally:
        tmp.close()
        tmp_path.unlink(missing_ok=True)
        play_path.unlink(missing_ok=True)

    old = await case_service.get_video(db, case.id)
    old_keys = [k for k in (old.storage_key, old.playback_key) if k] if old is not None else []
    if old is not None:
        await db.delete(old)
        await db.flush()

    video = Video(
        id=video_id, case_id=case.id, filename=(upload.filename or "video.mp4")[:FILENAME_MAX], size_bytes=size,
        duration_sec=probe.duration_sec, mime_type=mime[:MIME_MAX], recorded_at=probe.recorded_at, meta=None,
        storage_key=key, playback_key=playback_key, created_at=now_utc(),
    )
    db.add(video)
    case_service.touch(case)
    await db.commit()

    for old_key in old_keys:
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
            "estimated": True,  # AI 영상 분석 추정치 — durationSec·recordedAt 같은 파일 실측값과 구분한다
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
