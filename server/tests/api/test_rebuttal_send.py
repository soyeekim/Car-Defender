import asyncio
import uuid

import pytest

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


async def test_send_retry_with_same_key_after_failure_short_circuits(client, auth_headers, reported_case, settle, monkeypatch):
    """실패 로그가 남은 뒤 같은 키로 재시도하면, 메일러를 다시 부르지 않고 바로 MAIL_SEND_FAILED를 낸다."""
    from app.mail.base import MailSendError

    await ready(client, auth_headers, reported_case, settle)
    calls = {"n": 0}

    async def boom(msg):
        calls["n"] += 1
        raise MailSendError("smtp down")

    monkeypatch.setattr(get_mailer(), "send", boom)
    key = str(uuid.uuid4())
    h = {**auth_headers, "Idempotency-Key": key}
    res1 = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res1.status_code == 502
    assert calls["n"] == 1

    res2 = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res2.status_code == 502 and res2.json()["error"]["code"] == "MAIL_SEND_FAILED"
    assert calls["n"] == 1  # 메일러를 다시 부르지 않았다

    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "draft" and g2["editable"] is True
    logs = (await client.get(f"/cases/{reported_case}/rebuttal/sends", headers=auth_headers)).json()
    assert len(logs["items"]) == 1 and logs["items"][0]["result"] == "failed"


async def test_send_logs_list_newest_first_with_two_rows(client, auth_headers, reported_case, settle, monkeypatch):
    from app.mail.base import MailSendError

    await ready(client, auth_headers, reported_case, settle)
    mailer = get_mailer()
    original_send = mailer.send

    async def boom(msg):
        raise MailSendError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    key1 = str(uuid.uuid4())
    h1 = {**auth_headers, "Idempotency-Key": key1}
    res1 = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h1)
    assert res1.status_code == 502

    monkeypatch.setattr(mailer, "send", original_send)
    key2 = str(uuid.uuid4())
    h2 = {**auth_headers, "Idempotency-Key": key2}
    res2 = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h2)
    assert res2.status_code == 200, res2.text

    logs = (await client.get(f"/cases/{reported_case}/rebuttal/sends", headers=auth_headers)).json()["items"]
    assert len(logs) == 2
    assert logs[0]["result"] == "sent" and logs[0]["sendLogId"] == res2.json()["sendLogId"]
    assert logs[1]["result"] == "failed"


async def test_send_generic_exception_from_mailer_is_mail_send_failed(client, auth_headers, reported_case, settle, monkeypatch):
    """MailSendError뿐 아니라 어떤 예외라도 실패 계약(로그 남기고 MAIL_SEND_FAILED)을 따른다."""
    await ready(client, auth_headers, reported_case, settle)

    async def boom(msg):
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(get_mailer(), "send", boom)
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 502 and res.json()["error"]["code"] == "MAIL_SEND_FAILED"

    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "draft" and g2["editable"] is True
    logs = (await client.get(f"/cases/{reported_case}/rebuttal/sends", headers=auth_headers)).json()
    assert logs["items"][0]["result"] == "failed"

    from sqlalchemy import select

    from app.db import session_scope
    from app.models import SendLog

    async with session_scope() as db:
        row = (await db.execute(select(SendLog).where(SendLog.case_id == reported_case))).scalar_one()
        assert row.error == "unexpected boom"


async def _bare_case_with_rebuttal(recipient: str = "kim@insu.co.kr", claim_number: str = "2026-08-0000") -> tuple[str, str]:
    """리포트·판정 없이 바로 보낼 수 있는 반박의견서를 DB에 직접 만든다 (동시성/멱등키 범위 테스트 전용)."""
    from app.clock import now_utc
    from app.db import session_scope
    from app.ids import new_id
    from app.models import Case, Rebuttal, User

    now = now_utc()
    uid = new_id()
    async with session_scope() as db:
        user = User(id=uid, email=f"{uid.lower()}@example.com", password_hash="x", created_at=now)
        db.add(user)
        await db.flush()  # user 행이 먼저 존재해야 case.user_id FK가 통과한다
        case = Case(id=new_id(), user_id=uid, title="사건", status="judged", created_at=now, updated_at=now)
        db.add(case)
        await db.flush()
        rebuttal = Rebuttal(
            id=new_id(), case_id=case.id, recipient=recipient, claim_number=claim_number,
            subject="테스트 제목", subject_auto=False, body="본문입니다.", attachments=[], status="draft",
            created_at=now, updated_at=now,
        )
        db.add(rebuttal)
        await db.commit()
        return case.id, user.id


async def _send_bare(case_id: str, user_id: str, key: str) -> dict:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Case, Rebuttal, User
    from app.services import rebuttal as rebuttal_service

    async with session_scope() as db:
        case = await db.get(Case, case_id)
        user = await db.get(User, user_id)
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        return await rebuttal_service.send(db, case, rebuttal, user, key)


async def _stick_in_sending(case_id: str, *, error: str | None = "in_progress", result: str = "failed") -> str:
    """sending에 갇힌 행을 흉내낸다. send()는 발송 직전에 SendLog(error="in_progress")를 먼저 남기고
    status를 sending으로 선점하므로, 복구 규칙이 보는 로그까지 같이 만들어 준다."""
    from sqlalchemy import select

    from app.clock import now_utc
    from app.db import session_scope
    from app.ids import new_id
    from app.models import Rebuttal, SendLog

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        rebuttal.status = "sending"
        log = SendLog(
            id=new_id(), case_id=case_id, rebuttal_id=rebuttal.id, idempotency_key=str(uuid.uuid4()),
            sent_at=now_utc(), from_email="hyun@example.com", recipient="kim@insu.co.kr", subject="테스트 제목",
            attachment_names=[], result=result, provider_message_id=None, error=error,
        )
        db.add(log)
        await db.commit()
        return log.id


async def test_send_concurrent_same_key_sends_exactly_once(app, monkeypatch):
    """같은 멱등키로 동시에 두 번 보내면: 메일은 한 번만 나가고, 이긴 쪽만 성공하며
    진 쪽은 MAIL_SEND_FAILED를 받는다(아직 승자가 끝나지 않았으므로). 이후 같은 키로
    다시 보내면 메일을 다시 보내지 않고 승자의 결과를 그대로 돌려준다.

    타이밍을 실제로 겹치게 확인하기 위해, 승자를 먼저 sending 상태(메일러에서 막힘)까지
    보내 놓고 그 상태에서 진 쪽을 보낸다 — asyncio.gather로 둘 다 동시에 던지면 스케줄링에
    따라 진 쪽의 재조회가 승자가 끝난 *뒤*에 일어날 수도 있어(둘 다 성공) 결과가 결정적이지
    않다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.errors import ApiError
    from app.models import Rebuttal

    case_id, user_id = await _bare_case_with_rebuttal()
    mailer = get_mailer()
    original_send = mailer.send
    gate = asyncio.Event()

    async def gated_send(msg):
        await gate.wait()
        return await original_send(msg)

    monkeypatch.setattr(mailer, "send", gated_send)
    key = str(uuid.uuid4())

    winner_task = asyncio.create_task(_send_bare(case_id, user_id, key))
    rebuttal = None
    for _ in range(100):
        async with session_scope() as db:
            rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
            if rebuttal.status == "sending":
                break
        await asyncio.sleep(0.01)
    assert rebuttal is not None and rebuttal.status == "sending" and not winner_task.done()

    with pytest.raises(ApiError) as ei:
        await _send_bare(case_id, user_id, key)
    assert ei.value.code == "MAIL_SEND_FAILED"

    gate.set()
    winner_result = await winner_task
    assert winner_result["sendLogId"]
    assert len(mailer.sent) == 1

    # 다시 같은 키로 보내면: 메일 다시 안 나가고 승자의 결과를 그대로 돌려준다.
    again = await _send_bare(case_id, user_id, key)
    assert again["sendLogId"] == winner_result["sendLogId"]
    assert len(mailer.sent) == 1


async def test_send_concurrent_different_keys_only_one_wins(app):
    """다른 멱등키로 동시에 보내면: 이긴 쪽만 메일을 보내고, 진 쪽의 플레이스홀더 로그는
    지워져(§B) sends 목록에 유령 실패 행을 남기지 않는다. 진 쪽 키로 재시도해도
    (플레이스홀더가 없으니 새로 검증을 거쳐) 여전히 REBUTTAL_ALREADY_SENT다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.errors import ApiError
    from app.models import SendLog

    case_id, user_id = await _bare_case_with_rebuttal()
    key1, key2 = str(uuid.uuid4()), str(uuid.uuid4())

    r1, r2 = await asyncio.gather(
        _send_bare(case_id, user_id, key1), _send_bare(case_id, user_id, key2), return_exceptions=True
    )

    results = [r1, r2]
    for r in results:
        if isinstance(r, Exception) and not isinstance(r, ApiError):
            raise r
    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, ApiError)]
    assert len(successes) == 1 and len(failures) == 1
    assert failures[0].code == "REBUTTAL_ALREADY_SENT"
    assert len(get_mailer().sent) == 1

    async with session_scope() as db:
        rows = (await db.execute(select(SendLog).where(SendLog.case_id == case_id))).scalars().all()
        assert len(rows) == 1 and rows[0].result == "sent"  # 진 쪽 플레이스홀더는 지워졌다

    loser_key = key1 if isinstance(r1, ApiError) else key2
    with pytest.raises(ApiError) as ei:
        await _send_bare(case_id, user_id, loser_key)
    assert ei.value.code == "REBUTTAL_ALREADY_SENT"


async def test_send_idempotency_key_scoped_by_case(app):
    case1_id, user1_id = await _bare_case_with_rebuttal()
    case2_id, user2_id = await _bare_case_with_rebuttal()
    key = str(uuid.uuid4())

    r1 = await _send_bare(case1_id, user1_id, key)
    r2 = await _send_bare(case2_id, user2_id, key)

    assert r1["sendLogId"] != r2["sendLogId"]
    assert len(get_mailer().sent) == 2


async def test_send_cancelled_task_reverts_to_draft_and_can_retry(app, monkeypatch):
    """발송 태스크가 취소되면(프로세스 종료·타임아웃 등을 흉내) sending에 갇힌 초안이
    draft로 되돌아가고, 새 키로 다시 보내면 정상적으로 발송된다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Rebuttal

    case_id, user_id = await _bare_case_with_rebuttal()
    mailer = get_mailer()
    original_send = mailer.send
    stuck = asyncio.Event()  # 절대 set되지 않는다 — 메일 발송이 영원히 멈춘 상황을 흉내낸다

    async def hang(msg):
        await stuck.wait()

    monkeypatch.setattr(mailer, "send", hang)
    key = str(uuid.uuid4())

    task = asyncio.create_task(_send_bare(case_id, user_id, key))
    rebuttal = None
    for _ in range(100):
        async with session_scope() as db:
            rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
            if rebuttal.status == "sending":
                break
        await asyncio.sleep(0.01)
    assert rebuttal is not None and rebuttal.status == "sending" and not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        assert rebuttal.status == "draft"

    monkeypatch.setattr(mailer, "send", original_send)
    retry_key = str(uuid.uuid4())
    result = await _send_bare(case_id, user_id, retry_key)
    assert result["sendLogId"]
    assert len(mailer.sent) == 1


async def test_send_reset_stuck_sending_service_function(app):
    """reset_stuck_sending()은 '최신 발송기록이 in_progress'인 sending 행을 draft로 되돌린다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Rebuttal
    from app.services import rebuttal as rebuttal_service

    case_id, _user_id = await _bare_case_with_rebuttal()
    await _stick_in_sending(case_id)

    async with session_scope() as db:
        n = await rebuttal_service.reset_stuck_sending(db)
    assert n >= 1

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        assert rebuttal.status == "draft"


async def test_send_reset_stuck_sending_leaves_finished_rows_alone(app):
    """최신 발송기록이 in_progress가 아니면(예: 이미 sent) 메일은 나갔는데 상태만 못 바꾼 경우일 수 있다.
    draft로 풀면 중복 발송이 되므로 sending 그대로 두고 수동 확인 대상으로 남긴다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Rebuttal
    from app.services import rebuttal as rebuttal_service

    case_id, _user_id = await _bare_case_with_rebuttal()
    await _stick_in_sending(case_id, error=None, result="sent")

    async with session_scope() as db:
        n = await rebuttal_service.reset_stuck_sending(db)
    assert n == 0

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        assert rebuttal.status == "sending"


async def test_send_reset_stuck_sending_leaves_rows_without_any_send_log(app):
    """발송기록이 아예 없는 sending 행도 send()가 만든 상태가 아니다 — 손대지 않는다."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Rebuttal
    from app.services import rebuttal as rebuttal_service

    case_id, _user_id = await _bare_case_with_rebuttal()
    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        rebuttal.status = "sending"
        await db.commit()

    async with session_scope() as db:
        assert await rebuttal_service.reset_stuck_sending(db) == 0

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        assert rebuttal.status == "sending"


async def test_send_reset_stuck_sending_on_fresh_app_start(test_env):
    """실제 서버 기동(lifespan)이 sending에 갇힌 행을 draft로 되돌리는지 끝까지 확인한다."""
    from asgi_lifespan import LifespanManager
    from sqlalchemy import select

    from app.config import get_settings
    from app.db import configure_database, create_all, session_scope
    from app.models import Rebuttal

    settings = get_settings()
    configure_database(settings.database_url)
    await create_all()

    case_id, _user_id = await _bare_case_with_rebuttal()
    await _stick_in_sending(case_id)

    from app.main import create_app

    application = create_app()
    async with LifespanManager(application):
        pass  # 기동 중 reset_stuck_sending()이 실행된다

    async with session_scope() as db:
        rebuttal = (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one()
        assert rebuttal.status == "draft"


async def test_send_rejects_over_long_idempotency_key(client, auth_headers, reported_case, settle):
    """send_logs.idempotency_key는 varchar(64)다. 더 긴 키는 기록 시점에 터지므로 미리 막는다."""
    await ready(client, auth_headers, reported_case, settle)
    h = {**auth_headers, "Idempotency-Key": "k" * 65}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "VALIDATION_FAILED"
    assert err["fields"] == {"Idempotency-Key": "키가 너무 길어요 (최대 64자)."}

    ok = {**auth_headers, "Idempotency-Key": "k" * 64}
    assert (await client.post(f"/cases/{reported_case}/rebuttal/send", headers=ok)).status_code == 200
