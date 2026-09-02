from app.services.video import duration_label, parse_range


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
