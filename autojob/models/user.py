"""
User, per-user settings, and encrypted per-user credentials.

``User`` is the tenant. ``UserSettings`` holds what used to live in the global
``.env`` (target roles, thresholds, sending toggles) but scoped to one person.
``UserCredential`` stores third-party secrets (their SMTP password, API keys)
encrypted at rest with Fernet — the plaintext never touches the database.
"""

from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from .base import TimestampMixin, gen_uuid


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Billing / plan gating (kept simple for now).
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships (all cascade-delete so removing a user removes their data).
    settings: Mapped[UserSettings] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    credentials: Mapped[list[UserCredential]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    # ── Password handling ─────────────────────────────────────────
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


class UserSettings(TimestampMixin, db.Model):
    """Per-user pipeline configuration (formerly the global .env)."""

    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # Targeting — most of this is derived from the CV, but a user can override.
    blacklist_keywords: Mapped[str] = mapped_column(Text, default="internship,unpaid")
    min_match_score: Mapped[int] = mapped_column(Integer, default=60)
    min_salary: Mapped[int] = mapped_column(Integer, default=0)
    remote_only: Mapped[bool] = mapped_column(Boolean, default=True)
    target_countries: Mapped[str] = mapped_column(Text, default="Remote")

    # Sending behaviour.
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False)
    generate_docs_without_hr: Mapped[bool] = mapped_column(Boolean, default=True)
    follow_up_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    follow_up_days: Mapped[int] = mapped_column(Integer, default=6)
    dedup_window_days: Mapped[int] = mapped_column(Integer, default=30)
    email_daily_limit: Mapped[int] = mapped_column(Integer, default=100)

    # Scheduling.
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_cron: Mapped[str] = mapped_column(String(64), default="0 8 * * 1-5")

    # Consent — required before we ever send on the user's behalf (Phase 7).
    sending_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="settings")


class UserCredential(TimestampMixin, db.Model):
    """
    A single encrypted secret owned by a user.

    ``provider`` names the integration (e.g. 'groq', 'hunter', 'smtp',
    'imap'); ``ciphertext`` is the Fernet-encrypted value. Decryption happens
    only in memory, only when the pipeline needs it (see
    ``autojob.services.crypto``).
    """

    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "name", name="uq_user_provider_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), default="default")
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # Non-secret metadata safe to display (e.g. the SMTP host, the from-addr).
    meta: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="credentials")
