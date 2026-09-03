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
        # 명세 §8.1은 Sender 헤더에 가입 이메일을 넣으라고 했지만 SES는 Sender 주소도
        # 검증된 자격 증명일 것을 요구한다(554 Email address is not verified).
        # 사용자 주소를 전부 SES에 등록할 수는 없으므로 헤더를 넣지 않는다.
        # 회신 경로는 Reply-To가, 보낸 사람 표시는 From의 display name이 담당한다.
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
