"""
Public landing page and the authenticated dashboard shell.

The landing page is reachable by anyone. The dashboard requires login and is
where the full job-application UI mounts in later phases; for now it renders the
user's profile snapshot and settings so the auth + tenancy wiring is visible
end to end.
"""

from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..services import repository as repo

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def landing():
    return render_template("landing.html")


@main_bp.get("/app")
@login_required
def dashboard():
    settings = repo.get_or_create_settings(current_user.id)
    stats = repo.get_stats(current_user.id)
    awaiting = repo.jobs_with_replies_awaiting_action(current_user.id)
    return render_template(
        "dashboard/index.html", settings=settings, stats=stats, awaiting=awaiting
    )
