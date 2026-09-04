from pydantic import Field

from app.schemas.base import CamelModel


class SendMessageRequest(CamelModel):
    text: str = Field(min_length=1, max_length=2000)
