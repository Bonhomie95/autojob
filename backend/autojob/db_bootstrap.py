"""
Idempotent Mongo index setup.

MongoDB is schemaless — there's no Alembic-style migration to run. The only
thing that needs to exist ahead of traffic is the handful of indexes the app
relies on for uniqueness (e.g. one email per user, one job per user+URL) and
lookup speed. ``ensure_indexes`` is called automatically by
``Mongo.init_app`` (see extensions.py) every time the app starts, so it's
always up to date; ``bootstrap_database`` exists only for the explicit
``manage.py init-db`` CLI command.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def ensure_indexes(conn) -> None:
    conn.users.create_index("email", unique=True)

    conn.user_settings.create_index("user_id", unique=True)

    conn.user_credentials.create_index(
        [("user_id", 1), ("provider", 1), ("name", 1)], unique=True
    )

    conn.jobs.create_index([("user_id", 1), ("url", 1)], unique=True)
    conn.jobs.create_index("user_id")
    conn.jobs.create_index("source")
    conn.jobs.create_index("hr_email")
    conn.jobs.create_index("status")
    conn.jobs.create_index("email_message_id")
    conn.jobs.create_index("created_at")

    conn.runs.create_index("user_id")
    # At most one 'running' run per user, enforced atomically by the DB (not by
    # a check-then-insert in application code, which two near-simultaneous
    # requests could both pass) — see repository.start_run. Named explicitly:
    # its auto-generated name would collide with the plain index above.
    conn.runs.create_index(
        [("user_id", 1)], unique=True, partialFilterExpression={"status": "running"},
        name="uniq_one_running_run_per_user",
    )

    conn.cv_documents.create_index("user_id")
    conn.cv_documents.create_index("content_hash")

    conn.cv_profiles.create_index([("user_id", 1), ("content_hash", 1)], unique=True)

    conn.cv_choices.create_index(
        [("user_id", 1), ("content_hash", 1), ("field", 1)], unique=True
    )

    # Follow-up-cycle lock: a doc's mere existence (by _id=user_id) means a
    # cycle is in progress; released on completion. The TTL is a crash-safety
    # net only, not the normal release path — see repository.claim/release_
    # followup_lock and tasks.run_followups.
    conn.followup_locks.create_index("claimed_at", expireAfterSeconds=1800)


def bootstrap_database() -> str:
    from .extensions import db

    ensure_indexes(db.conn)
    return "indexes ensured"
