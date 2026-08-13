"""
User, per-user settings, and encrypted per-user credentials.

``User`` is the tenant. ``UserSettings`` holds what used to live in the global
``.env`` (target roles, thresholds, sending toggles) but scoped to one person.
``UserCredential`` stores third-party secrets (their SMTP password, API keys)
encrypted at rest with Fernet — the plaintext never touches the database.

Plain dataclasses, not an ORM: each Mongo document's ``_id`` is the model's
``id`` (a uuid4 hex string), and ``to_doc``/``from_doc`` convert to and from
the dict pymongo reads and writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .base import gen_uuid, utcnow


@dataclass
class User(UserMixin):
    COLLECTION = "users"

    email: str
    name: str = ""
    password_hash: str = ""
    id: str = field(default_factory=gen_uuid)

    is_active: bool = True
    is_admin: bool = False
    email_verified: bool = False

    # Billing / plan gating (kept simple for now).
    plan: str = "free"

    last_login_at: datetime | None = None

    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    # ── Password handling ─────────────────────────────────────────
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # ── Flask-Login ──────────────────────────────────────────────
    def get_id(self) -> str:
        return self.id

    # ── Mongo (de)serialisation ────────────────────────────────────
    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "email": self.email,
            "name": self.name,
            "password_hash": self.password_hash,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
            "email_verified": self.email_verified,
            "plan": self.plan,
            "last_login_at": self.last_login_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> User:
        return cls(
            id=doc["_id"],
            email=doc["email"],
            name=doc.get("name", ""),
            password_hash=doc.get("password_hash", ""),
            is_active=doc.get("is_active", True),
            is_admin=doc.get("is_admin", False),
            email_verified=doc.get("email_verified", False),
            plan=doc.get("plan", "free"),
            last_login_at=doc.get("last_login_at"),
            created_at=doc.get("created_at", utcnow()),
            updated_at=doc.get("updated_at", utcnow()),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


@dataclass
class UserSettings:
    """Per-user pipeline configuration (formerly the global .env)."""

    COLLECTION = "user_settings"

    user_id: str
    id: str = field(default_factory=gen_uuid)

    # Targeting — most of this is derived from the CV, but a user can override.
    blacklist_keywords: str = "internship,unpaid"
    min_match_score: int = 60
    min_salary: int = 0
    remote_only: bool = True
    target_countries: str = "Remote"

    # Sending behaviour.
    auto_send: bool = False
    generate_docs_without_hr: bool = True
    follow_up_enabled: bool = True
    follow_up_days: int = 6
    dedup_window_days: int = 30
    email_daily_limit: int = 100

    # Master switch for BYO AI/enrichment keys (Settings → Credentials). Off
    # means always use the platform-managed pool for Groq/Hunter/Prospeo/
    # Reoon/Million, even if the user has a key saved.
    use_own_api_keys: bool = True

    # Which AI provider tailors CVs/cover letters and scores jobs. The
    # matching credential (see UserCredential.provider) is looked up under
    # this same name. One of: groq, openai, anthropic, gemini, openrouter.
    ai_provider: str = "groq"

    # Scheduling.
    schedule_enabled: bool = False
    schedule_cron: str = "0 8 * * 1-5"

    # Consent — required before we ever send on the user's behalf (Phase 7).
    sending_consent_at: datetime | None = None

    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "blacklist_keywords": self.blacklist_keywords,
            "min_match_score": self.min_match_score,
            "min_salary": self.min_salary,
            "remote_only": self.remote_only,
            "target_countries": self.target_countries,
            "auto_send": self.auto_send,
            "generate_docs_without_hr": self.generate_docs_without_hr,
            "follow_up_enabled": self.follow_up_enabled,
            "follow_up_days": self.follow_up_days,
            "dedup_window_days": self.dedup_window_days,
            "email_daily_limit": self.email_daily_limit,
            "use_own_api_keys": self.use_own_api_keys,
            "ai_provider": self.ai_provider,
            "schedule_enabled": self.schedule_enabled,
            "schedule_cron": self.schedule_cron,
            "sending_consent_at": self.sending_consent_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> UserSettings:
        defaults = cls(user_id=doc["user_id"])
        return cls(
            id=doc["_id"],
            user_id=doc["user_id"],
            blacklist_keywords=doc.get("blacklist_keywords", defaults.blacklist_keywords),
            min_match_score=doc.get("min_match_score", defaults.min_match_score),
            min_salary=doc.get("min_salary", defaults.min_salary),
            remote_only=doc.get("remote_only", defaults.remote_only),
            target_countries=doc.get("target_countries", defaults.target_countries),
            auto_send=doc.get("auto_send", defaults.auto_send),
            generate_docs_without_hr=doc.get(
                "generate_docs_without_hr", defaults.generate_docs_without_hr
            ),
            follow_up_enabled=doc.get("follow_up_enabled", defaults.follow_up_enabled),
            follow_up_days=doc.get("follow_up_days", defaults.follow_up_days),
            dedup_window_days=doc.get("dedup_window_days", defaults.dedup_window_days),
            email_daily_limit=doc.get("email_daily_limit", defaults.email_daily_limit),
            use_own_api_keys=doc.get("use_own_api_keys", defaults.use_own_api_keys),
            ai_provider=doc.get("ai_provider", defaults.ai_provider),
            schedule_enabled=doc.get("schedule_enabled", defaults.schedule_enabled),
            schedule_cron=doc.get("schedule_cron", defaults.schedule_cron),
            sending_consent_at=doc.get("sending_consent_at"),
            created_at=doc.get("created_at", utcnow()),
            updated_at=doc.get("updated_at", utcnow()),
        )


@dataclass
class UserCredential:
    """
    A single encrypted secret owned by a user.

    ``provider`` names the integration (e.g. 'groq', 'hunter', 'smtp',
    'imap'); ``ciphertext`` is the Fernet-encrypted value. Decryption happens
    only in memory, only when the pipeline needs it (see
    ``autojob.services.crypto``).

    Uniqueness of (user_id, provider, name) is enforced by a Mongo index
    (see db_bootstrap.ensure_indexes), mirroring the old SQL constraint.
    """

    COLLECTION = "user_credentials"

    user_id: str
    provider: str
    id: str = field(default_factory=gen_uuid)
    name: str = "default"
    ciphertext: str = ""
    # Non-secret metadata safe to display (e.g. the SMTP host, the from-addr).
    meta: str = ""

    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "provider": self.provider,
            "name": self.name,
            "ciphertext": self.ciphertext,
            "meta": self.meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> UserCredential:
        return cls(
            id=doc["_id"],
            user_id=doc["user_id"],
            provider=doc["provider"],
            name=doc.get("name", "default"),
            ciphertext=doc.get("ciphertext", ""),
            meta=doc.get("meta", ""),
            created_at=doc.get("created_at", utcnow()),
            updated_at=doc.get("updated_at", utcnow()),
        )
