"""
Tenant-scoped data access.

Every function here takes an explicit ``user_id`` and filters by it. Routes and
the pipeline must go through this layer rather than querying models directly,
so a missing ``WHERE user_id = ...`` can never leak one tenant's data to
another. Passing an empty ``user_id`` raises, closing the "forgot to scope"
hole rather than silently returning everything.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    CorpusMeta,
    CvChoice,
    CvProfile,
    Job,
    Run,
    TokenDf,
    UserCredential,
    UserSettings,
)
from . import crypto


def _require_user(user_id: str) -> str:
    if not user_id:
        raise ValueError("user_id is required — refusing an unscoped query")
    return user_id


def make_job_id(user_id: str, url: str) -> str:
    """Deterministic per-user job id (same posting → same id for that user)."""
    return hashlib.md5(f"{user_id}:{url}".encode()).hexdigest()


# ──────────────────────────────────────────────────────────────
# Jobs
# ──────────────────────────────────────────────────────────────


def get_jobs(user_id: str, limit: int = 200, status: str | None = None,
             source: str | None = None) -> list[Job]:
    _require_user(user_id)
    stmt = select(Job).where(Job.user_id == user_id)
    if status and status != "all":
        stmt = stmt.where(Job.status == status)
    if source and source != "all":
        stmt = stmt.where(Job.source == source)
    stmt = stmt.order_by(Job.created_at.desc()).limit(limit)
    return list(db.session.scalars(stmt))


def get_job(user_id: str, job_id: str) -> Job | None:
    _require_user(user_id)
    return db.session.scalar(
        select(Job).where(Job.user_id == user_id, Job.id == job_id)
    )


def job_exists(user_id: str, url: str) -> bool:
    _require_user(user_id)
    return db.session.scalar(
        select(func.count()).select_from(Job).where(
            Job.user_id == user_id, Job.url == url
        )
    ) > 0


def insert_job(user_id: str, job: dict[str, Any]) -> Job | None:
    """Insert a new job for this user. Returns None if a duplicate URL exists."""
    _require_user(user_id)
    url = job.get("url", "")
    if not url or job_exists(user_id, url):
        return None
    obj = Job(
        id=make_job_id(user_id, url),
        user_id=user_id,
        title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        url=url,
        description=job.get("description", ""),
        salary=job.get("salary", ""),
        posted_date=job.get("posted_date", ""),
        source=job.get("source", ""),
    )
    db.session.add(obj)
    db.session.commit()
    return obj


def update_job(user_id: str, job_id: str, **fields: Any) -> None:
    _require_user(user_id)
    job = get_job(user_id, job_id)
    if not job:
        return
    for key, value in fields.items():
        if hasattr(job, key):
            setattr(job, key, value)
    db.session.commit()


# ──────────────────────────────────────────────────────────────
# Follow-up / dedup / portal selectors
# ──────────────────────────────────────────────────────────────


def get_jobs_needing_follow_up(user_id: str, follow_up_days: int = 6) -> list[Job]:
    _require_user(user_id)
    cutoff_expr = func.julianday("now") - func.julianday(Job.email_sent_at)
    stmt = (
        select(Job)
        .where(
            Job.user_id == user_id,
            Job.email_status == "sent",
            Job.reply_detected.is_(False),
            Job.follow_up_status == "pending",
            Job.email_sent_at.is_not(None),
            cutoff_expr >= follow_up_days,
        )
        .order_by(Job.email_sent_at.asc())
    )
    return list(db.session.scalars(stmt))


def email_already_sent_to(user_id: str, email: str, within_days: int = 30) -> bool:
    _require_user(user_id)
    if not email:
        return False
    cutoff_expr = func.julianday("now") - func.julianday(Job.email_sent_at)
    return db.session.scalar(
        select(func.count()).select_from(Job).where(
            Job.user_id == user_id,
            (Job.hr_email == email) | (Job.application_email == email),
            Job.email_status == "sent",
            Job.email_sent_at.is_not(None),
            cutoff_expr <= within_days,
        )
    ) > 0


def emails_sent_today(user_id: str) -> int:
    _require_user(user_id)
    return db.session.scalar(
        select(func.count()).select_from(Job).where(
            Job.user_id == user_id,
            Job.email_status == "sent",
            Job.email_sent_at.is_not(None),
            func.date(Job.email_sent_at) == func.date("now"),
        )
    ) or 0


def jobs_with_replies_awaiting_action(user_id: str) -> list[Job]:
    """
    Replies that need the user's manual attention: a reply was detected and no
    follow-up has been sent (we deliberately do NOT auto-follow-up a reply).
    """
    _require_user(user_id)
    return list(db.session.scalars(
        select(Job).where(
            Job.user_id == user_id,
            Job.reply_detected.is_(True),
            Job.follow_up_status != "sent",
        ).order_by(Job.email_sent_at.desc())
    ))


def get_jobs_for_portal(user_id: str) -> list[Job]:
    _require_user(user_id)
    stmt = select(Job).where(
        Job.user_id == user_id,
        Job.status == "done",
        Job.output_dir != "",
        Job.portal_status == "pending",
        Job.email_status != "sent",
        Job.application_url != "",
    ).order_by(Job.score.desc())
    return list(db.session.scalars(stmt))


# ──────────────────────────────────────────────────────────────
# Runs & stats
# ──────────────────────────────────────────────────────────────


def start_run(user_id: str) -> str:
    _require_user(user_id)
    run = Run(user_id=user_id, started_at=datetime.now(UTC), status="running")
    db.session.add(run)
    db.session.commit()
    return run.id


def finish_run(user_id: str, run_id: str, found: int, scored: int, docs: int,
               emails: int = 0, follow_ups: int = 0, status: str = "done") -> None:
    _require_user(user_id)
    run = db.session.scalar(
        select(Run).where(Run.user_id == user_id, Run.id == run_id)
    )
    if not run:
        return
    run.finished_at = datetime.now(UTC)
    run.jobs_found, run.jobs_scored, run.docs_generated = found, scored, docs
    run.emails_sent, run.follow_ups_sent, run.status = emails, follow_ups, status
    db.session.commit()


def get_recent_runs(user_id: str, limit: int = 10) -> list[Run]:
    _require_user(user_id)
    return list(
        db.session.scalars(
            select(Run).where(Run.user_id == user_id)
            .order_by(Run.started_at.desc()).limit(limit)
        )
    )


def get_stats(user_id: str) -> dict[str, Any]:
    _require_user(user_id)

    def count(*conds) -> int:
        return db.session.scalar(
            select(func.count()).select_from(Job).where(Job.user_id == user_id, *conds)
        ) or 0

    board_rows = db.session.execute(
        select(Job.source, func.count()).where(
            Job.user_id == user_id, Job.source != ""
        ).group_by(Job.source).order_by(func.count().desc())
    ).all()

    return {
        "total": count(),
        "done": count(Job.status == "done"),
        "skipped": count(Job.status == "skipped"),
        "emails_sent": count(Job.email_status == "sent"),
        "portal_submitted": count(Job.portal_status == "submitted"),
        "replies": count(Job.reply_detected.is_(True)),
        "follow_ups": count(Job.follow_up_status == "sent"),
        "by_board": {src: cnt for src, cnt in board_rows},
    }


# ──────────────────────────────────────────────────────────────
# CV profile cache & ambiguity choices
# ──────────────────────────────────────────────────────────────


def load_cv_profile(user_id: str, content_hash: str) -> dict | None:
    _require_user(user_id)
    row = db.session.scalar(
        select(CvProfile).where(
            CvProfile.user_id == user_id, CvProfile.content_hash == content_hash
        )
    )
    if not row:
        return None
    return row.profile if isinstance(row.profile, dict) else json.loads(row.profile)


def save_cv_profile(user_id: str, content_hash: str, filename: str, profile: dict) -> None:
    _require_user(user_id)
    row = db.session.scalar(
        select(CvProfile).where(
            CvProfile.user_id == user_id, CvProfile.content_hash == content_hash
        )
    )
    if row:
        row.filename, row.profile = filename, profile
    else:
        db.session.add(CvProfile(
            user_id=user_id, content_hash=content_hash,
            filename=filename, profile=profile,
        ))
    db.session.commit()


def load_cv_choices(user_id: str, content_hash: str) -> dict[str, str]:
    _require_user(user_id)
    rows = db.session.scalars(
        select(CvChoice).where(
            CvChoice.user_id == user_id, CvChoice.content_hash == content_hash
        )
    )
    return {r.field: r.value for r in rows}


def save_cv_choice(user_id: str, content_hash: str, field: str, value: str) -> None:
    _require_user(user_id)
    row = db.session.scalar(
        select(CvChoice).where(
            CvChoice.user_id == user_id,
            CvChoice.content_hash == content_hash,
            CvChoice.field == field,
        )
    )
    if row:
        row.value = value
    else:
        db.session.add(CvChoice(
            user_id=user_id, content_hash=content_hash, field=field, value=value
        ))
    db.session.commit()


def clear_cv_choice(user_id: str, content_hash: str, field: str) -> None:
    _require_user(user_id)
    row = db.session.scalar(
        select(CvChoice).where(
            CvChoice.user_id == user_id,
            CvChoice.content_hash == content_hash,
            CvChoice.field == field,
        )
    )
    if row:
        db.session.delete(row)
        db.session.commit()


# ──────────────────────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────────────────────


def get_or_create_settings(user_id: str) -> UserSettings:
    _require_user(user_id)
    row = db.session.scalar(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    if not row:
        row = UserSettings(user_id=user_id)
        db.session.add(row)
        db.session.commit()
    return row


# ──────────────────────────────────────────────────────────────
# Credentials (encrypted at rest)
# ──────────────────────────────────────────────────────────────


def set_credential(user_id: str, provider: str, secret: str,
                   name: str = "default", meta: str = "") -> None:
    _require_user(user_id)
    row = db.session.scalar(
        select(UserCredential).where(
            UserCredential.user_id == user_id,
            UserCredential.provider == provider,
            UserCredential.name == name,
        )
    )
    ciphertext = crypto.encrypt(secret)
    if row:
        row.ciphertext, row.meta = ciphertext, meta
    else:
        db.session.add(UserCredential(
            user_id=user_id, provider=provider, name=name,
            ciphertext=ciphertext, meta=meta,
        ))
    db.session.commit()


def get_credential(user_id: str, provider: str, name: str = "default") -> str:
    _require_user(user_id)
    row = db.session.scalar(
        select(UserCredential).where(
            UserCredential.user_id == user_id,
            UserCredential.provider == provider,
            UserCredential.name == name,
        )
    )
    return crypto.decrypt(row.ciphertext) if row else ""


# ──────────────────────────────────────────────────────────────
# Global IDF corpus (intentionally NOT tenant-scoped — see models/corpus.py)
# ──────────────────────────────────────────────────────────────


def bump_token_df(tokens: set[str]) -> None:
    if not tokens:
        return
    for tok in tokens:
        row = db.session.get(TokenDf, tok)
        if row:
            row.df += 1
        else:
            db.session.add(TokenDf(token=tok, df=1))
    meta = db.session.get(CorpusMeta, "doc_count")
    if meta:
        meta.value = str(int(meta.value or 0) + 1)
    else:
        db.session.add(CorpusMeta(key="doc_count", value="1"))
    db.session.commit()


def load_token_df() -> tuple[dict[str, int], int]:
    rows = db.session.scalars(select(TokenDf))
    df = {r.token: r.df for r in rows}
    meta = db.session.get(CorpusMeta, "doc_count")
    total = int(meta.value) if meta and meta.value else 0
    return df, total
