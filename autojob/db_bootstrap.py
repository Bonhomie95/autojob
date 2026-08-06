"""
Safe database bootstrap for deploys.

Replaces a bare ``flask db upgrade`` at boot so it does the right thing against
three possible starting states:

1. **Already Alembic-managed** (``alembic_version`` present) — just apply any new
   migrations. This is every normal redeploy; it's a no-op when up to date.
2. **A legacy single-user database** — tables like ``jobs``/``corpus_meta`` left
   behind by the old top-level ``app.py`` (which created them directly, with no
   Alembic tracking). The legacy ``jobs`` table has no ``user_id`` column, which
   is an unambiguous fingerprint. That data is not multi-tenant and is abandoned
   in the migration to the SaaS, so those tables are dropped and the SaaS schema
   is built fresh. Set ``KEEP_LEGACY_DB=true`` to refuse instead of dropping.
3. **Empty** — just build the SaaS schema.

This is what makes the first SaaS deploy onto a database the single-user app had
already populated succeed, instead of failing on ``relation already exists``.
"""

from __future__ import annotations

import logging

from flask_migrate import upgrade as alembic_upgrade
from sqlalchemy import inspect, text

from .config import _bool
from .extensions import db

logger = logging.getLogger(__name__)

# Tables the legacy single-user app created directly (no Alembic). Dropped only
# when a legacy schema is positively identified (see below).
_LEGACY_TABLES = [
    "app_settings", "jobs", "runs", "cv_profiles", "cv_choices",
    "token_df", "corpus_meta",
]


def _is_legacy_schema(inspector) -> bool:
    """A ``jobs`` table with no ``user_id`` column is the single-user schema."""
    if "jobs" not in inspector.get_table_names():
        return False
    cols = {c["name"] for c in inspector.get_columns("jobs")}
    return "user_id" not in cols


def _drop_legacy_tables() -> None:
    cascade = " CASCADE" if db.engine.dialect.name == "postgresql" else ""
    with db.engine.begin() as conn:
        for table in _LEGACY_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}"{cascade}'))


def bootstrap_database() -> str:
    """Bring the database to the current SaaS schema. Returns what it did."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if "alembic_version" in tables:
        alembic_upgrade()
        return "upgraded"

    if _is_legacy_schema(inspector):
        if _bool("KEEP_LEGACY_DB", False):
            raise RuntimeError(
                "Refusing to start: the database holds the legacy single-user "
                "schema and KEEP_LEGACY_DB is set. Migrate or drop it manually, "
                "or unset KEEP_LEGACY_DB to let the SaaS rebuild it (this drops "
                f"the old tables: {', '.join(_LEGACY_TABLES)})."
            )
        logger.warning(
            "Legacy single-user database detected — dropping its tables (%s) and "
            "rebuilding the multi-tenant schema. Old single-user data is not "
            "carried over.",
            ", ".join(_LEGACY_TABLES),
        )
        _drop_legacy_tables()

    alembic_upgrade()
    return "initialised"
