"""
Trigger a pipeline run and stream its progress.

``POST /runs/start`` starts a discovery run for the current user and returns the
run id. ``GET /runs/<id>/stream`` is a Server-Sent Events endpoint that relays
the run's progress to the browser. Each user can only start and watch their own
runs — the run is looked up scoped to the user.

By default the run executes in a background thread inside this web process and
progress streams over the in-process bus, so AutoJob deploys as a single web
service with no Redis and no separate Celery worker. Set ``RUN_VIA_CELERY=true``
(with a real broker + worker) to enqueue the run instead.
"""

from __future__ import annotations

import json
import logging
import threading

from flask import Blueprint, Response, current_app, jsonify, stream_with_context
from flask_login import current_user, login_required

from ..extensions import limiter
from ..services import progress
from ..services import repository as repo
from ..services.runtime_config import build_runtime_config, preflight_issues

logger = logging.getLogger(__name__)

runs_bp = Blueprint("runs", __name__, url_prefix="/api/runs")


def _run_in_thread(app, user_id: str, run_id: str) -> None:
    """Execute a discovery run inside its own app context (background thread)."""
    from ..tasks import run_discovery

    with app.app_context():
        try:
            run_discovery(user_id, run_id)
        except Exception:  # noqa: BLE001
            logger.exception("Background run %s failed", run_id)


@runs_bp.post("/start")
@login_required
@limiter.limit("10 per hour")
def start():
    # One active run per user at a time — enforced atomically by a DB index
    # (repo.start_run), not by a check-then-insert two requests could both
    # pass. Checked before the credentials guard below: if a run's already
    # going, that's the more relevant thing to tell the user about.
    try:
        run_id = repo.start_run(current_user.id)
    except repo.RunAlreadyActive:
        active = repo.has_active_run(current_user.id)
        return jsonify(
            error="A run is already in progress", runId=active.id if active else None
        ), 409

    # Guard: don't actually launch a run that's missing credentials it needs
    # to finish properly (no AI key anywhere, or auto-send on with no SMTP
    # configured). Release the run slot claimed above if so.
    cfg = build_runtime_config(current_user.id)
    issues = preflight_issues(cfg)
    if issues:
        repo.finish_run(current_user.id, run_id, 0, 0, 0, status="failed")
        return jsonify(error=" ".join(issues)), 400

    if current_app.config.get("RUN_VIA_CELERY", False):
        from ..tasks import run_discovery_for_user

        run_discovery_for_user.delay(current_user.id, run_id)
    else:
        app = current_app._get_current_object()
        threading.Thread(
            target=_run_in_thread,
            args=(app, current_user.id, run_id),
            daemon=True,
        ).start()
    return jsonify(status="started", runId=run_id)


@runs_bp.post("/<run_id>/cancel")
@login_required
def cancel(run_id: str):
    """
    Ask a running pipeline to stop. Cooperative, not instant: the run checks
    for this between scrapers/jobs, so a job that's mid-write finishes that one
    step before stopping rather than being left half-updated.
    """
    ok = repo.request_cancel_run(current_user.id, run_id)
    if not ok:
        return jsonify(error="No active run with that id"), 404
    return jsonify(status="stopping")


@runs_bp.get("/<run_id>/stream")
@login_required
def stream(run_id: str):
    # Authorise: the run must belong to this user.
    run = repo.get_run(current_user.id, run_id)
    if not run:
        return jsonify(error="not_found"), 404

    @stream_with_context
    def generate():
        for payload in progress.subscribe(run_id):
            yield f"data: {json.dumps(payload)}\n\n"

    return Response(
        generate(),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
