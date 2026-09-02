from app.config import get_settings
from app.mail.base import Mailer, MailMessage, MailAttachment, MailSendError

_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        settings = get_settings()
        if settings.mail_backend == "smtp":
            from app.mail.smtp import SmtpMailer

            _mailer = SmtpMailer(settings)
        else:
            from app.mail.mock import MockMailer

            _mailer = MockMailer()
    return _mailer


def reset_mailer() -> None:
    global _mailer
    _mailer = None


__all__ = ["Mailer", "MailMessage", "MailAttachment", "MailSendError", "get_mailer", "reset_mailer"]
