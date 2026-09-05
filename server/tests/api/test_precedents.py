import base64
import json

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


async def test_precedent_cross_user_isolation(client, auth_headers, case_id):
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
        await perform_verdict(db, case_id)

    other = await client.post("/auth/signup", json={
        "email": "other@example.com", "password": "carguard12", "passwordConfirm": "carguard12",
        "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True}})
    other_headers = {"Authorization": f"Bearer {other.json()['accessToken']}"}

    res = await client.get("/precedents/2019-018856", headers=other_headers)
    assert res.status_code == 404

    res = await client.get("/precedents/2019-018856", params={"caseId": case_id}, headers=other_headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"


# --------------------------------------------------------------------------- 심의사례 그림 (PNG)

_PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _image_dir(tmp_path, monkeypatch, *ids):
    """AI 쪽 build_case_images.py 가 만드는 폴더 모양(PNG + manifest.json)을 흉내 낸다."""
    from app.config import get_settings
    from app.services.precedent_images import reset_precedent_images

    folder = tmp_path / "images"
    folder.mkdir()
    manifest = {}
    for pid in ids:
        (folder / f"{pid}.png").write_bytes(_PNG_1PX)
        manifest[pid] = {"file": f"{pid}.png", "unit_type": "case", "page": 1}
    (folder / "manifest.json").write_text(json.dumps({"version": 1, "images": manifest}), encoding="utf-8")
    monkeypatch.setenv("PRECEDENT_IMAGE_DIR", str(folder))
    get_settings.cache_clear()
    reset_precedent_images()
    return folder


async def _judged(case_id):
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
        await perform_verdict(db, case_id)


async def test_precedent_image_url_and_download(client, auth_headers, case_id, tmp_path, monkeypatch):
    _image_dir(tmp_path, monkeypatch, "2019-018856")
    await _judged(case_id)

    res = await client.get("/precedents/2019-018856", params={"caseId": case_id}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["imageUrl"].startswith("/api/v1/precedents/2019-018856/image?t=")
    assert body["imageCaption"] and "출처" in body["imageCaption"]

    # 프론트는 받은 URL 을 <img src> 에 그대로 넣는다 → 헤더 없이 서명만으로 열린다
    img = await client.get(body["imageUrl"].removeprefix("/api/v1"))
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png" and img.content == _PNG_1PX
    assert "max-age=600" in img.headers["cache-control"]

    # 서명이 틀리거나 다른 사례의 서명이면 열리지 않는다
    bad = await client.get("/precedents/2019-018856/image", params={"t": "not-a-token"})
    assert bad.status_code in (401, 403)
    from app.security import create_precedent_image_token

    other = await client.get("/precedents/2019-018856/image", params={"t": create_precedent_image_token("2019-000000")})
    assert other.status_code == 403


async def test_precedent_without_image_returns_null_url(client, auth_headers, case_id, tmp_path, monkeypatch):
    _image_dir(tmp_path, monkeypatch)  # 폴더는 있지만 이 사례의 그림은 없다
    await _judged(case_id)

    res = await client.get("/precedents/2019-018856", params={"caseId": case_id}, headers=auth_headers)
    assert res.status_code == 200 and res.json()["imageUrl"] is None and res.json()["imageCaption"] is None

    from app.security import create_precedent_image_token

    missing = await client.get("/precedents/2019-018856/image", params={"t": create_precedent_image_token("2019-018856")})
    assert missing.status_code == 404


async def test_precedent_image_manifest_cannot_escape_folder(client, tmp_path, monkeypatch):
    folder = _image_dir(tmp_path, monkeypatch)
    secret = tmp_path / "secret.png"
    secret.write_bytes(_PNG_1PX)
    (folder / "manifest.json").write_text(json.dumps({"version": 1, "images": {"evil": {"file": "../secret.png"}}}), encoding="utf-8")
    from app.services.precedent_images import image_file, reset_precedent_images

    reset_precedent_images()
    assert image_file("evil") is None
