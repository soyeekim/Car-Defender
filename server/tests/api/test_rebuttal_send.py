import uuid

from app.mail import get_mailer


async def ready(client, h, case_id, settle):
    await client.post(f"/cases/{case_id}/rebuttal", headers=h)
    await settle()
    await client.patch(f"/cases/{case_id}/rebuttal", json={"recipient": "kim@insu.co.kr", "claimNumber": "2026-08-0000"}, headers=h)


async def test_send_requires_idempotency_key(client, auth_headers, reported_case, settle):
    await ready(client, auth_headers, reported_case, settle)
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=auth_headers)
    assert res.status_code == 400 and res.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_send_validation(client, auth_headers, reported_case, settle):
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 422 and res.json()["error"]["code"] == "RECIPIENT_INVALID"
    await client.patch(f"/cases/{reported_case}/rebuttal", json={"recipient": "kim@insu.co.kr"}, headers=auth_headers)
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 422 and res.json()["error"]["code"] == "CLAIM_NUMBER_REQUIRED"


async def test_send_success_flow(client, auth_headers, reported_case, settle, sse):
    await ready(client, auth_headers, reported_case, settle)
    tap = await sse(reported_case)
    key = str(uuid.uuid4())
    h = {**auth_headers, "Idempotency-Key": key}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["recipient"] == "kim@insu.co.kr" and body["fromEmail"] == "hyun@example.com" and body["attachmentCount"] == 2
    assert body["sentAt"].endswith("+09:00") and body["sendLogId"]

    sent = get_mailer().sent[-1]
    assert sent.to == "kim@insu.co.kr" and sent.reply_to == "hyun@example.com" and sent.sender_email == "hyun@example.com"
    assert sent.display_name == "카-디펜더 (hyun@example.com)"
    assert sent.subject == "과실비율 재검토 요청 (접수번호 2026-08-0000)"
    assert [a.filename for a in sent.attachments][0].startswith("사건경위서_") and sent.attachments[1].filename == "blackbox_0822.mp4"
    assert sent.attachments[0].content[:4] == b"%PDF"
    assert "이 메일은 카-디펜더(cardefender.kr)를 통해 hyun@example.com 님이 보냈습니다." in sent.body_text

    frames = await tap.take(3)
    assert '"type": "sent"' in frames[0] and '"attachmentCount": 2' in frames[0] and '"nextSteps"' in frames[0]
    assert "event: rebuttal.sent" in frames[1]
    assert '"status": "sent"' in frames[2] and '"rebuttal": {"exists": true, "locked": false, "label": "발송 완료 · ' in frames[2]

    detail = (await client.get(f"/cases/{reported_case}", headers=auth_headers)).json()
    assert detail["stages"]["rebuttal"] == {"state": "done"}

    # 같은 키 재호출: 메일 안 나가고 같은 결과
    again = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert again.status_code == 200 and again.json()["sendLogId"] == body["sendLogId"]
    assert len(get_mailer().sent) == 1

    # 새 키로 재호출: 이미 보냄
    h2 = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    assert (await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h2)).json()["error"]["code"] == "REBUTTAL_ALREADY_SENT"
    assert (await client.patch(f"/cases/{reported_case}/rebuttal", json={"body": "x"}, headers=auth_headers)).json()["error"]["code"] == "REBUTTAL_ALREADY_SENT"
    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "sent" and g2["editable"] is False and g2["canSend"] is False

    logs = (await client.get(f"/cases/{reported_case}/rebuttal/sends", headers=auth_headers)).json()
    assert len(logs["items"]) == 1 and logs["items"][0]["result"] == "sent" and logs["items"][0]["attachmentNames"][1] == "blackbox_0822.mp4"


async def test_send_failure_keeps_draft_and_logs_failed(client, auth_headers, reported_case, settle, monkeypatch):
    from app.mail.base import MailSendError

    await ready(client, auth_headers, reported_case, settle)

    async def boom(msg):
        raise MailSendError("smtp down")

    monkeypatch.setattr(get_mailer(), "send", boom)
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 502
    err = res.json()["error"]
    assert err["code"] == "MAIL_SEND_FAILED" and err["retryable"] is True and err["actions"] == [{"label": "다시 시도", "type": "retry_send"}]
    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "draft" and g2["editable"] is True
    logs = (await client.get(f"/cases/{reported_case}/rebuttal/sends", headers=auth_headers)).json()
    assert logs["items"][0]["result"] == "failed"


async def test_send_drops_video_when_too_large(client, auth_headers, reported_case, settle, monkeypatch):
    from app.services import rebuttal as rs

    monkeypatch.setattr(rs, "MAX_ATTACH_BYTES", 1000)
    await ready(client, auth_headers, reported_case, settle)
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 200 and res.json()["attachmentCount"] == 1
    sent = get_mailer().sent[-1]
    assert "블랙박스 영상은 용량 제한으로 첨부하지 못했습니다." in sent.body_text


async def test_send_log_survives_case_delete(client, auth_headers, reported_case, settle):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import SendLog

    await ready(client, auth_headers, reported_case, settle)
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    await client.delete(f"/cases/{reported_case}", headers=auth_headers)
    async with session_scope() as db:
        assert len((await db.execute(select(SendLog))).scalars().all()) == 1
