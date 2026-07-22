import sqlite3
import hashlib
import json
from datetime import datetime
from config import config


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
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

            -- When a CV states the same thing twice (two emails, two phone
            -- numbers — usually an old value left in a template), the parser
            -- cannot know which is current. The user picks, and the pick is
            -- remembered against that specific CV. Keyed by cv_hash so a
            -- different CV, or an edited one, asks again rather than silently
            -- reusing a stale answer.
            CREATE TABLE IF NOT EXISTS cv_choices (
                cv_hash    TEXT,
                field      TEXT,
                value      TEXT,
                chosen_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (cv_hash, field)
            );

            -- Document-frequency counts over scraped job descriptions.
            -- Feeds IDF weighting in the scorer so common boilerplate words
            -- ("team", "agile") count for less than rare stack terms.
            CREATE TABLE IF NOT EXISTS token_df (
                token TEXT PRIMARY KEY,
                df    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS corpus_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        # Migrate existing DB — add columns that may not exist yet
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for col, defn in [
            ("email_status", "TEXT DEFAULT 'not_sent'"),
            ("email_sent_at", "TEXT"),
            ("email_error", "TEXT"),
            ("follow_up_sent_at", "TEXT"),
            ("follow_up_status", "TEXT DEFAULT 'pending'"),
            ("reply_detected", "INTEGER DEFAULT 0"),
            ("portal_status", "TEXT DEFAULT 'pending'"),
            ("portal_submitted_at", "TEXT"),
            ("portal_error", "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")

        existing_runs = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        for col, defn in [
            ("emails_sent", "INTEGER DEFAULT 0"),
            ("follow_ups_sent", "INTEGER DEFAULT 0"),
        ]:
            if col not in existing_runs:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {defn}")


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
            """INSERT OR REPLACE INTO cv_profiles (cv_hash, filename, profile)
               VALUES (?, ?, ?)""",
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
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(cv_hash, field)
               DO UPDATE SET value = excluded.value, chosen_at = excluded.chosen_at""",
            (cv_hash, field, value),
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
               ON CONFLICT(token) DO UPDATE SET df = df + 1""",
            [(t,) for t in tokens],
        )
        conn.execute(
            """INSERT INTO corpus_meta (key, value) VALUES ('doc_count', '1')
               ON CONFLICT(key) DO UPDATE SET value = CAST(
                   CAST(value AS INTEGER) + 1 AS TEXT)"""
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
            VALUES (:id, :title, :company, :location, :url, :description,
                    :salary, :posted_date, :source)
            """,
            {
                "id": jid,
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
                "url": job.get("url", ""),
                "description": job.get("description", ""),
                "salary": job.get("salary", ""),
                "posted_date": job.get("posted_date", ""),
                "source": job.get("source", ""),
            },
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
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE email_status = 'sent'
              AND reply_detected = 0
              AND follow_up_status = 'pending'
              AND email_sent_at IS NOT NULL
              AND julianday('now') - julianday(email_sent_at) >= ?
            ORDER BY email_sent_at ASC
            """,
            (follow_up_days,),
        ).fetchall()
    return [dict(r) for r in rows]


def email_already_sent_to(email: str, within_days: int = 30) -> bool:
    """
    Return True if we already sent to this address in the last N days.
    Used to prevent double-emailing the same HR contact.
    """
    if not email:
        return False
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM jobs
            WHERE (hr_email = ? OR application_email = ?)
              AND email_status = 'sent'
              AND email_sent_at IS NOT NULL
              AND julianday('now') - julianday(email_sent_at) <= ?
            LIMIT 1
            """,
            (email, email, within_days),
        ).fetchone()
    return row is not None


def emails_sent_today() -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE email_status = 'sent'
              AND email_sent_at IS NOT NULL
              AND date(email_sent_at) = date('now')
            """
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
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
            (datetime.utcnow().isoformat(),),
        )
    return cur.lastrowid


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
                datetime.utcnow().isoformat(),
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
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='done'").fetchone()[
            0
        ]
        today = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE date(created_at) = date('now')"
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
            """SELECT date(email_sent_at) as day, COUNT(*) as cnt
               FROM jobs WHERE email_status='sent' AND email_sent_at IS NOT NULL
               AND julianday('now') - julianday(email_sent_at) <= 14
               GROUP BY day ORDER BY day ASC"""
        ).fetchall()
        send_trend = [{"day": r["day"], "count": r["cnt"]} for r in trend_rows]

    return {
        "total": total,
        "done": done,
        "today": today,
        "skipped": skipped,
        "emails_sent": emails_sent,
        "portal_submitted": portal_submitted,
        "replies": replies,
        "follow_ups": follow_ups,
        "by_board": by_board,
        "send_trend": send_trend,
    }
