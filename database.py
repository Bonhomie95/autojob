"""
database.py — data layer for the single-user AutoJob app.

Works on two engines from the same code:

  * **Postgres** — used automatically when DATABASE_URL is set (the recommended
    setup on Render). Set e.g. DATABASE_URL=postgresql://user:pass@host/dbname
  * **SQLite** — the zero-config local fallback (DB_PATH, default jobhunter.db).

To stay dialect-neutral, all date math is done with ISO-8601 string
comparisons (timestamps are stored as `datetime.utcnow().isoformat()`, which
sorts lexicographically) and `substr(col, 1, 10)` for "same day" checks —
so no `julianday()` / `date('now')` (SQLite-only) appears in any query.
"""

import os
import hashlib
import json
from datetime import datetime, timedelta
from config import config

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.lower().startswith(("postgres://", "postgresql://"))

if IS_PG:
    import psycopg2
    import psycopg2.extras

    _PG_DSN = DATABASE_URL
    if _PG_DSN.startswith("postgres://"):
        # SQLAlchemy-style scheme that psycopg2 doesn't accept
        _PG_DSN = "postgresql://" + _PG_DSN[len("postgres://") :]

def _q(sql: str) -> str:
    """
    Translate the one SQLite param style used in this file (positional ``?``)
    to psycopg2's ``%s``. We deliberately avoid ``:name`` params so this never
    has to reason about colons inside string literals or ``::type`` casts.
    """
    return sql.replace("?", "%s") if IS_PG else sql


class _Conn:
    """
    Thin connection wrapper so the rest of the module can keep calling
    `conn.execute(sql, params)` regardless of engine, with rows that support
    both positional (`row[0]`) and name (`row["col"]`) access, and that
    commit + close on `with` exit.
    """

    def __init__(self):
        if IS_PG:
            self._c = psycopg2.connect(_PG_DSN)
        else:
            import sqlite3

            self._c = sqlite3.connect(config.DB_PATH)
            self._c.row_factory = sqlite3.Row

    def _cursor(self):
        if IS_PG:
            return self._c.cursor(cursor_factory=psycopg2.extras.DictCursor)
        return self._c.cursor()

    def execute(self, sql, params=()):
        cur = self._cursor()
        cur.execute(_q(sql), params)
        return cur

    def executemany(self, sql, seq):
        cur = self._cursor()
        cur.executemany(_q(sql), seq)
        return cur

    def commit(self):
        self._c.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._c.commit()
            else:
                self._c.rollback()
        finally:
            self._c.close()


def get_conn() -> _Conn:
    return _Conn()


# ──────────────────────────────────────────────────────────
# Time helpers (dialect-neutral)
# ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _cutoff_iso(days: float) -> str:
    """ISO timestamp `days` in the past — the boundary for age comparisons."""
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    title             TEXT,
    company           TEXT,
    location          TEXT,
    url               TEXT UNIQUE,
    description       TEXT,
    salary            TEXT,
    posted_date       TEXT,
    source            TEXT,
    score             INTEGER DEFAULT 0,
    hr_name           TEXT,
    hr_email          TEXT,
    hr_title          TEXT,
    application_email TEXT,
    application_url   TEXT,
    contact_notes     TEXT,
    status            TEXT DEFAULT 'pending',
    output_dir        TEXT,
    email_status      TEXT DEFAULT 'not_sent',
    email_sent_at     TEXT,
    email_error       TEXT,
    follow_up_sent_at TEXT,
    follow_up_status  TEXT DEFAULT 'pending',
    reply_detected    INTEGER DEFAULT 0,
    portal_status     TEXT DEFAULT 'pending',
    portal_submitted_at TEXT,
    portal_error      TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT,
    finished_at    TEXT,
    jobs_found     INTEGER DEFAULT 0,
    jobs_scored    INTEGER DEFAULT 0,
    docs_generated INTEGER DEFAULT 0,
    emails_sent    INTEGER DEFAULT 0,
    follow_ups_sent INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS cv_profiles (
    cv_hash    TEXT PRIMARY KEY,
    filename   TEXT,
    profile    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cv_choices (
    cv_hash    TEXT,
    field      TEXT,
    value      TEXT,
    chosen_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (cv_hash, field)
);
CREATE TABLE IF NOT EXISTS token_df (
    token TEXT PRIMARY KEY,
    df    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS corpus_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    title             TEXT,
    company           TEXT,
    location          TEXT,
    url               TEXT UNIQUE,
    description       TEXT,
    salary            TEXT,
    posted_date       TEXT,
    source            TEXT,
    score             INTEGER DEFAULT 0,
    hr_name           TEXT,
    hr_email          TEXT,
    hr_title          TEXT,
    application_email TEXT,
    application_url   TEXT,
    contact_notes     TEXT,
    status            TEXT DEFAULT 'pending',
    output_dir        TEXT,
    email_status      TEXT DEFAULT 'not_sent',
    email_sent_at     TEXT,
    email_error       TEXT,
    follow_up_sent_at TEXT,
    follow_up_status  TEXT DEFAULT 'pending',
    reply_detected    INTEGER DEFAULT 0,
    portal_status     TEXT DEFAULT 'pending',
    portal_submitted_at TEXT,
    portal_error      TEXT,
    created_at        TEXT DEFAULT (now()::text)
);
CREATE TABLE IF NOT EXISTS runs (
    id             SERIAL PRIMARY KEY,
    started_at     TEXT,
    finished_at    TEXT,
    jobs_found     INTEGER DEFAULT 0,
    jobs_scored    INTEGER DEFAULT 0,
    docs_generated INTEGER DEFAULT 0,
    emails_sent    INTEGER DEFAULT 0,
    follow_ups_sent INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS cv_profiles (
    cv_hash    TEXT PRIMARY KEY,
    filename   TEXT,
    profile    TEXT,
    created_at TEXT DEFAULT (now()::text)
);
CREATE TABLE IF NOT EXISTS cv_choices (
    cv_hash    TEXT,
    field      TEXT,
    value      TEXT,
    chosen_at  TEXT DEFAULT (now()::text),
    PRIMARY KEY (cv_hash, field)
);
CREATE TABLE IF NOT EXISTS token_df (
    token TEXT PRIMARY KEY,
    df    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS corpus_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (now()::text)
);
"""

# Columns added after the original schema shipped, kept for DBs created earlier.
_MIGRATE_JOBS = [
    ("email_status", "TEXT DEFAULT 'not_sent'"),
    ("email_sent_at", "TEXT"),
    ("email_error", "TEXT"),
    ("follow_up_sent_at", "TEXT"),
    ("follow_up_status", "TEXT DEFAULT 'pending'"),
    ("reply_detected", "INTEGER DEFAULT 0"),
    ("portal_status", "TEXT DEFAULT 'pending'"),
    ("portal_submitted_at", "TEXT"),
    ("portal_error", "TEXT"),
]
_MIGRATE_RUNS = [
    ("emails_sent", "INTEGER DEFAULT 0"),
    ("follow_ups_sent", "INTEGER DEFAULT 0"),
]

_DID_INIT = False


def init_db(force: bool = False):
    global _DID_INIT
    if _DID_INIT and not force:
        return

    with get_conn() as conn:
        if IS_PG:
            # DDL is dialect-specific and has no bind params — run it raw so the
            # param translator never touches ``::text`` casts etc.
            cur = conn._c.cursor()
            cur.execute(_PG_SCHEMA)
            # Postgres can add-if-missing directly — no introspection needed.
            for col, defn in _MIGRATE_JOBS:
                cur.execute(f"ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {col} {defn}")
            for col, defn in _MIGRATE_RUNS:
                cur.execute(f"ALTER TABLE runs ADD COLUMN IF NOT EXISTS {col} {defn}")
        else:
            conn._c.executescript(_SQLITE_SCHEMA)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            for col, defn in _MIGRATE_JOBS:
                if col not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")
            existing_runs = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
            for col, defn in _MIGRATE_RUNS:
                if col not in existing_runs:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {defn}")
        conn.commit()
    _DID_INIT = True
    # Now that app_settings exists, refresh config so DB-stored settings win
    # over env/defaults for the rest of this process.
    try:
        from config import config as _cfg

        _cfg.reload()
    except Exception:
        pass


def make_job_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


# ──────────────────────────────────────────────────────────
# CV profile cache
# ──────────────────────────────────────────────────────────


def load_cv_profile(cv_hash: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT profile FROM cv_profiles WHERE cv_hash = ?", (cv_hash,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["profile"])
    except (json.JSONDecodeError, TypeError):
        return None


def save_cv_profile(cv_hash: str, filename: str, profile: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cv_profiles (cv_hash, filename, profile)
               VALUES (?, ?, ?)
               ON CONFLICT(cv_hash) DO UPDATE SET
                   filename = excluded.filename,
                   profile  = excluded.profile""",
            (cv_hash, filename, json.dumps(profile)),
        )


def load_cv_choices(cv_hash: str) -> dict[str, str]:
    """The user's resolved choices for an ambiguous CV: {field: value}."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT field, value FROM cv_choices WHERE cv_hash = ?", (cv_hash,)
        ).fetchall()
    return {r["field"]: r["value"] for r in rows}


def save_cv_choice(cv_hash: str, field: str, value: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cv_choices (cv_hash, field, value, chosen_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(cv_hash, field)
               DO UPDATE SET value = excluded.value, chosen_at = excluded.chosen_at""",
            (cv_hash, field, value, _now_iso()),
        )


def clear_cv_choice(cv_hash: str, field: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM cv_choices WHERE cv_hash = ? AND field = ?",
            (cv_hash, field),
        )


def list_cv_profiles() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT cv_hash, filename, created_at FROM cv_profiles "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────
# IDF corpus
# ──────────────────────────────────────────────────────────


def bump_token_df(tokens: set[str]):
    """Record that one more document contained each of these tokens."""
    if not tokens:
        return
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO token_df (token, df) VALUES (?, 1)
               ON CONFLICT(token) DO UPDATE SET df = token_df.df + 1""",
            [(t,) for t in tokens],
        )
        conn.execute(
            """INSERT INTO corpus_meta (key, value) VALUES ('doc_count', '1')
               ON CONFLICT(key) DO UPDATE SET value = CAST(
                   CAST(corpus_meta.value AS INTEGER) + 1 AS TEXT)"""
        )


def load_token_df() -> tuple[dict[str, int], int]:
    """Return ({token: document frequency}, total documents seen)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT token, df FROM token_df").fetchall()
        meta = conn.execute(
            "SELECT value FROM corpus_meta WHERE key = 'doc_count'"
        ).fetchone()
    total = int(meta["value"]) if meta else 0
    return {r["token"]: r["df"] for r in rows}, total


def job_exists(url: str) -> bool:
    jid = make_job_id(url)
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (jid,)).fetchone()
    return row is not None


def insert_job(job: dict) -> bool:
    """Insert a new job. Returns True if inserted, False if duplicate."""
    jid = make_job_id(job["url"])
    if job_exists(job["url"]):
        return False
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, title, company, location, url, description,
                              salary, posted_date, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                jid,
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("url", ""),
                job.get("description", ""),
                job.get("salary", ""),
                job.get("posted_date", ""),
                job.get("source", ""),
            ),
        )
    return True


def update_job(job_id: str, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)


def get_all_jobs(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_jobs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_job(job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_jobs_needing_follow_up(follow_up_days: int = 6) -> list[dict]:
    """
    Return jobs that were sent N+ days ago, haven't had a reply detected,
    and haven't had a follow-up sent yet.
    """
    cutoff = _cutoff_iso(follow_up_days)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE email_status = 'sent'
              AND reply_detected = 0
              AND follow_up_status = 'pending'
              AND email_sent_at IS NOT NULL
              AND email_sent_at <= ?
            ORDER BY email_sent_at ASC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def email_already_sent_to(email: str, within_days: int = 30) -> bool:
    """
    Return True if we already sent to this address in the last N days.
    Used to prevent double-emailing the same HR contact.
    """
    if not email:
        return False
    cutoff = _cutoff_iso(within_days)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM jobs
            WHERE (hr_email = ? OR application_email = ?)
              AND email_status = 'sent'
              AND email_sent_at IS NOT NULL
              AND email_sent_at >= ?
            LIMIT 1
            """,
            (email, email, cutoff),
        ).fetchone()
    return row is not None


def emails_sent_today() -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE email_status = 'sent'
              AND email_sent_at IS NOT NULL
              AND substr(email_sent_at, 1, 10) = ?
            """,
            (_today(),),
        ).fetchone()
    return int(row[0] if row else 0)


def get_jobs_by_email(email: str) -> list[dict]:
    """All jobs sent to a given email address, most recent first."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE (hr_email = ? OR application_email = ?)
            ORDER BY email_sent_at DESC
            """,
            (email, email),
        ).fetchall()
    return [dict(r) for r in rows]


def start_run() -> int:
    now = _now_iso()
    with get_conn() as conn:
        if IS_PG:
            cur = conn.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, 'running') RETURNING id",
                (now,),
            )
            return int(cur.fetchone()[0])
        cur = conn.execute(
            "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now,)
        )
        return int(cur.lastrowid)


def finish_run(
    run_id: int,
    found: int,
    scored: int,
    docs: int,
    emails: int = 0,
    follow_ups: int = 0,
    status: str = "done",
):
    with get_conn() as conn:
        conn.execute(
            """UPDATE runs SET finished_at=?, jobs_found=?, jobs_scored=?,
               docs_generated=?, emails_sent=?, follow_ups_sent=?, status=?
               WHERE id=?""",
            (
                _now_iso(),
                found,
                scored,
                docs,
                emails,
                follow_ups,
                status,
                run_id,
            ),
        )


def get_recent_runs(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_jobs_for_portal() -> list[dict]:
    """
    Jobs that have an application_url but no email was sent,
    docs are generated, and portal hasn't been attempted yet.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'done'
              AND output_dir IS NOT NULL AND output_dir != ''
              AND portal_status = 'pending'
              AND email_status NOT IN ('sent')
              AND (application_url IS NOT NULL AND application_url != '')
            ORDER BY score DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    today = _today()
    cutoff14 = _cutoff_iso(14)
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='done'"
        ).fetchone()[0]
        today_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE substr(created_at, 1, 10) = ?",
            (today,),
        ).fetchone()[0]
        skipped = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='skipped'"
        ).fetchone()[0]
        emails_sent = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE email_status='sent'"
        ).fetchone()[0]
        portal_submitted = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE portal_status='submitted'"
        ).fetchone()[0]
        replies = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE reply_detected=1"
        ).fetchone()[0]
        follow_ups = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE follow_up_status='sent'"
        ).fetchone()[0]
        # Board breakdown
        board_rows = conn.execute(
            """SELECT source, COUNT(*) as cnt FROM jobs
               WHERE source IS NOT NULL AND source != ''
               GROUP BY source ORDER BY cnt DESC"""
        ).fetchall()
        by_board = {r["source"]: r["cnt"] for r in board_rows}
        # Daily send trend (last 14 days)
        trend_rows = conn.execute(
            """SELECT substr(email_sent_at, 1, 10) as day, COUNT(*) as cnt
               FROM jobs WHERE email_status='sent' AND email_sent_at IS NOT NULL
               AND email_sent_at >= ?
               GROUP BY substr(email_sent_at, 1, 10) ORDER BY day ASC""",
            (cutoff14,),
        ).fetchall()
        send_trend = [{"day": r["day"], "count": r["cnt"]} for r in trend_rows]

    return {
        "total": total,
        "done": done,
        "today": today_count,
        "skipped": skipped,
        "emails_sent": emails_sent,
        "portal_submitted": portal_submitted,
        "replies": replies,
        "follow_ups": follow_ups,
        "by_board": by_board,
        "send_trend": send_trend,
    }


# ──────────────────────────────────────────────────────────
# App settings — UI-editable config, the source of truth.
#
# This is what lets a deployed user (who has no access to .env) change how the
# tool behaves entirely from the dashboard. Config reads these first, then
# falls back to environment variables, then to code defaults.
# ──────────────────────────────────────────────────────────


def get_settings_map() -> dict[str, str]:
    """All saved settings as {key: value}. Empty on any error (e.g. table not
    created yet), so config can safely fall back to env/defaults."""
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    set_settings({key: value})


def set_settings(values: dict[str, str]):
    """Upsert a batch of settings. Values are stored as strings."""
    if not values:
        return
    now = _now_iso()
    with get_conn() as conn:
        for key, value in values.items():
            conn.execute(
                """INSERT INTO app_settings (key, value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value, updated_at = excluded.updated_at""",
                (key, "" if value is None else str(value), now),
            )


def delete_setting(key: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
