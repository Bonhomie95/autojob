"""
Jobs and pipeline runs — tenant-scoped.

Mirrors the legacy ``jobs`` and ``runs`` tables from ``database.py`` but adds a
``user_id`` so every discovered job, generated document, and sent email belongs
to exactly one user. The ``url`` uniqueness that was global before is now
unique *per user* (two users can legitimately both discover the same posting).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import gen_uuid


class Job(db.Model):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_user_job_url"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Posting ──────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(512), default="")
    company: Mapped[str] = mapped_column(String(512), default="")
    location: Mapped[str] = mapped_column(String(512), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    salary: Mapped[str] = mapped_column(String(255), default="")
    posted_date: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(64), default="", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)

    # ── Contact ──────────────────────────────────────────────────
    hr_name: Mapped[str] = mapped_column(String(255), default="")
    hr_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    hr_title: Mapped[str] = mapped_column(String(255), default="")
    application_email: Mapped[str] = mapped_column(String(255), default="")
    application_url: Mapped[str] = mapped_column(Text, default="")
    contact_notes: Mapped[str] = mapped_column(Text, default="")

    # ── Workflow state ───────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    output_dir: Mapped[str] = mapped_column(Text, default="")

    email_status: Mapped[str] = mapped_column(String(32), default="not_sent")
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_error: Mapped[str] = mapped_column(Text, default="")

    follow_up_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_status: Mapped[str] = mapped_column(String(32), default="pending")
    reply_detected: Mapped[bool] = mapped_column(Boolean, default=False)

    portal_status: Mapped[str] = mapped_column(String(32), default="pending")
    portal_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    portal_error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=db.func.now(), nullable=False, index=True
    )


class Run(db.Model):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_scored: Mapped[int] = mapped_column(Integer, default=0)
    docs_generated: Mapped[int] = mapped_column(Integer, default=0)
    emails_sent: Mapped[int] = mapped_column(Integer, default=0)
    follow_ups_sent: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="running")
