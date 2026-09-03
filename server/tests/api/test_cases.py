import asyncio

import pytest

from app.sse.hub import hub


async def test_create_case_returns_detail_and_inserts_guide(client, auth_headers):
    res = await client.post("/cases", headers=auth_headers)
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "새 사건" and body["status"] == "intake" and body["statusLabel"] == "접수중"
    assert body["subtitle"].startswith("접수 ")
    assert body["stages"]["analysis"] == {"state": "pending"}
    assert body["verdict"] is None and body["verdictPlaceholder"] == "아직 판정 전이에요."
    assert body["documents"]["report"]["label"] == "아직 없음"
    assert body["documents"]["rebuttal"]["label"] == "잠김 · 판정과 경위서가 먼저예요"
    assert body["video"] is None and body["activeJob"] is None

    msgs = (await client.get(f"/cases/{body['id']}/messages", headers=auth_headers)).json()
    assert len(msgs["items"]) == 1
    assert msgs["items"][0]["type"] == "guide" and msgs["items"][0]["role"] == "assistant"
    assert msgs["items"][0]["payload"]["limitsLabel"] == "mp4 권장 · 최대 200MB · 3분 이내"


async def test_list_cases_newest_first(client, auth_headers):
    a = (await client.post("/cases", headers=auth_headers)).json()["id"]
    b = (await client.post("/cases", headers=auth_headers)).json()["id"]
    await client.patch(f"/cases/{a}", json={"title": "나중에 고침"}, headers=auth_headers)
    res = await client.get("/cases", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert [i["id"] for i in body["items"]] == [a, b]
    assert body["hasMore"] is False and body["nextCursor"] is None
    assert set(body["items"][0]) == {"id", "title", "status", "statusLabel", "updatedAt"}


async def test_list_empty(client, auth_headers):
    assert (await client.get("/cases", headers=auth_headers)).json()["items"] == []


async def test_get_case_forbidden_for_other_user(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    other = await client.post("/auth/signup", json={
        "email": "other@example.com", "password": "carguard12", "passwordConfirm": "carguard12",
        "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True}})
    h = {"Authorization": f"Bearer {other.json()['accessToken']}"}
    res = await client.get(f"/cases/{case_id}", headers=h)
    assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"


async def test_get_case_not_found(client, auth_headers):
    res = await client.get("/cases/01JNOPE00000000000000000000", headers=auth_headers)
    assert res.status_code == 404


async def test_rename_validation_and_success(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    res = await client.patch(f"/cases/{case_id}", json={"title": "   "}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_FAILED"
    res = await client.patch(f"/cases/{case_id}", json={"title": "x" * 61}, headers=auth_headers)
    assert res.status_code == 422
    res = await client.patch(f"/cases/{case_id}", json={"title": "논현사거리 이륜차 충돌"}, headers=auth_headers)
    assert res.status_code == 200 and res.json()["title"] == "논현사거리 이륜차 충돌"


async def test_delete_case(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    res = await client.delete(f"/cases/{case_id}", headers=auth_headers)
    assert res.status_code == 204
    assert (await client.get(f"/cases/{case_id}", headers=auth_headers)).status_code == 404


async def test_delete_case_drops_hub_buffer_and_subscribers(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    it = hub.subscribe(case_id)
    await it.__anext__()  # connected
    hub.publish(case_id, "case.updated", {"id": case_id})
    await it.__anext__()
    assert hub.buffer_count(case_id) == 1

    assert (await client.delete(f"/cases/{case_id}", headers=auth_headers)).status_code == 204
    assert hub.buffer_count(case_id) == 0
    assert hub.subscriber_count(case_id) == 0
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(it.__anext__(), timeout=1)


async def test_rename_publishes_case_updated(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    it = hub.subscribe(case_id)
    await it.__anext__()
    await client.patch(f"/cases/{case_id}", json={"title": "바뀜"}, headers=auth_headers)
    frame = await it.__anext__()
    assert "event: case.updated" in frame and '"title": "바뀜"' in frame
    await it.aclose()


async def test_delete_case_while_job_running_drops_subscribers_and_job_finishes(client, auth_headers):
    from app.db import session_scope
    from app.jobs.runner import runner

    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    gate = asyncio.Event()

    async def gated(db, cid, job_id):
        await gate.wait()

    it = hub.subscribe(case_id)
    await it.__anext__()  # connected
    async with session_scope() as db:
        await runner.start(db, case_id, "report", gated)
    await it.__anext__()  # case.updated (Job 시작)

    try:
        assert (await client.delete(f"/cases/{case_id}", headers=auth_headers)).status_code == 204
        assert hub.subscriber_count(case_id) == 0
        assert hub.buffer_count(case_id) == 0
    finally:
        gate.set()

    # 사건이 사라진 뒤에도 Job 마무리(마지막 case.updated 발행)는 조용히 끝나야 한다.
    await asyncio.wait_for(runner.wait_all(), timeout=5)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(it.__anext__(), timeout=1)
    await it.aclose()


async def test_touched_case_is_always_on_top_even_on_a_coarse_clock(client, auth_headers):
    """목록은 updated_at 내림차순이다. 시계 해상도가 거칠어(윈도우는 종종 ~15ms) 두 사건의
    updated_at이 같은 값으로 찍히면 '방금 만진 사건이 위'라는 규칙이 깨진다.
    touch()가 항상 증가하는 값을 쓰는지 반복해서 확인한다."""
    for _ in range(20):
        a = (await client.post("/cases", headers=auth_headers)).json()["id"]
        b = (await client.post("/cases", headers=auth_headers)).json()["id"]
        await client.patch(f"/cases/{a}", json={"title": "방금 고침"}, headers=auth_headers)
        items = (await client.get("/cases", headers=auth_headers)).json()["items"]
        assert [i["id"] for i in items[:2]] == [a, b]


async def test_touch_never_repeats_or_moves_backwards():
    """touch()는 같은 값을 두 번 쓰지 않는다 (단위 수준 확인)."""
    from app.clock import ensure_aware, now_utc
    from app.models import Case
    from app.services.cases import touch

    case = Case(id="x", user_id="u", title="t", status="intake", created_at=now_utc(), updated_at=now_utc())
    stamps = []
    for _ in range(200):
        touch(case)
        stamps.append(ensure_aware(case.updated_at))
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
