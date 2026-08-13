"""
Flask extension singletons, instantiated but unbound.

Each extension is created here once and bound to the application inside
``create_app()`` via ``init_app``. Keeping them in their own module avoids
circular imports: models import ``db`` from here, and the app factory imports
both.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from flask import Flask, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_wtf import CSRFProtect


class Mongo:
    """
    Thin holder for the pymongo (or mongomock, in tests) client + database.

    There's no ORM and no migrations: MongoDB is schemaless, so ``init_app``
    just opens the connection and makes sure the handful of indexes the app
    relies on (uniqueness constraints, lookup speed) exist. That replaces what
    Alembic used to do.
    """

    def __init__(self) -> None:
        self.client: Any = None
        self.conn: Any = None  # the pymongo/mongomock Database

    def init_app(self, app: Flask) -> None:
        uri = app.config["MONGODB_URI"]
        db_name = app.config.get("MONGODB_DB_NAME", "autojob")

        if uri.startswith("mongomock://"):
            # Test sentinel: pymongo's real URI parser rejects this scheme, so
            # build a bare mongomock client and pick the db by name directly
            # rather than trying to get it to parse a fake URI.
            import mongomock

            self.client = mongomock.MongoClient()
            self.conn = self.client[db_name]
        else:
            import certifi
            from pymongo import MongoClient

            # tz_aware + tzinfo=UTC so datetimes read back out are aware and
            # comparable with the datetime.now(UTC) values the app writes —
            # pymongo otherwise returns naive UTC datetimes.
            # tlsCAFile=certifi.where(): some environments (notably stock
            # macOS Python) don't have a working system CA bundle, which
            # makes TLS to Atlas fail with CERTIFICATE_VERIFY_FAILED —
            # pointing at certifi's bundle explicitly sidesteps that.
            self.client = MongoClient(
                uri, tz_aware=True, tzinfo=UTC, tlsCAFile=certifi.where()
            )
            self.conn = self.client.get_default_database(default=db_name)

        app.extensions["mongo"] = self

        from .db_bootstrap import ensure_indexes

        ensure_indexes(self.conn)

    def ping(self) -> None:
        """Raise if the database is unreachable — used by the /readyz probe."""
        self.client.admin.command("ping")


db = Mongo()

# Authentication / session management. This is a JSON API — the React SPA
# owns all rendering, so an unauthenticated request gets a 401 to act on
# (redirect client-side), never a server-side redirect to an HTML login page.
login_manager = LoginManager()
login_manager.session_protection = "strong"


@login_manager.unauthorized_handler
def _unauthorized():
    return jsonify(error="unauthorized"), 401


# CSRF protection for all state-changing requests. The SPA fetches a token
# from GET /api/auth/csrf once and sends it back via the X-CSRFToken header —
# Flask-WTF's CSRFProtect checks that header on its own, independent of forms.
csrf = CSRFProtect()

# Per-IP / per-user rate limiting (Phase 3)
limiter = Limiter(key_func=get_remote_address)
