"""
Health and readiness endpoints.

``/healthz``   — liveness: the process is up and serving. Never touches
                dependencies, so an orchestrator won't kill a pod just because
                the database is briefly unreachable.
``/readyz``    — readiness: dependencies (database) are reachable, so it is
                safe to route traffic here.
``/``          — minimal landing response until the UI is migrated (Phase 3+).
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify
from sqlalchemy import text

from .. import __version__
from ..extensions import db, limiter

health_bp = Blueprint("health", __name__)


@health_bp.get("/metrics")
@limiter.exempt
def metrics():
    from ..observability import render_metrics

    return Response(render_metrics(), content_type="text/plain; version=0.0.4")


@health_bp.get("/healthz")
@limiter.exempt
def healthz():
    return jsonify(status="ok", version=__version__)


@health_bp.get("/readyz")
@limiter.exempt
def readyz():
    checks = {"database": "ok"}
    status_code = 200
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - report any failure as not-ready
        checks["database"] = f"error: {exc.__class__.__name__}"
        status_code = 503
    return jsonify(status="ok" if status_code == 200 else "degraded", checks=checks), status_code
