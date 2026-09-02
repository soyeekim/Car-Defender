from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SendLog(Base):
    __tablename__ = "send_logs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), index=True)  # FK 없음
    rebuttal_id: Mapped[str] = mapped_column(String(26))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    attachment_names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result: Mapped[str] = mapped_column(String(10), nullable=False)  # sent | failed
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
