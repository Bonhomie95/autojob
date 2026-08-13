"""
Jobs API: list discovered jobs, view one, download its documents, send manually.

Everything is scoped to the signed-in user via the repository. Generated
documents live in the storage abstraction (like CVs), keyed
``output/<user_id>/<job_id>/<filename>`` — a job's ``output_dir`` is validated
to sit under this user's own prefix before anything is listed or served, so no
key can address another tenant's files.
"""

from __future__ import annotations

import logging
import mimetypes
import threading
from datetime import UTC, datetime

from flask import Blueprint, Response, current_app, jsonify, request
from flask_login import current_user, login_required

from ..extensions import limiter
from ..services import mailer, storage
from ..services import repository as repo
from ..services.runtime_config import build_runtime_config

logger = logging.getLogger(__name__)

jobs_bp = Blueprint("jobs", __name__, url_prefix="/api/jobs")


def _run_followups_in_thread(app, user_id: str) -> None:
    """Execute a follow-up cycle inside its own app context (background thread)."""
    from ..tasks import run_followups

    with app.app_context():
        try:
            run_followups(user_id)
        except Exception:  # noqa: BLE001
            logger.exception("Background follow-up cycle failed for user %s", user_id)


def _serialize_job(job, files: list[str] | None = None) -> dict:
    d = {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "description": job.description,
        "salary": job.salary,
        "source": job.source,
        "score": job.score,
        "hrName": job.hr_name,
        "hrEmail": job.hr_email,
        "hrTitle": job.hr_title,
        "applicationEmail": job.application_email,
        "applicationUrl": job.application_url,
        "contactNotes": job.contact_notes,
        "status": job.status,
        "emailStatus": job.email_status,
        "emailError": job.email_error,
        "followUpStatus": job.follow_up_status,
        "replyDetected": job.reply_detected,
        "bounced": job.bounced,
        "portalStatus": job.portal_status,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "hasDocuments": bool(job.output_dir),
    }
    if files is not None:
        d["files"] = files
    return d


def _owns_output(job) -> bool:
    """A job's output_dir must be this user's own storage prefix — defence
    against ever addressing another tenant's generated documents."""
    return bool(job.output_dir) and job.output_dir.startswith(f"output/{current_user.id}/")


@jobs_bp.get("/")
@login_required
def list_jobs():
    status = request.args.get("status", "all")
    jobs = repo.get_jobs(current_user.id, limit=500, status=status)
    awaiting = repo.jobs_with_replies_awaiting_action(current_user.id)
    return jsonify(
        jobs=[_serialize_job(j) for j in jobs],
        awaiting=[_serialize_job(j) for j in awaiting],
        status=status,
    )


@jobs_bp.get("/<job_id>")
@login_required
def detail(job_id: str):
    job = repo.get_job(current_user.id, job_id)
    if not job:
        return jsonify(error="not_found"), 404
    files = []
    if _owns_output(job):
        files = sorted(k.rsplit("/", 1)[-1] for k in storage.get_storage().list_keys(job.output_dir))
    return jsonify(job=_serialize_job(job, files=files))


@jobs_bp.get("/<job_id>/file/<filename>")
@login_required
def download(job_id: str, filename: str):
    job = repo.get_job(current_user.id, job_id)
    if not job or not _owns_output(job):
        return jsonify(error="not_found"), 404
    # filename is a single path SEGMENT (no "/" route converter used above),
    # but guard explicitly anyway — a storage key is built by concatenation,
    # not resolved through Werkzeug's own traversal-safe helpers.
    if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
        return jsonify(error="bad_request"), 400
    try:
        data = storage.get_storage().get(f"{job.output_dir}/{filename}")
    except Exception:  # noqa: BLE001
        return jsonify(error="not_found"), 404
    mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        data, mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@jobs_bp.post("/<job_id>/send")
@login_required
@limiter.limit("60 per hour")
def send(job_id: str):
    job = repo.get_job(current_user.id, job_id)
    if not job:
        return jsonify(error="not_found"), 404
    if not job.output_dir:
        return jsonify(error="No document package for this job yet — run discovery first."), 400

    cfg = build_runtime_config(current_user.id)
    to_email = job.hr_email or job.application_email
    result = mailer.send_application(cfg, job, to_email, skip_dedup=True)
    if result.ok:
        repo.update_job(current_user.id, job_id, email_status="sent",
                        email_sent_at=datetime.now(UTC), email_message_id=result.message_id,
                        email_error="", status="applied")
        return jsonify(status="sent", message=result.message)

    repo.update_job(current_user.id, job_id, email_status="failed", email_error=result.message)
    return jsonify(error=result.message), 400


@jobs_bp.post("/followups/run")
@login_required
@limiter.limit("10 per hour")
def run_followups():
    # Guard: one follow-up cycle per user at a time (atomic — see repo docs),
    # so a double-click or a race with the daily scheduled cycle can't send
    # duplicate follow-ups.
    if not repo.claim_followup_lock(current_user.id):
        return jsonify(error="Already checking for replies — try again in a moment."), 409

    if current_app.config.get("RUN_VIA_CELERY", False):
        from ..tasks import run_followups_for_user

        run_followups_for_user.delay(current_user.id)
    else:
        # Same reasoning as runs.start: CELERY_TASK_ALWAYS_EAGER defaults to
        # true, so without this a full IMAP scan + sends would run inline in
        # this request instead of in the background.
        app = current_app._get_current_object()
        threading.Thread(
            target=_run_followups_in_thread,
            args=(app, current_user.id),
            daemon=True,
        ).start()

    return jsonify(status="started")


@jobs_bp.post("/clear")
@login_required
@limiter.limit("10 per hour")
def clear():
    """
    Wipe every discovered job so previously-seen postings (deduped by URL)
    can resurface on the next run. Blocked while a run is active — that run
    is actively writing jobs right now, so clearing under it would race.
    """
    if repo.has_active_run(current_user.id):
        return jsonify(error="Stop the current run before clearing jobs."), 409

    count = repo.clear_jobs(current_user.id)
    return jsonify(status="cleared", count=count)
