"""
AutoJob SaaS — Flask application factory.

This is a pure JSON API — every blueprint is mounted under ``/api``. The
frontend is a separate React/Vite SPA deployed on its own (e.g. Vercel) and
talks to this API cross-origin, so CORS and cross-site cookies are configured
here (see ``_register_cors`` and ``FRONTEND_ORIGIN``/``SESSION_COOKIE_SAMESITE``
in config.py). Flask wires configuration, extensions, blueprints, security
headers, logging, and error handlers together, but owns no request logic
itself — that lives in blueprints.

Usage::

    from autojob import create_app
    app = create_app()            # picks config from $APP_ENV
"""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify

from .config import get_config
from .extensions import csrf, db, limiter, login_manager
from .logging_config import configure_logging

__version__ = "0.1.0"


def create_app(config_object=None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    cfg = config_object or get_config()
    app.config.from_object(cfg)

    configure_logging(
        level=app.config.get("LOG_LEVEL", "INFO"),
        as_json=app.config.get("LOG_JSON", False),
    )
    logging.getLogger(__name__).info(
        "Starting AutoJob SaaS v%s (env=%s)",
        __version__,
        app.config.get("APP_ENV"),
    )

    _init_extensions(app)
    _register_cors(app)
    _register_security_headers(app)
    from .observability import init_observability

    init_observability(app)
    _register_blueprints(app)
    _register_root(app)
    _register_error_handlers(app)
    _init_sentry(app)

    return app


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)

    # Enforcement is toggled by ``WTF_CSRF_ENABLED`` — the testing config sets
    # it False so the test client can post without a token. In real use the
    # SPA fetches one from GET /api/auth/csrf and sends it back via header.
    csrf.init_app(app)


def _register_cors(app: Flask) -> None:
    """Allow the deployed frontend to call this API cross-origin, with cookies."""
    origins = [o.strip() for o in app.config.get("FRONTEND_ORIGIN", "").split(",") if o.strip()]
    if not origins:
        return
    from flask_cors import CORS

    CORS(
        app,
        resources={r"/api/*": {"origins": origins}},
        supports_credentials=True,
    )


def _register_security_headers(app: Flask) -> None:
    @app.after_request
    def set_secure_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


def _register_blueprints(app: Flask) -> None:
    from .blueprints.auth import auth_bp
    from .blueprints.cv import cv_bp
    from .blueprints.health import health_bp
    from .blueprints.jobs import jobs_bp
    from .blueprints.main import main_bp
    from .blueprints.runs import runs_bp
    from .blueprints.settings import settings_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(cv_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(jobs_bp)


def _register_root(app: Flask) -> None:
    """A bare, friendly response for anyone hitting the API's bare domain."""

    @app.get("/")
    def root():
        return jsonify(service="autojob-api", version=__version__)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(err):
        return jsonify(error="not_found"), 404

    @app.errorhandler(405)
    def method_not_allowed(err):
        return jsonify(error="method_not_allowed"), 405

    @app.errorhandler(429)
    def rate_limited(err):
        return jsonify(error="rate_limited", detail=str(err.description)), 429

    @app.errorhandler(500)
    def server_error(err):
        logging.getLogger(__name__).exception("Unhandled server error")
        return jsonify(error="internal_server_error"), 500


def _init_sentry(app: Flask) -> None:
    dsn = app.config.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            environment=app.config.get("APP_ENV"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        )
        logging.getLogger(__name__).info("Sentry error tracking enabled")
    except ImportError:
        logging.getLogger(__name__).warning(
            "SENTRY_DSN set but sentry-sdk not installed — skipping"
        )
