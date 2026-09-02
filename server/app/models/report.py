from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    caveat: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision_request: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pdf = relationship("ReportPdf", cascade="all, delete-orphan", passive_deletes=True, uselist=False)


class ReportPdf(Base):
    __tablename__ = "report_pdfs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(26), ForeignKey("reports.id", ondelete="CASCADE"), unique=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
