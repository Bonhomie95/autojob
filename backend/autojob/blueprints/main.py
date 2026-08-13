"""
Dashboard overview data.

The landing page is static marketing content the SPA renders client-side with
no backend call — nothing to serve here for it. This blueprint is just the
one JSON endpoint the authenticated dashboard/overview page needs.
"""

from __future__ import annotations

from flask import Blueprint, jsonify
from flask_login import current_user, login_required

from ..services import cv_service
from ..services import repository as repo

main_bp = Blueprint("main", __name__, url_prefix="/api")


def _serialize_settings(s) -> dict:
    return {
        "autoSend": s.auto_send,
        "followUpEnabled": s.follow_up_enabled,
        "minMatchScore": s.min_match_score,
    }


@main_bp.get("/dashboard")
@login_required
def dashboard():
    settings = repo.get_or_create_settings(current_user.id)
    stats = repo.get_stats(current_user.id)
    awaiting = repo.jobs_with_replies_awaiting_action(current_user.id)
    has_cv = cv_service.active_cv(current_user.id) is not None
    active_run = repo.has_active_run(current_user.id)
    return jsonify(
        settings=_serialize_settings(settings),
        stats=stats,
        awaiting=[{"id": j.id, "title": j.title, "company": j.company, "hrEmail": j.hr_email}
                  for j in awaiting],
        hasCv=has_cv,
        activeRunId=active_run.id if active_run else None,
    )
