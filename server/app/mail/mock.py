import logging

from app.ids import new_id
from app.mail.base import MailMessage

log = logging.getLogger(__name__)


class MockMailer:
    def __init__(self) -> None:
        self.sent: list[MailMessage] = []

    async def send(self, msg: MailMessage) -> str:
        self.sent.append(msg)
        log.info("[mock mail] to=%s subject=%s attachments=%d", msg.to, msg.subject, len(msg.attachments))
        return f"mock-{new_id()}"

    def healthy(self) -> bool:
        return True
