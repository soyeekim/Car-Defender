from app.mail import get_mailer, reset_mailer
from app.mail.base import MailMessage
from app.mail.mock import MockMailer


async def test_mock_mailer_records_messages(test_env):
    reset_mailer()
    mailer = get_mailer()
    assert isinstance(mailer, MockMailer)
    mid = await mailer.send(MailMessage(to="a@b.co", subject="s", body_text="hi"))
    assert mid.startswith("mock-")
    assert mailer.sent[0].to == "a@b.co"
