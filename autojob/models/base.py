"""
Shared model base and mixins.

The whole data model is SQLite- and Postgres-compatible: no engine-specific
column types, JSON stored via SQLAlchemy's portable ``JSON`` type. Tenancy is
expressed with a ``user_id`` foreign key on every per-user table plus a
``TenantMixin`` marker so the data-access layer can assert isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def gen_uuid() -> str:
    return uuid.uuid4().hex


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TenantMixin:
    """
    Mixin for any table that belongs to a single user (tenant).

    Every query against a tenant table MUST filter by ``user_id``; the
    ``autojob.services.repository`` layer enforces this so no route can leak
    one tenant's rows to another.
    """

    @classmethod
    def __declare_last__(cls):  # pragma: no cover - declarative hook
        pass

    user_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
