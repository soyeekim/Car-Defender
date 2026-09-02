from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Rebuttal(Base):
    __tablename__ = "rebuttals"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), unique=True)
    recipient: Mapped[str | None] = mapped_column(String(320))
    claim_number: Mapped[str | None] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")  # draft | sent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
