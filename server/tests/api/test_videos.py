import shutil
import subprocess

import pytest

from app.services.video import (
    duration_label,
    parse_range,
)
from app.services.video import (
    probe_video as real_probe_video,  # conftest 가 갈아끼우기 전의 진짜 함수
)


def test_parse_range():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=90-", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=0-500", 100) == (0, 99)
    assert parse_range("bytes=200-300", 100) is None
    assert parse_range("garbage", 100) is None
    assert parse_range("bytes=500-100", 1000) is None  # 역전된 범위
    assert parse_range("bytes=-0", 100) is None  # 길이 0 접미 범위


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


async def test_upload_copy_failure_removes_temp_file(client, auth_headers, case_id, monkeypatch):
    import tempfile
    from pathlib import Path

    from app.services import video as video_service

    recorded: list[str] = []
    orig_ntf = tempfile.NamedTemporaryFile

    def _spy_ntf(*args, **kwargs):
        f = orig_ntf(*args, **kwargs)
        recorded.append(f.name)
        return f

    def _boom(*args, **kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(video_service.tempfile, "NamedTemporaryFile", _spy_ntf)
    monkeypatch.setattr(video_service.shutil, "copyfileobj", _boom)

    files = {"file": ("blackbox_0822.mp4", b"\x00" * 2048, "video/mp4")}
    res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"

    assert recorded, "temp file should have been created before the copy failure"
    for path in recorded:
        assert not Path(path).exists()


async def test_upload_replace_deletes_old_storage_after_commit(client, auth_headers, case_id, upload, monkeypatch):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Video
    from app.storage import get_storage

    v1 = (await upload(filename="a.mp4")).json()["video"]["id"]

    storage = get_storage()
    orig_delete = storage.delete
    captured: dict = {}

    async def _spy_delete(key):
        async with session_scope() as db:
            row = (await db.execute(select(Video).where(Video.case_id == case_id))).scalar_one_or_none()
            captured["visible_id"] = row.id if row else None
        await orig_delete(key)

    monkeypatch.setattr(storage, "delete", _spy_delete)

    v2 = (await upload(filename="b.mp4")).json()["video"]["id"]

    assert v1 != v2
    assert captured["visible_id"] == v2


async def test_delete_case_removes_storage_after_commit(client, auth_headers, case_id, upload, monkeypatch):
    from app.db import session_scope
    from app.models import Case
    from app.storage import get_storage

    await upload()

    storage = get_storage()
    orig_delete = storage.delete
    captured: dict = {}

    async def _spy_delete(key):
        async with session_scope() as db:
            captured["case_exists"] = (await db.get(Case, case_id)) is not None
        await orig_delete(key)

    monkeypatch.setattr(storage, "delete", _spy_delete)

    assert (await client.delete(f"/cases/{case_id}", headers=auth_headers)).status_code == 204
    assert captured.get("case_exists") is False


async def test_reversed_and_empty_range_fall_back_to_full_body(client, auth_headers, case_id, upload):
    content = bytes(range(256)) * 4
    video_id = (await upload(content=content)).json()["video"]["id"]
    meta = (await client.get(f"/videos/{video_id}", headers=auth_headers)).json()
    stream_url = meta["streamUrl"].removeprefix("/api/v1")

    for header in ("bytes=500-100", "bytes=-0"):
        res = await client.get(stream_url, headers={"Range": header})
        assert res.status_code == 200, header
        assert "content-range" not in res.headers
        assert res.content == content


async def test_upload_normalizes_client_mime_and_stream_never_echoes_it(client, auth_headers, case_id):
    files = {"file": ("evil.html", b"<script>alert(1)</script>", "text/html")}
    res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
    assert res.status_code == 201, res.text
    video_id = res.json()["video"]["id"]
    assert res.json()["video"]["mimeType"] == "video/mp4"

    meta = (await client.get(f"/videos/{video_id}", headers=auth_headers)).json()
    stream = await client.get(meta["streamUrl"].removeprefix("/api/v1"))
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("video/mp4")
    assert stream.headers["x-content-type-options"] == "nosniff"

    ranged = await client.get(meta["streamUrl"].removeprefix("/api/v1"), headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert ranged.headers["content-type"].startswith("video/mp4")
    assert ranged.headers["x-content-type-options"] == "nosniff"


async def test_stream_falls_back_when_stored_mime_is_unknown(client, auth_headers, case_id, upload):
    from app.db import session_scope
    from app.models import Video

    video_id = (await upload()).json()["video"]["id"]
    async with session_scope() as db:
        video = await db.get(Video, video_id)
        video.mime_type = "text/html"
        await db.commit()

    meta = (await client.get(f"/videos/{video_id}", headers=auth_headers)).json()
    stream = await client.get(meta["streamUrl"].removeprefix("/api/v1"))
    assert stream.headers["content-type"].startswith("video/mp4")


async def test_upload_truncates_long_filename(client, auth_headers, case_id):
    files = {"file": ("x" * 300 + ".mp4", bytes(1024), "video/mp4")}
    res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
    assert res.status_code == 201, res.text
    assert len(res.json()["video"]["filename"]) <= 255

    from app.db import session_scope
    from app.models import Video

    async with session_scope() as db:
        video = await db.get(Video, res.json()["video"]["id"])
        assert len(video.filename) <= 255 and len(video.mime_type) <= 64


async def test_stream_does_not_hold_db_session_while_sending(client, auth_headers, case_id, upload, monkeypatch):
    from app.db import engine
    from app.storage import get_storage

    video_id = (await upload(content=bytes(range(256)) * 8)).json()["video"]["id"]
    meta = (await client.get(f"/videos/{video_id}", headers=auth_headers)).json()

    storage = get_storage()
    original = storage.read_range
    seen: dict = {}

    def _spy(key, start, end):
        async def _gen():
            async for chunk in original(key, start, end):
                seen.setdefault("checkedout", engine().pool.checkedout())
                yield chunk
        return _gen()

    monkeypatch.setattr(storage, "read_range", _spy)
    res = await client.get(meta["streamUrl"].removeprefix("/api/v1"))
    assert res.status_code == 200
    assert seen["checkedout"] == 0


PLAYBACK_BYTES = b"H264-PLAYBACK-COPY" * 64


def make_unplayable(monkeypatch, codec="mpeg4", brand="isom"):
    """올라온 파일이 브라우저가 못 여는 형식이라고 ffprobe 자리에서 알려 준다.

    기본값은 예시 영상들과 같은 경우 — 확장자는 .mp4 인데 안이 mp4v.
    """
    from app.services import video as video_service

    probe = video_service.Probe(duration_sec=42, video_codec=codec,
                                format_names=frozenset({"mov", "mp4"}), major_brand=brand)
    assert probe.browser_playable is False, "재생 불가 상황을 만들려던 설정이 아니다"
    monkeypatch.setattr(video_service, "probe_video", lambda path: probe)


def fake_transcoder(monkeypatch, *, ok=True):
    """진짜 ffmpeg 대신 정해진 바이트를 쓴다. 실제 변환은 test_video_playback.py 가 검증한다."""
    from app.services import video as video_service

    calls: list = []

    def _fake(src, dst):
        calls.append(src)
        if not ok:
            return False
        dst.write_bytes(PLAYBACK_BYTES)
        return True

    monkeypatch.setattr(video_service, "transcode_to_h264", _fake)
    return calls


async def test_playable_upload_is_streamed_without_making_a_second_copy(client, auth_headers, case_id, upload, monkeypatch):
    """대부분의 업로드는 이미 H.264 라 변환 없이 지나간다."""
    from app.storage import get_storage

    calls = fake_transcoder(monkeypatch)
    content = bytes(range(256)) * 4
    video_id = (await upload(content=content)).json()["video"]["id"]

    assert calls == [], "이미 재생 가능한 파일인데 변환을 시도했다"
    with pytest.raises(FileNotFoundError):
        await get_storage().size(f"videos/{case_id}/{video_id}_play.mp4")

    meta = (await client.get(f"/videos/{video_id}", headers=auth_headers)).json()
    stream = await client.get(meta["streamUrl"].removeprefix("/api/v1"))
    assert stream.status_code == 200 and stream.content == content


async def test_unplayable_upload_streams_the_h264_copy_and_keeps_the_original(client, auth_headers, case_id, upload, monkeypatch):
    from app.storage import get_storage

    make_unplayable(monkeypatch)
    calls = fake_transcoder(monkeypatch)
    content = bytes(range(256)) * 4
    body = (await upload(content=content)).json()
    video_id = body["video"]["id"]

    assert len(calls) == 1
    assert body["video"]["sizeBytes"] == len(content), "용량은 사용자가 올린 원본 기준이다"

    # 분석은 원본을 쓴다. 원본이 그대로 남아 있어야 한다.
    assert await get_storage().read_bytes(f"videos/{case_id}/{video_id}.mp4") == content

    meta = (await client.get(f"/videos/{video_id}", headers=auth_headers)).json()
    stream_url = meta["streamUrl"].removeprefix("/api/v1")
    stream = await client.get(stream_url)
    assert stream.status_code == 200
    assert stream.content == PLAYBACK_BYTES, "뷰어에는 재생본이 흘러야 한다"
    assert stream.headers["content-type"].startswith("video/mp4")

    ranged = await client.get(stream_url, headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == f"bytes 0-9/{len(PLAYBACK_BYTES)}"
    assert ranged.content == PLAYBACK_BYTES[:10]


async def test_unplayable_mov_streams_mp4_content_type(client, auth_headers, case_id, monkeypatch):
    """아이폰 .mov — 안은 H.264 인데 크롬이 컨테이너를 못 연다. 재생본은 mp4 로 나간다."""
    make_unplayable(monkeypatch, codec="h264", brand="qt")
    fake_transcoder(monkeypatch)
    files = {"file": ("iphone.mov", b"\x00" * 2048, "video/quicktime")}
    res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
    assert res.status_code == 201, res.text

    meta = (await client.get(f"/videos/{res.json()['video']['id']}", headers=auth_headers)).json()
    stream = await client.get(meta["streamUrl"].removeprefix("/api/v1"))
    assert stream.content == PLAYBACK_BYTES
    assert stream.headers["content-type"].startswith("video/mp4")


async def test_transcode_failure_falls_back_to_the_original(client, auth_headers, case_id, upload, monkeypatch):
    """변환이 실패해도 업로드는 성공한다. 프론트가 '미리 볼 수 없는 형식' 안내를 띄운다."""
    from app.storage import get_storage

    make_unplayable(monkeypatch)
    fake_transcoder(monkeypatch, ok=False)
    content = bytes(range(256)) * 4
    video_id = (await upload(content=content)).json()["video"]["id"]

    with pytest.raises(FileNotFoundError):
        await get_storage().size(f"videos/{case_id}/{video_id}_play.mp4")

    meta = (await client.get(f"/videos/{video_id}", headers=auth_headers)).json()
    stream = await client.get(meta["streamUrl"].removeprefix("/api/v1"))
    assert stream.status_code == 200 and stream.content == content


async def test_replacing_video_deletes_the_playback_copy(client, auth_headers, case_id, upload, monkeypatch):
    from app.storage import get_storage

    make_unplayable(monkeypatch)
    fake_transcoder(monkeypatch)
    v1 = (await upload(filename="a.mp4")).json()["video"]["id"]
    old_playback = f"videos/{case_id}/{v1}_play.mp4"
    assert await get_storage().size(old_playback) > 0

    await upload(filename="b.mp4")

    with pytest.raises(FileNotFoundError):
        await get_storage().size(old_playback)
    with pytest.raises(FileNotFoundError):
        await get_storage().size(f"videos/{case_id}/{v1}.mp4")


async def test_delete_case_removes_the_playback_copy(client, auth_headers, case_id, upload, monkeypatch):
    from app.storage import get_storage

    make_unplayable(monkeypatch)
    fake_transcoder(monkeypatch)
    video_id = (await upload()).json()["video"]["id"]
    playback = f"videos/{case_id}/{video_id}_play.mp4"
    assert await get_storage().size(playback) > 0

    assert (await client.delete(f"/cases/{case_id}", headers=auth_headers)).status_code == 204

    with pytest.raises(FileNotFoundError):
        await get_storage().size(playback)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 가 없는 환경")
async def test_real_mp4v_upload_is_streamed_as_h264(client, auth_headers, case_id, tmp_path, monkeypatch):
    """가짜를 끼우지 않고 ffprobe·ffmpeg 를 그대로 태운 전 구간 확인.

    확장자는 .mp4 인데 안이 mp4v 인 파일 — 예시 영상 3개와 같은 경우다.
    """
    from app.services import video as video_service

    monkeypatch.setattr(video_service, "probe_video", real_probe_video)

    src = tmp_path / "blackbox.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x480:rate=10:duration=1", "-c:v", "mpeg4", str(src)],
        check=True, capture_output=True,
    )
    assert video_service.probe_video(src).browser_playable is False, "mp4v 파일을 만들려던 게 아니다"

    files = {"file": ("blackbox.mp4", src.read_bytes(), "video/mp4")}
    res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
    assert res.status_code == 201, res.text

    meta = (await client.get(f"/videos/{res.json()['video']['id']}", headers=auth_headers)).json()
    stream = await client.get(meta["streamUrl"].removeprefix("/api/v1"))
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("video/mp4")

    served = tmp_path / "served.mp4"
    served.write_bytes(stream.content)
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(served)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert codec == "h264", f"뷰어가 받은 파일이 아직 {codec} 이다"


async def test_upload_rejected_while_another_job_runs(client, auth_headers, case_id, upload, settle):
    import asyncio

    from sqlalchemy import select

    from app.db import session_scope
    from app.jobs.runner import runner
    from app.models import Video

    v1 = (await upload(filename="a.mp4")).json()["video"]["id"]
    await settle()

    gate = asyncio.Event()

    async def gated(db, cid, job_id):
        await gate.wait()

    async with session_scope() as db:
        await runner.start(db, case_id, "report", gated)

    try:
        files = {"file": ("b.mp4", bytes(4096), "video/mp4")}
        res = await client.post(f"/cases/{case_id}/videos", files=files, headers=auth_headers)
        assert res.status_code == 409 and res.json()["error"]["code"] == "JOB_ALREADY_RUNNING"

        detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
        assert detail["video"]["id"] == v1 and detail["video"]["filename"] == "a.mp4"
        assert detail["status"] == "intake"
        async with session_scope() as db:
            rows = (await db.execute(select(Video).where(Video.case_id == case_id))).scalars().all()
            assert [r.id for r in rows] == [v1]
    finally:
        gate.set()
    await settle()
