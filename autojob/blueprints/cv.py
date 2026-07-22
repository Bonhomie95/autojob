"""
CV upload and the parsed-profile view — the heart of "just upload your CV".

Everything is scoped to the signed-in user: uploads are validated (extension +
magic bytes + size), stored under the user's prefix, parsed, and surfaced as a
profile with any ambiguous contact fields flagged for the user to resolve.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import limiter
from ..services import cv_service
from ..services import repository as repo

logger = logging.getLogger(__name__)

cv_bp = Blueprint("cv", __name__, url_prefix="/cv")


@cv_bp.get("/")
@login_required
def profile_page():
    doc = cv_service.active_cv(current_user.id)
    profile = cv_service.get_profile(current_user.id) if doc else None
    sendable = False
    blockers: list[str] = []
    if profile:
        from core.cv_profile import profile_is_sendable

        sendable, blockers = profile_is_sendable(profile)
    return render_template(
        "dashboard/cv.html",
        doc=doc, profile=profile, sendable=sendable,
        blockers=[b[6:].strip() for b in blockers],
    )


@cv_bp.post("/upload")
@login_required
@limiter.limit("20 per hour")
def upload():
    file = request.files.get("cv")
    if not file or not file.filename:
        flash("Choose a CV file to upload.", "error")
        return redirect(url_for("cv.profile_page"))

    data = file.read()
    try:
        cv_service.store_cv(
            current_user.id, file.filename, data,
            current_app.config["MAX_CONTENT_LENGTH"],
        )
    except cv_service.CvValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("cv.profile_page"))

    # Parse eagerly so the user immediately sees what we understood.
    cv_service.get_profile(current_user.id, force=True)
    flash("CV uploaded and parsed. Review the details below.", "success")
    return redirect(url_for("cv.profile_page"))


@cv_bp.post("/resolve")
@login_required
def resolve_choice():
    """Record which value to use for a contact field the CV states twice."""
    from core.cv_profile import CONTACT_FIELDS

    doc = cv_service.active_cv(current_user.id)
    if not doc:
        return jsonify(error="No CV uploaded"), 404

    field = (request.form.get("field") or "").strip()
    value = (request.form.get("value") or "").strip()
    if field not in CONTACT_FIELDS:
        return jsonify(error="Unknown field"), 400

    profile = cv_service.get_profile(current_user.id)
    allowed = (profile or {}).get("contact_choices", {}).get(field, {}).get("options", [])
    if value and value not in allowed:
        flash("That value isn't one of the options found in your CV.", "error")
    else:
        repo.save_cv_choice(current_user.id, doc.content_hash, field, value)
        flash("Saved.", "success")
    return redirect(url_for("cv.profile_page"))
