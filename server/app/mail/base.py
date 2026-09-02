from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class MailAttachment:
    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"


@dataclass
class MailMessage:
    to: str
    subject: str
    body_text: str
    reply_to: str | None = None
    sender_email: str | None = None
    display_name: str | None = None
    attachments: list[MailAttachment] = field(default_factory=list)


class MailSendError(Exception):
    pass


class Mailer(Protocol):
    async def send(self, msg: MailMessage) -> str: ...

    def healthy(self) -> bool: ...
