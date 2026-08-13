"""
Shared helpers for the plain-dataclass models.

There's no ORM here — MongoDB documents are just dicts, and each model is a
plain ``@dataclass`` with ``to_doc``/``from_doc`` to convert to and from the
dict pymongo reads and writes. Every document's Mongo ``_id`` doubles as the
app-level ``id`` (a uuid4 hex string), so existing code that passes ids around
as plain strings needs no changes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def gen_uuid() -> str:
    return uuid.uuid4().hex
