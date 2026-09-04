from pydantic import Field

from app.schemas.base import CamelModel


class RevisionRequest(CamelModel):
    request: str = Field(min_length=1, max_length=500)
