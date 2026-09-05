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
    assert body["downloadUrl"].startswith(f"/api/v1/cases/{judged_case}/report/versions/1/pdf?t=")

    again = await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert again.status_code == 200 and again.json()["pdfId"] == body["pdfId"]

    dl = await client.get(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''%EC%82%AC%EA%B1%B4%EA%B2%BD%EC%9C%84%EC%84%9C_" in dl.headers["content-disposition"]
    assert dl.content[:4] == b"%PDF"

    versions = (await client.get(f"/cases/{judged_case}/report/versions", headers=auth_headers)).json()
    assert versions["items"][0]["hasPdf"] is True


async def test_pdf_create_race_returns_existing_pdf(client, auth_headers, judged_case, settle):
    from app.clock import now_utc
    from app.db import session_scope
    from app.ids import new_id
    from app.models import Case, ReportPdf
    from app.services import report as report_service

    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()

    async with session_scope() as db:
        report = await report_service.report_by_version(db, judged_case, "latest")
        case = await db.get(Case, judged_case)
        winner = ReportPdf(
            id=new_id(), report_id=report.id, storage_key=f"pdfs/{judged_case}/{report.id}.pdf",
            filename="이미_있는.pdf", size_bytes=42, created_at=now_utc(),
        )
        db.add(winner)
        await db.commit()

        original_pdf_for = report_service.pdf_for
        calls = {"n": 0}

        async def fake_pdf_for(db_, report_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # 첫 조회 시점엔 아직 없었던 것처럼 흉내낸다
            return await original_pdf_for(db_, report_id)

        report_service.pdf_for = fake_pdf_for
        try:
            result_pdf, created = await report_service.ensure_pdf(db, case, report)
        finally:
            report_service.pdf_for = original_pdf_for

    assert created is False
    assert result_pdf.id == winner.id


async def test_pdf_create_endpoint_survives_race_rollback(client, auth_headers, judged_case, settle, monkeypatch):
    from app.clock import now_utc
    from app.db import session_scope
    from app.ids import new_id
    from app.models import ReportPdf
    from app.services import report as report_service

    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()

    async with session_scope() as db:
        report = await report_service.report_by_version(db, judged_case, "latest")
        winner = ReportPdf(
            id=new_id(), report_id=report.id, storage_key=f"pdfs/{judged_case}/{report.id}.pdf",
            filename="이미_있는.pdf", size_bytes=42, created_at=now_utc(),
        )
        db.add(winner)
        await db.commit()
        winner_id = winner.id

    original_pdf_for = report_service.pdf_for
    calls = {"n": 0}

    async def fake_pdf_for(db_, report_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 첫 조회 시점엔 아직 없었던 것처럼 흉내낸다 — INSERT가 유니크 제약에 걸리게 한다
        return await original_pdf_for(db_, report_id)

    monkeypatch.setattr(report_service, "pdf_for", fake_pdf_for)

    # rollback 뒤에도 라우터가 pdf_response_dict(case, report, pdf)를 문제없이 만들어야 한다.
    res = await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["pdfId"] == winner_id


async def test_chat_create_report_action(client, auth_headers, judged_case, settle):
    await client.post(f"/cases/{judged_case}/messages", json={"text": "사건경위서 만들어 주세요"}, headers=auth_headers)
    await settle()
    detail = (await client.get(f"/cases/{judged_case}", headers=auth_headers)).json()
    assert detail["documents"]["report"]["exists"] is True


async def test_pdf_rendering_runs_off_the_event_loop(client, auth_headers, judged_case, settle, monkeypatch):
    """fpdf2 렌더링은 순수 CPU 작업이다. 이벤트 루프에서 돌리면 그동안 다른 요청과 SSE가 전부 멈춘다."""
    import threading

    from app.services import report as report_service

    seen: dict[str, bool] = {}
    original = report_service.render_report_pdf

    def spy(**kwargs):
        seen["off_main_thread"] = threading.current_thread() is not threading.main_thread()
        return original(**kwargs)

    monkeypatch.setattr(report_service, "render_report_pdf", spy)

    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    res = await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert res.status_code == 201, res.text
    assert seen["off_main_thread"] is True


async def test_pdf_download_url_opens_without_auth_header(client, auth_headers, judged_case, settle):
    # 브라우저는 <a href>/window.open 으로 PDF를 여는데 그 요청에는 Authorization 헤더를 실을 수 없다.
    # 헤더 없이 치면 401 JSON이 내려가고 크롬은 "PDF 문서를 로드하지 못했습니다"를 띄운다(실서버 재현).
    # 영상 스트림(?t=)과 같은 방식으로 downloadUrl 자체에 단기 토큰을 넣어 헤더 없이 열리게 한다.
    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    url = (await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)).json()["downloadUrl"]
    assert url.startswith(f"/api/v1/cases/{judged_case}/report/versions/1/pdf?t=")

    dl = await client.get(url.removeprefix("/api/v1"))  # 인증 헤더 없음
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/pdf"
    assert dl.content[:4] == b"%PDF"

    # 깨진 토큰은 401, 다른 버전의 PDF에는 쓸 수 없다(403)
    assert (await client.get(f"/cases/{judged_case}/report/versions/1/pdf?t=broken")).status_code == 401
    await client.post(f"/cases/{judged_case}/report/revisions", json={"request": "짧게"}, headers=auth_headers)
    await settle()
    await client.post(f"/cases/{judged_case}/report/versions/2/pdf", headers=auth_headers)
    token_v1 = url.split("t=", 1)[1]
    assert (await client.get(f"/cases/{judged_case}/report/versions/2/pdf?t={token_v1}")).status_code == 403
