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
