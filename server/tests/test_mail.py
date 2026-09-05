import email.policy

import aiosmtplib
import pytest

from app.config import Settings
from app.mail import get_mailer, reset_mailer
from app.mail.base import MailAttachment, MailMessage, MailSendError
from app.mail.mock import MockMailer
from app.mail.smtp import SmtpMailer


async def test_mock_mailer_records_messages(test_env):
    reset_mailer()
    mailer = get_mailer()
    assert isinstance(mailer, MockMailer)
    mid = await mailer.send(MailMessage(to="a@b.co", subject="s", body_text="hi"))
    assert mid.startswith("mock-")
    assert mailer.sent[0].to == "a@b.co"


def _smtp_settings() -> Settings:
    return Settings(mail_from="no-reply@fairway.click", smtp_host="smtp.example.com")


async def test_smtp_mailer_headers(monkeypatch):
    captured = {}

    async def fake_send(em, **kwargs):
        captured["msg"] = em

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    mailer = SmtpMailer(_smtp_settings())
    msg = MailMessage(
        to="kim@insu.co.kr",
        subject="과실비율 재검토 요청 (접수번호 2026-08-0000)",
        body_text="본문입니다.",
        reply_to="hyun@example.com",
        sender_email="hyun@example.com",
        display_name="Fairway (hyun@example.com)",
        attachments=[
            MailAttachment(filename="사건경위서_20260822.pdf", content=b"%PDF-1.4", mime_type="application/pdf"),
            MailAttachment(filename="blackbox_0822.mp4", content=b"\x00\x00", mime_type="video/mp4"),
        ],
    )
    message_id = await mailer.send(msg)
    assert message_id

    parsed = email.message_from_bytes(captured["msg"].as_bytes(), policy=email.policy.default)
    assert parsed["From"].addresses[0].display_name == "Fairway (hyun@example.com)"
    assert parsed["From"].addresses[0].addr_spec == "no-reply@fairway.click"
    # SES가 Sender 주소도 검증된 자격 증명일 것을 요구하므로 이 헤더는 넣지 않는다.
    # 사용자 주소는 Reply-To와 From의 display name으로만 드러난다.
    assert parsed["Sender"] is None
    assert parsed["Reply-To"] == "hyun@example.com"
    assert parsed["Subject"] == "과실비율 재검토 요청 (접수번호 2026-08-0000)"
    assert parsed["To"] == "kim@insu.co.kr"
    names = [p.get_filename() for p in parsed.iter_attachments()]
    assert names == ["사건경위서_20260822.pdf", "blackbox_0822.mp4"]


async def test_smtp_mailer_wraps_smtp_exception(monkeypatch):
    async def fake_send(em, **kwargs):
        raise aiosmtplib.SMTPException("smtp down")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    mailer = SmtpMailer(_smtp_settings())
    msg = MailMessage(to="kim@insu.co.kr", subject="s", body_text="본문")
    with pytest.raises(MailSendError):
        await mailer.send(msg)


async def test_smtp_mailer_uses_configured_timeout(monkeypatch):
    # 25MB 첨부는 base64 후 33MB가 되어 업로드에 1분 가까이 걸린다. 30초 고정값은 실제 크기의
    # 블랙박스 영상 첨부를 항상 실패시켰다(실측: 30초 실패, 실소요 51.7초). 값은 설정에서 와야 한다.
    captured = {}

    async def fake_send(em, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    settings = Settings(mail_from="no-reply@fairway.click", smtp_host="smtp.example.com", smtp_timeout_seconds=777)
    await SmtpMailer(settings).send(MailMessage(to="kim@insu.co.kr", subject="s", body_text="본문"))
    assert captured["timeout"] == 777
