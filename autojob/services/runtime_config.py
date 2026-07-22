"""
Per-user runtime configuration.

The legacy engine reads one global ``config`` singleton sourced from a single
``.env``. In a multi-tenant world each run must instead use *that user's*
settings and *that user's* credentials. ``build_runtime_config`` assembles a
plain object from ``UserSettings`` + decrypted ``UserCredential`` rows; Phase 5
threads it into the pipeline so scraping, scoring, and sending all run with the
right tenant's configuration and secrets.

Managed vs. bring-your-own: if a user hasn't supplied a credential (e.g. a Groq
key), we fall back to a platform-managed pool from the app config — this is what
lets a user "just upload a CV" without configuring any API keys themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flask import current_app

from . import repository as repo


@dataclass
class RuntimeConfig:
    user_id: str

    # Targeting
    blacklist_keywords: list[str] = field(default_factory=list)
    min_match_score: int = 60
    min_salary: int = 0
    remote_only: bool = True
    target_countries: list[str] = field(default_factory=lambda: ["Remote"])

    # Sending behaviour
    auto_send: bool = False
    generate_docs_without_hr: bool = True
    follow_up_enabled: bool = True
    follow_up_days: int = 6
    dedup_window_days: int = 30
    email_daily_limit: int = 100
    has_sending_consent: bool = False

    # Scheduling
    schedule_enabled: bool = False
    schedule_cron: str = "0 8 * * 1-5"

    # Credentials (decrypted, in memory only)
    groq_keys: list[str] = field(default_factory=list)
    hunter_keys: list[str] = field(default_factory=list)
    smtp: dict = field(default_factory=dict)
    imap: dict = field(default_factory=dict)

    # Whether the platform is supplying managed keys the user didn't set.
    managed_llm: bool = False


def _split(csv: str) -> list[str]:
    return [x.strip() for x in (csv or "").split(",") if x.strip()]


def _managed_pool(config_key: str) -> list[str]:
    raw = current_app.config.get(config_key, "")
    return _split(raw) if isinstance(raw, str) else list(raw or [])


def build_runtime_config(user_id: str) -> RuntimeConfig:
    s = repo.get_or_create_settings(user_id)

    groq = _split(repo.get_credential(user_id, "groq"))
    managed_llm = False
    if not groq:
        groq = _managed_pool("MANAGED_GROQ_KEYS")
        managed_llm = bool(groq)

    hunter = _split(repo.get_credential(user_id, "hunter"))

    smtp = {}
    smtp_secret = repo.get_credential(user_id, "smtp")
    if smtp_secret:
        # meta holds host/port/from; secret holds the password
        row_meta = _credential_meta(user_id, "smtp")
        smtp = {**row_meta, "password": smtp_secret}

    imap = {}
    imap_secret = repo.get_credential(user_id, "imap")
    if imap_secret:
        imap = {**_credential_meta(user_id, "imap"), "password": imap_secret}

    return RuntimeConfig(
        user_id=user_id,
        blacklist_keywords=_split(s.blacklist_keywords),
        min_match_score=s.min_match_score,
        min_salary=s.min_salary,
        remote_only=s.remote_only,
        target_countries=_split(s.target_countries) or ["Remote"],
        auto_send=s.auto_send,
        generate_docs_without_hr=s.generate_docs_without_hr,
        follow_up_enabled=s.follow_up_enabled,
        follow_up_days=s.follow_up_days,
        dedup_window_days=s.dedup_window_days,
        email_daily_limit=s.email_daily_limit,
        has_sending_consent=s.sending_consent_at is not None,
        schedule_enabled=s.schedule_enabled,
        schedule_cron=s.schedule_cron,
        groq_keys=groq,
        hunter_keys=hunter,
        smtp=smtp,
        imap=imap,
        managed_llm=managed_llm,
    )


def _credential_meta(user_id: str, provider: str) -> dict:
    import json

    from ..extensions import db
    from ..models import UserCredential

    row = db.session.scalar(
        db.select(UserCredential).where(
            UserCredential.user_id == user_id, UserCredential.provider == provider
        )
    )
    if not row or not row.meta:
        return {}
    try:
        return json.loads(row.meta)
    except (ValueError, TypeError):
        return {}
