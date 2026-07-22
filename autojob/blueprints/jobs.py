"""
Jobs UI: list discovered jobs, view one, download its documents, send manually.

Everything is scoped to the signed-in user via the repository. Document
downloads are confined to the user's own output directory — a job's
``output_dir`` is validated to sit under this user's storage root before any
file is served, so no path can escape into another tenant's files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import limiter
from ..services import mailer
from ..services import repository as repo
from ..services.runtime_config import build_runtime_config

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")


@jobs_bp.get("/")
@login_required
def list_jobs():
    status = request.args.get("status", "all")
    jobs = repo.get_jobs(current_user.id, limit=500, status=status)
    awaiting = repo.jobs_with_replies_awaiting_action(current_user.id)
    return render_template("dashboard/jobs.html", jobs=jobs, status=status, awaiting=awaiting)


@jobs_bp.get("/<job_id>")
@login_required
def detail(job_id: str):
    job = repo.get_job(current_user.id, job_id)
    if not job:
        abort(404)
    files = []
    if job.output_dir and Path(job.output_dir).exists():
        files = sorted(p.name for p in Path(job.output_dir).iterdir() if p.is_file())
    return render_template("dashboard/job_detail.html", job=job, files=files)


def _user_output_root() -> Path:
    root = Path(current_app.config.get("STORAGE_LOCAL_ROOT", "storage"))
    return (root / "output" / current_user.id).resolve()


@jobs_bp.get("/<job_id>/file/<path:filename>")
@login_required
def download(job_id: str, filename: str):
    job = repo.get_job(current_user.id, job_id)
    if not job or not job.output_dir:
        abort(404)
    folder = Path(job.output_dir).resolve()
    # Confine to this user's output tree — defence against path traversal.
    if not str(folder).startswith(str(_user_output_root())):
        abort(403)
    return send_from_directory(str(folder), filename, as_attachment=True)


@jobs_bp.post("/<job_id>/send")
@login_required
@limiter.limit("60 per hour")
def send(job_id: str):
    job = repo.get_job(current_user.id, job_id)
    if not job:
        abort(404)
    if not job.output_dir:
        flash("No document package for this job yet — run discovery first.", "error")
        return redirect(url_for("jobs.detail", job_id=job_id))

    cfg = build_runtime_config(current_user.id)
    to_email = job.hr_email or job.application_email
    result = mailer.send_application(cfg, job, to_email, skip_dedup=True)
    if result.ok:
        repo.update_job(current_user.id, job_id, email_status="sent",
                        email_sent_at=datetime.now(UTC), email_error="", status="applied")
        flash(result.message, "success")
    else:
        repo.update_job(current_user.id, job_id, email_status="failed", email_error=result.message)
        flash(result.message, "error")
    return redirect(url_for("jobs.detail", job_id=job_id))


@jobs_bp.post("/followups/run")
@login_required
@limiter.limit("10 per hour")
def run_followups():
    from ..tasks import run_followups_for_user

    run_followups_for_user.delay(current_user.id)
    flash("Checking your inbox for replies and following up with non-responders…", "success")
    return redirect(url_for("jobs.list_jobs"))
