async def test_onboarding_sets_timestamp(client, auth_headers):
    res = await client.patch("/users/me/onboarding", json={"completed": True, "skipped": False}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["onboardedAt"].endswith("+09:00")
    me = await client.get("/auth/me", headers=auth_headers)
    assert me.json()["onboardedAt"] == res.json()["onboardedAt"]


async def test_onboarding_skipped_also_sets_timestamp(client, auth_headers):
    res = await client.patch("/users/me/onboarding", json={"completed": False, "skipped": True}, headers=auth_headers)
    assert res.json()["onboardedAt"] is not None


async def test_onboarding_requires_auth(client):
    res = await client.patch("/users/me/onboarding", json={"completed": True})
    assert res.status_code == 401


async def test_legal_docs(client):
    for doc_type in ("terms", "privacy", "video-consent"):
        res = await client.get(f"/legal/{doc_type}")
        assert res.status_code == 200
        body = res.json()
        assert body["docType"] == doc_type
        assert body["title"] and body["version"] and body["bodyMarkdown"].startswith("## ")
    res = await client.get("/legal/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
