from pydantic import field_validator

from app.schemas.base import CamelModel


class RenameCaseRequest(CamelModel):
    title: str

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 60:
            raise ValueError("제목은 1~60자예요.")
        return v
