from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

CASE_STATUSES = ("intake", "analyzing", "needs_review", "judged", "sent", "closed")


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(60), nullable=False, default="새 사건")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="intake")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    messages = relationship("Message", cascade="all, delete-orphan", passive_deletes=True)
    video = relationship("Video", cascade="all, delete-orphan", passive_deletes=True, uselist=False)
    analysis = relationship("Analysis", cascade="all, delete-orphan", passive_deletes=True, uselist=False)
    verdicts = relationship("Verdict", cascade="all, delete-orphan", passive_deletes=True)
    reports = relationship("Report", cascade="all, delete-orphan", passive_deletes=True)
    rebuttal = relationship("Rebuttal", cascade="all, delete-orphan", passive_deletes=True, uselist=False)
    jobs = relationship("Job", cascade="all, delete-orphan", passive_deletes=True)
