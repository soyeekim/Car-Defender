from app.db import session_scope
from app.services.cases import add_message


async def test_messages_pagination_oldest_to_newest(client, auth_headers, case_id):
    async with session_scope() as db:
        for i in range(25):
            await add_message(db, case_id, "user", "text", {"text": f"m{i}"}, publish=False)
    res = await client.get(f"/cases/{case_id}/messages", headers=auth_headers)
    body = res.json()
    assert len(body["items"]) == 20 and body["hasMore"] is True
    texts = [m["payload"].get("text") for m in body["items"]]
    assert texts[-1] == "m24" and texts[0] == "m5"
    assert body["nextCursor"] == body["items"][0]["id"]

    res2 = await client.get(f"/cases/{case_id}/messages", params={"before": body["nextCursor"], "limit": 10}, headers=auth_headers)
    b2 = res2.json()
    assert [m["payload"].get("text") for m in b2["items"]][-1] == "m4"
    assert b2["items"][0]["type"] == "guide"
    assert b2["hasMore"] is False and b2["nextCursor"] is None


async def test_messages_limit_capped_at_50(client, auth_headers, case_id):
    res = await client.get(f"/cases/{case_id}/messages", params={"limit": 500}, headers=auth_headers)
    assert res.status_code == 200


async def test_messages_forbidden(client, auth_headers, case_id):
    res = await client.get(f"/cases/{case_id}/messages")
    assert res.status_code == 401
