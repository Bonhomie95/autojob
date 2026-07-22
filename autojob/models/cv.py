"""
CV documents, parsed profiles, and ambiguity resolutions — all tenant-scoped.

``CvDocument`` is an uploaded file (stored via the storage backend, Phase 4).
``CvProfile`` caches the parsed profile keyed by content hash *and* user — the
same résumé uploaded by two people is parsed independently. ``CvChoice``
records which of two conflicting contact values (e.g. two emails) the user
picked, so re-parsing never silently reuses a stale answer.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import TimestampMixin, gen_uuid


class CvDocument(TimestampMixin, db.Model):
    __tablename__ = "cv_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class CvProfile(TimestampMixin, db.Model):
    __tablename__ = "cv_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", name="uq_user_cv_hash"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), default="")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)


class CvChoice(TimestampMixin, db.Model):
    __tablename__ = "cv_choices"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", "field", name="uq_user_cv_choice"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
