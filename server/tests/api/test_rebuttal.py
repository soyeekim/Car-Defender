async def test_rebuttal_locked_without_verdict_and_report(client, auth_headers, case_id):
    res = await client.post(f"/cases/{case_id}/rebuttal", headers=auth_headers)
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "REBUTTAL_LOCKED" and err["fields"] == {"missing": "verdict,report"}
    assert err["actions"] == [{"label": "사건경위서 먼저 만들기", "type": "create_report"}]


async def test_rebuttal_locked_without_report(client, auth_headers, judged_case):
    res = await client.post(f"/cases/{judged_case}/rebuttal", headers=auth_headers)
    assert res.json()["error"]["fields"] == {"missing": "report"}
    assert (await client.get(f"/cases/{judged_case}/rebuttal", headers=auth_headers)).status_code == 404


async def test_rebuttal_draft_flow(client, auth_headers, reported_case, sse, settle):
    tap = await sse(reported_case)
    res = await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    assert res.status_code == 202 and res.json()["kind"] == "rebuttal"
    await settle()
    frames = await tap.take(3)
    assert '"type": "rebuttal_draft"' in frames[1]
    assert '"canSend": false' in frames[1] and '"blockedBy": ["recipient", "claimNumber"]' in frames[1]
    assert '"subject": "과실비율 재검토 요청 (접수번호는 아직 안 넣었어요)"' in frames[1]
    assert '"recipientPlaceholder": "아직 안 정했어요"' in frames[1]
    assert '"rebuttal": {"exists": true, "locked": false, "label": "작성 중"}' in frames[2]
    detail = (await client.get(f"/cases/{reported_case}", headers=auth_headers)).json()
    assert detail["stages"]["rebuttal"] == {"state": "in_progress"}

    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "draft" and g2["editable"] is True and g2["subjectAuto"] is True
    assert g2["fromEmail"] == "hyun@example.com"
    kinds = [a["kind"] for a in g2["attachments"]]
    assert kinds == ["report_pdf", "video"] and all(a["included"] for a in g2["attachments"])
    assert g2["attachments"][0]["name"] == "사건경위서.pdf" and g2["attachments"][1]["name"] == "blackbox_0822.mp4"
    assert g2["attachmentNotice"].startswith("영상에는 상대 차량 번호판")


async def test_rebuttal_patch_rules(client, auth_headers, reported_case, settle):
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    url = f"/cases/{reported_case}/rebuttal"

    res = await client.patch(url, json={"recipient": "not-an-email"}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "RECIPIENT_INVALID"

    res = await client.patch(url, json={"recipient": "kim@insu.co.kr"}, headers=auth_headers)
    assert res.status_code == 200 and res.json()["canSend"] is False and res.json()["blockedBy"] == ["claimNumber"]

    res = await client.patch(url, json={"claimNumber": "2026-08-0000"}, headers=auth_headers)
    body = res.json()
    assert body["canSend"] is True and body["blockedBy"] == []
    assert body["subject"] == "과실비율 재검토 요청 (접수번호 2026-08-0000)" and body["subjectAuto"] is True

    res = await client.patch(url, json={"subject": "직접 쓴 제목"}, headers=auth_headers)
    assert res.json()["subjectAuto"] is False
    res = await client.patch(url, json={"claimNumber": "X-1"}, headers=auth_headers)
    assert res.json()["subject"] == "직접 쓴 제목"

    video_ref = body["attachments"][1]["refId"]
    res = await client.patch(url, json={"attachments": [{"refId": video_ref, "included": False}]}, headers=auth_headers)
    assert [a["included"] for a in res.json()["attachments"]] == [True, False]

    res = await client.patch(url, json={"body": ""}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_rebuttal_patch_subject_normalizes_newlines(client, auth_headers, reported_case, settle):
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    url = f"/cases/{reported_case}/rebuttal"

    res = await client.patch(url, json={"subject": "제목 줄1\n줄2\r\n줄3"}, headers=auth_headers)
    assert res.status_code == 200
    subject = res.json()["subject"]
    assert subject == "제목 줄1 줄2 줄3"
    assert "\n" not in subject and "\r" not in subject


async def test_rebuttal_sending_status_blocks_edit_like_sent(client, auth_headers, reported_case, settle):
    """발송 중(sending)인 반박의견서는 sent와 동일하게 수정할 수 없다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Rebuttal

    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == reported_case))).scalar_one()
        rebuttal.status = "sending"
        await db.commit()

    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "sending" and g2["editable"] is False and g2["canSend"] is False

    res = await client.patch(f"/cases/{reported_case}/rebuttal", json={"body": "x"}, headers=auth_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "REBUTTAL_ALREADY_SENT"


async def test_large_video_excluded_from_attachments(client, auth_headers, reported_case, settle, monkeypatch):
    from app.services import rebuttal as rs

    monkeypatch.setattr(rs, "MAX_ATTACH_BYTES", 1000)
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    video = g2["attachments"][1]
    assert video["included"] is False and video["note"] == "용량이 커서 첨부할 수 없어요"


async def test_chat_create_rebuttal_action_locked_card(client, auth_headers, judged_case, settle):
    await client.post(f"/cases/{judged_case}/messages", json={"text": "바로 반박의견서 보낼 수 있어요?"}, headers=auth_headers)
    await settle()
    msgs = (await client.get(f"/cases/{judged_case}/messages", params={"limit": 50}, headers=auth_headers)).json()["items"]
    assert msgs[-1]["type"] == "rebuttal_locked" and msgs[-1]["payload"]["missing"] == ["report"]


async def test_rebuttal_recreate_is_blocked_while_sending(client, auth_headers, reported_case, settle):
    """발송 중(sending)에는 초안을 새로 만들 수도 없다 — 재생성이 sending 행을 덮어쓰면 안 된다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Rebuttal

    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == reported_case))).scalar_one()
        rebuttal.status = "sending"
        await db.commit()

    res = await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "REBUTTAL_ALREADY_SENT"


async def test_rebuttal_empty_subject_restores_auto_subject(client, auth_headers, reported_case, settle):
    """제목을 빈 값으로 비우면 '자동 제목으로 되돌려 줘'라는 뜻이다 (자동 제목을 끄는 게 아니다)."""
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    url = f"/cases/{reported_case}/rebuttal"

    await client.patch(url, json={"claimNumber": "2026-08-0000"}, headers=auth_headers)
    res = await client.patch(url, json={"subject": "직접 쓴 제목"}, headers=auth_headers)
    assert res.json()["subject"] == "직접 쓴 제목" and res.json()["subjectAuto"] is False

    res = await client.patch(url, json={"subject": "   "}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["subjectAuto"] is True
    assert res.json()["subject"] == "과실비율 재검토 요청 (접수번호 2026-08-0000)"

    # 자동 제목이 다시 켜졌으니 접수번호를 바꾸면 제목도 따라 바뀐다
    res = await client.patch(url, json={"claimNumber": "X-1"}, headers=auth_headers)
    assert res.json()["subject"] == "과실비율 재검토 요청 (접수번호 X-1)"


async def test_rebuttal_patch_bounds_user_strings(client, auth_headers, reported_case, settle):
    """DB 칼럼 상한(claim_number varchar(64) · recipient varchar(320))을 넘는 값은 422로 막는다.
    Postgres에서는 잘리지 않고 write가 통째로 실패하기 때문이다."""
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    url = f"/cases/{reported_case}/rebuttal"

    res = await client.patch(url, json={"claimNumber": "9" * 65}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_FAILED"
    assert (await client.patch(url, json={"claimNumber": "9" * 64}, headers=auth_headers)).status_code == 200

    long_email = "a" * 320 + "@example.com"
    res = await client.patch(url, json={"recipient": long_email}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_FAILED"
