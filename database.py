import sqlite3
import hashlib
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
            """
        )
        # Migrate existing DB — add columns that may not exist yet
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for col, defn in [
            ("email_status",      "TEXT DEFAULT 'not_sent'"),
            ("email_sent_at",     "TEXT"),
            ("email_error",       "TEXT"),
            ("follow_up_sent_at", "TEXT"),
            ("follow_up_status",  "TEXT DEFAULT 'pending'"),
            ("reply_detected",    "INTEGER DEFAULT 0"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")

        existing_runs = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        for col, defn in [
            ("emails_sent",      "INTEGER DEFAULT 0"),
            ("follow_ups_sent",  "INTEGER DEFAULT 0"),
        ]:
            if col not in existing_runs:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {defn}")


def make_job_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


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
                "id":          jid,
                "title":       job.get("title", ""),
                "company":     job.get("company", ""),
                "location":    job.get("location", ""),
                "url":         job.get("url", ""),
                "description": job.get("description", ""),
                "salary":      job.get("salary", ""),
                "posted_date": job.get("posted_date", ""),
                "source":      job.get("source", ""),
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


def finish_run(run_id: int, found: int, scored: int, docs: int,
               emails: int = 0, follow_ups: int = 0, status: str = "done"):
    with get_conn() as conn:
        conn.execute(
            """UPDATE runs SET finished_at=?, jobs_found=?, jobs_scored=?,
               docs_generated=?, emails_sent=?, follow_ups_sent=?, status=?
               WHERE id=?""",
            (datetime.utcnow().isoformat(), found, scored, docs,
             emails, follow_ups, status, run_id),
        )


def get_recent_runs(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    with get_conn() as conn:
        total       = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        done        = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='done'").fetchone()[0]
        today       = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE date(created_at) = date('now')"
        ).fetchone()[0]
        skipped     = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='skipped'").fetchone()[0]
        emails_sent = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE email_status='sent'"
        ).fetchone()[0]
        replies     = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE reply_detected=1"
        ).fetchone()[0]
        follow_ups  = conn.execute(
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
        "total":       total,
        "done":        done,
        "today":       today,
        "skipped":     skipped,
        "emails_sent": emails_sent,
        "replies":     replies,
        "follow_ups":  follow_ups,
        "by_board":    by_board,
        "send_trend":  send_trend,
    }
