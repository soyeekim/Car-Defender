from app.mail import get_mailer  # noqa: F401  (픽스처 호환)


async def test_report_requires_verdict(client, auth_headers, case_id):
    res = await client.post(f"/cases/{case_id}/report", headers=auth_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "REPORT_VERDICT_REQUIRED"


async def test_report_draft_flow(client, auth_headers, judged_case, sse, settle):
    tap = await sse(judged_case)
    res = await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    assert res.status_code == 202
    body = res.json()
    assert body["kind"] == "report" and body["status"] == "running" and body["version"] == 1
    await settle()
    frames = await tap.take(3)
    assert '"kind": "report"' in frames[0]
    assert '"type": "report_draft"' in frames[1] and '"versionLabel": "첫 번째 버전"' in frames[1] and '"canCreateRebuttal": true' in frames[1]
    assert '"report": {"exists": true, "label": "첫 번째 버전 · 2장"' in frames[2]
    assert '"rebuttal": {"exists": false, "locked": false, "label": "이제 만들 수 있어요"}' in frames[2]

    detail = (await client.get(f"/cases/{judged_case}", headers=auth_headers)).json()
    assert detail["stages"]["report"] == {"state": "done"}

    full = (await client.get(f"/cases/{judged_case}/report/versions/latest", headers=auth_headers)).json()
    assert full["version"] == 1 and len(full["sections"]) == 4 and full["sections"][0]["index"] == 1
    assert full["intro"] == "채팅에서 나눈 대화와 영상 분석 결과를 바탕으로 쓴 첫 번째 버전이에요."
    assert full["revisionPlaceholder"] == "예: 2번을 더 간단하게" and full["disclaimer"]

    assert (await client.get(f"/cases/{judged_case}/report/versions/9", headers=auth_headers)).status_code == 404


async def test_report_revision_creates_new_version_and_updates_card(client, auth_headers, judged_case, settle, sse):
    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    tap = await sse(judged_case)
    res = await client.post(f"/cases/{judged_case}/report/revisions", json={"request": "2번을 더 간단하게"}, headers=auth_headers)
    assert res.status_code == 202 and res.json()["fromVersion"] == 1 and res.json()["toVersion"] == 2
    await settle()
    frames = await tap.take(3)
    assert "event: message.updated" in frames[1] and '"version": 2' in frames[1]

    versions = (await client.get(f"/cases/{judged_case}/report/versions", headers=auth_headers)).json()
    assert versions["latestVersion"] == 2 and [v["version"] for v in versions["items"]] == [2, 1]
    assert versions["items"][0]["revisionRequest"] == "2번을 더 간단하게" and versions["items"][0]["hasPdf"] is False

    msgs = (await client.get(f"/cases/{judged_case}/messages", params={"limit": 50}, headers=auth_headers)).json()["items"]
    assert len([m for m in msgs if m["type"] == "report_draft"]) == 1


async def test_report_revision_validation(client, auth_headers, judged_case, settle):
    assert (await client.post(f"/cases/{judged_case}/report/revisions", json={"request": "x"}, headers=auth_headers)).status_code == 404
    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    assert (await client.post(f"/cases/{judged_case}/report/revisions", json={"request": ""}, headers=auth_headers)).status_code == 422


async def test_pdf_create_idempotent_and_download(client, auth_headers, judged_case, settle):
    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    assert (await client.get(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)).status_code == 404

    res = await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert res.status_code == 201
    body = res.json()
    assert body["filename"].startswith("사건경위서_교차로 직진 충돌 · 08-22_") and body["sizeBytes"] > 1000
    assert body["downloadUrl"] == f"/api/v1/cases/{judged_case}/report/versions/1/pdf"

    again = await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert again.status_code == 200 and again.json()["pdfId"] == body["pdfId"]

    dl = await client.get(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''%EC%82%AC%EA%B1%B4%EA%B2%BD%EC%9C%84%EC%84%9C_" in dl.headers["content-disposition"]
    assert dl.content[:4] == b"%PDF"

    versions = (await client.get(f"/cases/{judged_case}/report/versions", headers=auth_headers)).json()
    assert versions["items"][0]["hasPdf"] is True


async def test_chat_create_report_action(client, auth_headers, judged_case, settle):
    await client.post(f"/cases/{judged_case}/messages", json={"text": "사건경위서 만들어 주세요"}, headers=auth_headers)
    await settle()
    detail = (await client.get(f"/cases/{judged_case}", headers=auth_headers)).json()
    assert detail["documents"]["report"]["exists"] is True
