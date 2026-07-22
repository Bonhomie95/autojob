"""
Trigger a pipeline run and stream its progress.

``POST /runs/start`` enqueues a Celery task for the current user and returns the
run id. ``GET /runs/<id>/stream`` is a Server-Sent Events endpoint that relays
the worker's progress (via Redis pub/sub) to the browser. Each user can only
start and watch their own runs — the run is looked up scoped to the user.
"""

from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, stream_with_context
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..models import Run
from ..services import progress
from ..services import repository as repo

runs_bp = Blueprint("runs", __name__, url_prefix="/runs")


@runs_bp.post("/start")
@login_required
@limiter.limit("10 per hour")
def start():
    # Guard: one active run per user at a time.
    active = db.session.scalar(
        db.select(Run).where(Run.user_id == current_user.id, Run.status == "running")
    )
    if active:
        return jsonify(error="A run is already in progress", run_id=active.id), 409

    run_id = repo.start_run(current_user.id)

    from ..tasks import run_discovery_for_user

    run_discovery_for_user.delay(current_user.id, run_id)
    return jsonify(status="started", run_id=run_id)


@runs_bp.get("/<run_id>/stream")
@login_required
def stream(run_id: str):
    # Authorise: the run must belong to this user.
    run = db.session.scalar(
        db.select(Run).where(Run.user_id == current_user.id, Run.id == run_id)
    )
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
