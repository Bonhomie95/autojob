"""
CV API — the heart of "just upload your CV".

Everything is scoped to the signed-in user: uploads are validated (extension +
magic bytes + size), stored under the user's prefix, parsed, and surfaced as a
profile with any ambiguous contact fields flagged for the user to resolve.
"""

from __future__ import annotations

import logging
import re

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from ..extensions import limiter
from ..services import cv_service
from ..services import repository as repo

logger = logging.getLogger(__name__)

cv_bp = Blueprint("cv", __name__, url_prefix="/api/cv")

_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w-]+", re.I)
_GITHUB_RE = re.compile(r"github\.com/[\w-]+", re.I)


def _validate_custom_contact(field: str, value: str) -> str | None:
    """Return an error message if ``value`` doesn't look like a valid
    ``field``, or None if it's fine. A typo'd phone number or garbage URL
    would otherwise propagate silently into every generated application."""
    if field == "email" and ("@" not in value or "." not in value.split("@")[-1]):
        return "That doesn't look like a valid email address."
    if field == "phone" and len(re.sub(r"\D", "", value)) < 7:
        return "That doesn't look like a valid phone number."
    if field == "linkedin" and not _LINKEDIN_RE.search(value):
        return "That doesn't look like a LinkedIn profile URL (linkedin.com/in/...)."
    if field == "github" and not _GITHUB_RE.search(value):
        return "That doesn't look like a GitHub profile URL (github.com/...)."
    return None


def _profile_bundle() -> dict:
    doc = cv_service.active_cv(current_user.id)
    profile = cv_service.get_profile(current_user.id) if doc else None
    sendable = False
    blockers: list[str] = []
    if profile:
        from core.cv_profile import profile_is_sendable

        sendable, blockers = profile_is_sendable(profile)
    return {
        "doc": {"filename": doc.filename, "sizeBytes": doc.size_bytes} if doc else None,
        "profile": profile,
        "sendable": sendable,
        "blockers": [b[6:].strip() for b in blockers],
    }


@cv_bp.get("/profile")
@login_required
def profile():
    return jsonify(**_profile_bundle())


@cv_bp.post("/upload")
@login_required
@limiter.limit("20 per hour")
def upload():
    file = request.files.get("cv")
    if not file or not file.filename:
        return jsonify(error="Choose a CV file to upload."), 400

    data = file.read()
    try:
        cv_service.store_cv(
            current_user.id, file.filename, data,
            current_app.config["MAX_CONTENT_LENGTH"],
        )
    except cv_service.CvValidationError as exc:
        return jsonify(error=str(exc)), 400

    # Parse eagerly so the user immediately sees what we understood.
    cv_service.get_profile(current_user.id, force=True)
    return jsonify(**_profile_bundle()), 201


@cv_bp.post("/remove")
@login_required
def remove():
    if not cv_service.remove_cv(current_user.id):
        return jsonify(error="No CV to remove."), 404
    return jsonify(status="removed")


@cv_bp.post("/resolve")
@login_required
def resolve_choice():
    """
    Record which value to use for a contact field the CV states twice.

    Normally the value must be one of the options the CV itself offered — but
    a ``custom_value`` (e.g. neither email the CV lists still works) bypasses
    that check entirely, since the whole point is to let the user override
    what the CV says with something that actually works.
    """
    from core.cv_profile import CONTACT_FIELDS

    doc = cv_service.active_cv(current_user.id)
    if not doc:
        return jsonify(error="No CV uploaded"), 404

    data = request.get_json(silent=True) or {}
    field = (data.get("field") or "").strip()
    if field not in CONTACT_FIELDS:
        return jsonify(error="Unknown field"), 400

    custom_value = (data.get("custom_value") or "").strip()
    if custom_value:
        error = _validate_custom_contact(field, custom_value)
        if error:
            return jsonify(error=error), 400
        repo.save_cv_choice(current_user.id, doc.content_hash, field, custom_value)
        return jsonify(**_profile_bundle())

    value = (data.get("value") or "").strip()
    prof = cv_service.get_profile(current_user.id)
    allowed = (prof or {}).get("contact_choices", {}).get(field, {}).get("options", [])
    if value and value not in allowed:
        return jsonify(error="That value isn't one of the options found in your CV."), 400

    repo.save_cv_choice(current_user.id, doc.content_hash, field, value)
    return jsonify(**_profile_bundle())
