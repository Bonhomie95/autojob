"""
CV documents, parsed profiles, and ambiguity resolutions — all tenant-scoped.

``CvDocument`` is an uploaded file (stored via the storage backend, Phase 4).
``CvProfile`` caches the parsed profile keyed by content hash *and* user — the
same résumé uploaded by two people is parsed independently. ``CvChoice``
records which of two conflicting contact values (e.g. two emails) the user
picked, so re-parsing never silently reuses a stale answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .base import gen_uuid, utcnow


@dataclass
class CvDocument:
    COLLECTION = "cv_documents"

    user_id: str
    filename: str
    storage_key: str
    content_hash: str
    id: str = field(default_factory=gen_uuid)
    size_bytes: int = 0
    is_active: bool = False

    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "filename": self.filename,
            "storage_key": self.storage_key,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> CvDocument:
        return cls(
            id=doc["_id"],
            user_id=doc["user_id"],
            filename=doc.get("filename", ""),
            storage_key=doc.get("storage_key", ""),
            content_hash=doc.get("content_hash", ""),
            size_bytes=doc.get("size_bytes", 0),
            is_active=doc.get("is_active", False),
            created_at=doc.get("created_at", utcnow()),
            updated_at=doc.get("updated_at", utcnow()),
        )


@dataclass
class CvProfile:
    COLLECTION = "cv_profiles"

    user_id: str
    content_hash: str
    id: str = field(default_factory=gen_uuid)
    filename: str = ""
    profile: dict = field(default_factory=dict)

    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "content_hash": self.content_hash,
            "filename": self.filename,
            "profile": self.profile,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> CvProfile:
        return cls(
            id=doc["_id"],
            user_id=doc["user_id"],
            content_hash=doc.get("content_hash", ""),
            filename=doc.get("filename", ""),
            profile=doc.get("profile") or {},
        )


@dataclass
class CvChoice:
    COLLECTION = "cv_choices"

    user_id: str
    content_hash: str
    field_name: str
    id: str = field(default_factory=gen_uuid)
    value: str = ""

    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "content_hash": self.content_hash,
            "field": self.field_name,
            "value": self.value,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> CvChoice:
        return cls(
            id=doc["_id"],
            user_id=doc["user_id"],
            content_hash=doc.get("content_hash", ""),
            field_name=doc.get("field", ""),
            value=doc.get("value", ""),
        )
