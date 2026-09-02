from pydantic import Field

from app.schemas.base import CamelModel


class AttachmentPatch(CamelModel):
    ref_id: str
    included: bool


class RebuttalPatch(CamelModel):
    recipient: str | None = None
    claim_number: str | None = None
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    attachments: list[AttachmentPatch] | None = None
