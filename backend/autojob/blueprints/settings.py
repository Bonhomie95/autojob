"""
Per-user settings and credentials API.

Settings map to the ``UserSettings`` row (targeting, sending behaviour,
scheduling, consent). Credentials are written through the repository, which
encrypts them with Fernet — the plaintext secret is never persisted or echoed
back to the caller. Saving a credential reports only that one is set, never
its value.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ..services import repository as repo

settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")

_AI_PROVIDERS = ("groq", "openai", "anthropic", "gemini", "grok", "openrouter")
_CREDENTIAL_PROVIDERS = (*_AI_PROVIDERS, "hunter", "prospeo", "reoon", "million", "smtp", "imap")


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _int(data: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(data.get(key, default))))
    except (TypeError, ValueError):
        return default


def _serialize_settings(s) -> dict:
    return {
        "blacklistKeywords": s.blacklist_keywords,
        "minMatchScore": s.min_match_score,
        "minSalary": s.min_salary,
        "remoteOnly": s.remote_only,
        "targetCountries": s.target_countries,
        "autoSend": s.auto_send,
        "generateDocsWithoutHr": s.generate_docs_without_hr,
        "followUpEnabled": s.follow_up_enabled,
        "followUpDays": s.follow_up_days,
        "emailDailyLimit": s.email_daily_limit,
        "dedupWindowDays": s.dedup_window_days,
        "useOwnApiKeys": s.use_own_api_keys,
        "aiProvider": s.ai_provider,
        "scheduleEnabled": s.schedule_enabled,
        "hasSendingConsent": s.sending_consent_at is not None,
    }


@settings_bp.get("/")
@login_required
def page():
    s = repo.get_or_create_settings(current_user.id)
    return jsonify(
        settings=_serialize_settings(s),
        credentials=repo.list_credential_providers(current_user.id),
    )


@settings_bp.post("/general")
@login_required
def save_general():
    s = repo.get_or_create_settings(current_user.id)
    data = _body()
    updated = repo.update_settings(
        current_user.id,
        blacklist_keywords=(data.get("blacklistKeywords") or "").strip(),
        min_match_score=_int(data, "minMatchScore", s.min_match_score, 0, 100),
        min_salary=_int(data, "minSalary", s.min_salary, 0, 10_000_000),
        remote_only=bool(data.get("remoteOnly")),
        target_countries=(data.get("targetCountries") or "Remote").strip(),
        generate_docs_without_hr=bool(data.get("generateDocsWithoutHr")),
        follow_up_enabled=bool(data.get("followUpEnabled")),
        follow_up_days=_int(data, "followUpDays", s.follow_up_days, 1, 60),
        email_daily_limit=_int(data, "emailDailyLimit", s.email_daily_limit, 1, 1000),
        dedup_window_days=_int(data, "dedupWindowDays", s.dedup_window_days, 1, 365),
        schedule_enabled=bool(data.get("scheduleEnabled")),
    )
    return jsonify(settings=_serialize_settings(updated))


@settings_bp.post("/sending-consent")
@login_required
def sending_consent():
    """Explicit opt-in before AutoJob ever sends email on the user's behalf."""
    data = _body()
    if data.get("consent"):
        updated = repo.update_settings(
            current_user.id,
            sending_consent_at=datetime.now(UTC),
            auto_send=bool(data.get("autoSend")),
        )
    else:
        updated = repo.update_settings(
            current_user.id, sending_consent_at=None, auto_send=False
        )
    return jsonify(settings=_serialize_settings(updated))


@settings_bp.post("/credentials")
@login_required
def save_credentials():
    uid = current_user.id
    data = _body()
    # Master switch: even if the user has keys saved below, this can force
    # every run to use the platform-managed pool instead (see runtime_config).
    updates = {"use_own_api_keys": bool(data.get("useOwnApiKeys"))}
    ai_provider = (data.get("aiProvider") or "").strip().lower()
    if ai_provider:
        if ai_provider not in _AI_PROVIDERS:
            return jsonify(error=f"Unknown AI provider: {ai_provider}"), 400
        updates["ai_provider"] = ai_provider
    updated = repo.update_settings(uid, **updates)

    # LLM + enrichment keys (contact discovery + email verification).
    for provider in (*_AI_PROVIDERS, "hunter", "prospeo", "reoon", "million"):
        val = (data.get(provider) or "").strip()
        if val:
            repo.set_credential(uid, provider, val)

    # SMTP: password is the secret, host/port/from are non-secret meta.
    smtp_pw = (data.get("smtpPassword") or "").strip()
    if smtp_pw:
        meta = json.dumps({
            "host": (data.get("smtpHost") or "").strip(),
            "port": (data.get("smtpPort") or "587").strip(),
            "from": (data.get("smtpFrom") or "").strip(),
            "user": (data.get("smtpUser") or "").strip(),
            "from_name": (data.get("smtpFromName") or "").strip(),
            "reply_to": (data.get("smtpFrom") or "").strip(),
        })
        repo.set_credential(uid, "smtp", smtp_pw, meta=meta)

    # IMAP: for reply detection. Password is the secret; host/port/user are meta.
    imap_pw = (data.get("imapPassword") or "").strip()
    if imap_pw:
        meta = json.dumps({
            "host": (data.get("imapHost") or "").strip(),
            "port": (data.get("imapPort") or "993").strip(),
            "user": (data.get("imapUser") or "").strip(),
        })
        repo.set_credential(uid, "imap", imap_pw, meta=meta)

    return jsonify(settings=_serialize_settings(updated), credentials=repo.list_credential_providers(uid))


@settings_bp.post("/credentials/<provider>/remove")
@login_required
def remove_credential(provider: str):
    if provider not in _CREDENTIAL_PROVIDERS:
        return jsonify(error="Unknown credential."), 400

    if not repo.delete_credential(current_user.id, provider):
        return jsonify(error="Nothing to remove."), 404
    return jsonify(status="removed", credentials=repo.list_credential_providers(current_user.id))
