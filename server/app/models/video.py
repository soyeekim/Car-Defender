from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False, default="video/mp4")
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON))
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
