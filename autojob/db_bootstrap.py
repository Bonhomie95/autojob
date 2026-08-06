"""
Safe database bootstrap for deploys.

Replaces a bare ``flask db upgrade`` at boot so it does the right thing against
any starting state:

1. **Alembic-managed** (``alembic_version`` present) — apply any new migrations.
   This is every normal redeploy; a no-op when already up to date.
2. **Unmanaged but non-empty** — tables exist with no ``alembic_version``. Two
   ways this happens migrating from the old top-level ``app.py``:
     * the *legacy single-user* schema it created directly (its ``jobs`` table
       has no ``user_id`` column), or
     * a *partially initialised* schema from an earlier migration attempt that
       created some SaaS tables (e.g. ``users``) but never recorded a version —
       which then fails every retry on ``relation ... already exists``.
   Neither is trustworthy and neither carries multi-tenant data worth keeping,
   so the schema is dropped and rebuilt cleanly. Set ``KEEP_LEGACY_DB=true`` to
   refuse and stop instead of dropping.
3. **Empty** — just build the SaaS schema.

This is what lets the first SaaS deploy succeed onto a database the single-user
app had already populated (or a half-finished earlier attempt left behind).
"""

from __future__ import annotations

import logging

from flask_migrate import upgrade as alembic_upgrade
from sqlalchemy import inspect, text

from .config import _bool
from .extensions import db

logger = logging.getLogger(__name__)


def _is_legacy_schema(inspector) -> bool:
    """A ``jobs`` table with no ``user_id`` column is the single-user schema."""
    if "jobs" not in inspector.get_table_names():
        return False
    cols = {c["name"] for c in inspector.get_columns("jobs")}
    return "user_id" not in cols


def _drop_all_tables(inspector) -> list[str]:
    """Drop every table in the database. CASCADE on Postgres so foreign keys
    between them don't force a drop order; FKs aren't enforced on SQLite."""
    names = inspector.get_table_names()
    if not names:
        return []
    is_pg = db.engine.dialect.name == "postgresql"
    with db.engine.begin() as conn:
        if not is_pg:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
        for table in names:
            suffix = " CASCADE" if is_pg else ""
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}"{suffix}'))
    return names


def bootstrap_database() -> str:
    """Bring the database to the current SaaS schema. Returns what it did."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if "alembic_version" in tables:
        alembic_upgrade()
        return "upgraded"

    if tables:
        kind = "legacy single-user" if _is_legacy_schema(inspector) \
            else "partially-initialised"
        if _bool("KEEP_LEGACY_DB", False):
            raise RuntimeError(
                f"Refusing to start: the database holds an unmanaged "
                f"({kind}) schema and KEEP_LEGACY_DB is set. Migrate or drop it "
                f"manually, or unset KEEP_LEGACY_DB to let the SaaS reset and "
                f"rebuild it (this drops all existing tables)."
            )
        logger.warning(
            "Unmanaged %s database detected (%d table(s), no alembic_version) — "
            "resetting and rebuilding the multi-tenant schema. Existing data is "
            "not carried over.",
            kind, len(tables),
        )
        dropped = _drop_all_tables(inspector)
        logger.warning("Dropped tables: %s", ", ".join(sorted(dropped)))
        alembic_upgrade()
        return "reset"

    alembic_upgrade()
    return "initialised"
