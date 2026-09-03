from pydantic import Field

from app.schemas.base import CamelModel


class AttachmentPatch(CamelModel):
    ref_id: str
    included: bool


class RebuttalPatch(CamelModel):
    # 상한은 DB 칼럼 길이와 맞춘다(rebuttals.recipient varchar(320) · claim_number varchar(64)).
    # Postgres는 넘치는 값을 자르지 않고 write를 실패시키므로 입력 시점에 막는다.
    recipient: str | None = Field(default=None, max_length=320)
    claim_number: str | None = Field(default=None, max_length=64)
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    attachments: list[AttachmentPatch] | None = None
