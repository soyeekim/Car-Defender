from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid

import aiosmtplib

from app.config import Settings
from app.mail.base import MailMessage, MailSendError


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def healthy(self) -> bool:
        return bool(self.s.smtp_host and self.s.mail_from)

    async def send(self, msg: MailMessage) -> str:
        em = EmailMessage()
        display = msg.display_name or "Fairway"
        em["From"] = Address(display_name=display, addr_spec=self.s.mail_from)
        em["To"] = msg.to
        em["Subject"] = msg.subject
        if msg.sender_email:
            em["Sender"] = msg.sender_email
        if msg.reply_to:
            em["Reply-To"] = msg.reply_to
        message_id = make_msgid(domain=self.s.mail_from.split("@")[-1])
        em["Message-ID"] = message_id
        em.set_content(msg.body_text)
        for att in msg.attachments:
            maintype, _, subtype = att.mime_type.partition("/")
            em.add_attachment(att.content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=att.filename)
        try:
            await aiosmtplib.send(
                em,
                hostname=self.s.smtp_host,
                port=self.s.smtp_port,
                username=self.s.smtp_user or None,
                password=self.s.smtp_password or None,
                start_tls=self.s.smtp_starttls,
                timeout=30,
            )
        except (aiosmtplib.SMTPException, OSError) as e:
            raise MailSendError(str(e)) from e
        return message_id
