"""
Per-user settings and credentials.

Settings map to the ``UserSettings`` row (targeting, sending behaviour,
scheduling, consent). Credentials are written through the repository, which
encrypts them with Fernet — the plaintext secret is never persisted or echoed
back to the page. Saving a credential shows only that one is set, never its
value.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import UserCredential
from ..services import repository as repo

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


def _bool(name: str) -> bool:
    return request.form.get(name) in ("on", "true", "1", "yes")


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(request.form.get(name, default))))
    except (TypeError, ValueError):
        return default


def _which_set(user_id: str) -> dict[str, bool]:
    rows = db.session.scalars(
        db.select(UserCredential).where(UserCredential.user_id == user_id)
    )
    return {r.provider: True for r in rows}


@settings_bp.get("/")
@login_required
def page():
    s = repo.get_or_create_settings(current_user.id)
    return render_template(
        "dashboard/settings.html", settings=s, creds=_which_set(current_user.id)
    )


@settings_bp.post("/general")
@login_required
def save_general():
    s = repo.get_or_create_settings(current_user.id)
    s.blacklist_keywords = (request.form.get("blacklist_keywords") or "").strip()
    s.min_match_score = _int("min_match_score", s.min_match_score, 0, 100)
    s.min_salary = _int("min_salary", s.min_salary, 0, 10_000_000)
    s.remote_only = _bool("remote_only")
    s.target_countries = (request.form.get("target_countries") or "Remote").strip()
    s.generate_docs_without_hr = _bool("generate_docs_without_hr")
    s.follow_up_enabled = _bool("follow_up_enabled")
    s.follow_up_days = _int("follow_up_days", s.follow_up_days, 1, 60)
    s.schedule_enabled = _bool("schedule_enabled")
    db.session.commit()
    flash("Settings saved.", "success")
    return redirect(url_for("settings.page"))


@settings_bp.post("/sending-consent")
@login_required
def sending_consent():
    """Explicit opt-in before AutoJob ever sends email on the user's behalf."""
    s = repo.get_or_create_settings(current_user.id)
    if _bool("consent"):
        s.sending_consent_at = datetime.now(UTC)
        s.auto_send = _bool("auto_send")
        flash("Auto-send preferences updated.", "success")
    else:
        s.sending_consent_at = None
        s.auto_send = False
        flash("Auto-send disabled. AutoJob won't send anything on your behalf.", "warning")
    db.session.commit()
    return redirect(url_for("settings.page"))


@settings_bp.post("/credentials")
@login_required
def save_credentials():
    uid = current_user.id
    # LLM + enrichment keys.
    for provider in ("groq", "hunter"):
        val = (request.form.get(provider) or "").strip()
        if val:
            repo.set_credential(uid, provider, val)

    # SMTP: password is the secret, host/port/from are non-secret meta.
    smtp_pw = (request.form.get("smtp_password") or "").strip()
    if smtp_pw:
        meta = json.dumps({
            "host": (request.form.get("smtp_host") or "").strip(),
            "port": (request.form.get("smtp_port") or "587").strip(),
            "from": (request.form.get("smtp_from") or "").strip(),
            "user": (request.form.get("smtp_user") or "").strip(),
            "from_name": (request.form.get("smtp_from_name") or "").strip(),
            "reply_to": (request.form.get("smtp_from") or "").strip(),
        })
        repo.set_credential(uid, "smtp", smtp_pw, meta=meta)

    # IMAP: for reply detection. Password is the secret; host/port/user are meta.
    imap_pw = (request.form.get("imap_password") or "").strip()
    if imap_pw:
        meta = json.dumps({
            "host": (request.form.get("imap_host") or "").strip(),
            "port": (request.form.get("imap_port") or "993").strip(),
            "user": (request.form.get("imap_user") or "").strip(),
        })
        repo.set_credential(uid, "imap", imap_pw, meta=meta)

    flash("Credentials saved securely.", "success")
    return redirect(url_for("settings.page"))
