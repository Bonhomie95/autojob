"""
Tenant-scoped data access.

Every function here takes an explicit ``user_id`` and filters by it. Routes and
the pipeline must go through this layer rather than querying Mongo directly, so
a missing ``{"user_id": ...}`` filter can never leak one tenant's data to
another. Passing an empty ``user_id`` raises, closing the "forgot to scope"
hole rather than silently returning everything.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.errors import DuplicateKeyError

from ..extensions import db
from ..models import CvDocument, CvProfile, Job, Run, User, UserSettings
from ..models.base import gen_uuid
from . import crypto


def _require_user(user_id: str) -> str:
    if not user_id:
        raise ValueError("user_id is required — refusing an unscoped query")
    return user_id


def make_job_id(user_id: str, url: str) -> str:
    """Deterministic per-user job id (same posting → same id for that user)."""
    return hashlib.md5(f"{user_id}:{url}".encode()).hexdigest()


# ──────────────────────────────────────────────────────────────
# Users
# ──────────────────────────────────────────────────────────────


def get_user_by_id(user_id: str) -> User | None:
    if not user_id:
        return None
    doc = db.conn.users.find_one({"_id": user_id})
    return User.from_doc(doc) if doc else None


def get_user_by_email(email: str) -> User | None:
    if not email:
        return None
    doc = db.conn.users.find_one({"email": email})
    return User.from_doc(doc) if doc else None


def create_user(email: str, name: str, password: str) -> User:
    user = User(email=email, name=name)
    user.set_password(password)
    db.conn.users.insert_one(user.to_doc())
    return user


def update_user(user_id: str, **fields: Any) -> None:
    _require_user(user_id)
    if not fields:
        return
    fields["updated_at"] = datetime.now(UTC)
    db.conn.users.update_one({"_id": user_id}, {"$set": fields})


# ──────────────────────────────────────────────────────────────
# Jobs
# ──────────────────────────────────────────────────────────────


def get_jobs(user_id: str, limit: int = 200, status: str | None = None,
             source: str | None = None) -> list[Job]:
    _require_user(user_id)
    query: dict[str, Any] = {"user_id": user_id}
    if status and status != "all":
        query["status"] = status
    if source and source != "all":
        query["source"] = source
    cursor = db.conn.jobs.find(query).sort("created_at", -1).limit(limit)
    return [Job.from_doc(d) for d in cursor]


def get_job(user_id: str, job_id: str) -> Job | None:
    _require_user(user_id)
    doc = db.conn.jobs.find_one({"user_id": user_id, "_id": job_id})
    return Job.from_doc(doc) if doc else None


def job_exists(user_id: str, url: str) -> bool:
    _require_user(user_id)
    return db.conn.jobs.count_documents({"user_id": user_id, "url": url}) > 0


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
    try:
        db.conn.jobs.insert_one(obj.to_doc())
    except DuplicateKeyError:
        # lost a race with a concurrent insert of the same (user_id, url)
        return None
    return obj


def update_job(user_id: str, job_id: str, **fields: Any) -> None:
    _require_user(user_id)
    if not fields:
        return
    db.conn.jobs.update_one({"user_id": user_id, "_id": job_id}, {"$set": fields})


def clear_jobs(user_id: str) -> int:
    """
    Delete every discovered job for this user, so previously-seen postings
    (deduped by URL) can resurface on the next run. Returns the count deleted.
    """
    _require_user(user_id)
    result = db.conn.jobs.delete_many({"user_id": user_id})
    return result.deleted_count


# ──────────────────────────────────────────────────────────────
# Follow-up / dedup / portal selectors
# ──────────────────────────────────────────────────────────────


def get_jobs_needing_follow_up(user_id: str, follow_up_days: int = 6) -> list[Job]:
    _require_user(user_id)
    cutoff = datetime.now(UTC) - timedelta(days=follow_up_days)
    query = {
        "user_id": user_id,
        "email_status": "sent",
        "reply_detected": False,
        "follow_up_status": "pending",
        "email_sent_at": {"$ne": None, "$lte": cutoff},
    }
    cursor = db.conn.jobs.find(query).sort("email_sent_at", 1)
    return [Job.from_doc(d) for d in cursor]


def email_already_sent_to(user_id: str, email: str, within_days: int = 30) -> bool:
    _require_user(user_id)
    if not email:
        return False
    cutoff = datetime.now(UTC) - timedelta(days=within_days)
    query = {
        "user_id": user_id,
        "$or": [{"hr_email": email}, {"application_email": email}],
        "email_status": "sent",
        "email_sent_at": {"$ne": None, "$gte": cutoff},
    }
    return db.conn.jobs.count_documents(query) > 0


def emails_sent_today(user_id: str) -> int:
    _require_user(user_id)
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    query = {
        "user_id": user_id,
        "email_status": "sent",
        "email_sent_at": {"$gte": start, "$lt": end},
    }
    return db.conn.jobs.count_documents(query)


def jobs_with_replies_awaiting_action(user_id: str) -> list[Job]:
    """
    Replies that need the user's manual attention: a reply was detected and no
    follow-up has been sent (we deliberately do NOT auto-follow-up a reply).
    """
    _require_user(user_id)
    query = {"user_id": user_id, "reply_detected": True, "follow_up_status": {"$ne": "sent"}}
    cursor = db.conn.jobs.find(query).sort("email_sent_at", -1)
    return [Job.from_doc(d) for d in cursor]


def get_jobs_for_portal(user_id: str) -> list[Job]:
    _require_user(user_id)
    query = {
        "user_id": user_id,
        "status": "done",
        "output_dir": {"$ne": ""},
        "portal_status": "pending",
        "email_status": {"$ne": "sent"},
        "application_url": {"$ne": ""},
    }
    cursor = db.conn.jobs.find(query).sort("score", -1)
    return [Job.from_doc(d) for d in cursor]


def get_jobs_pending_reply_check(user_id: str) -> list[Job]:
    """Jobs still awaiting an outcome: sent, not yet replied, not yet bounced."""
    _require_user(user_id)
    query = {
        "user_id": user_id,
        "email_status": "sent",
        "reply_detected": False,
        "bounced": False,
    }
    return [Job.from_doc(d) for d in db.conn.jobs.find(query)]


# ──────────────────────────────────────────────────────────────
# Runs & stats
# ──────────────────────────────────────────────────────────────


class RunAlreadyActive(Exception):
    """Raised by start_run when this user already has a run in progress.

    Distinct from a plain check (has_active_run): the partial unique index on
    (user_id, status='running') makes this atomic, so two near-simultaneous
    start_run calls can't both succeed the way a check-then-insert could.
    """


def start_run(user_id: str) -> str:
    _require_user(user_id)
    run = Run(user_id=user_id, started_at=datetime.now(UTC), status="running")
    try:
        db.conn.runs.insert_one(run.to_doc())
    except DuplicateKeyError as exc:
        raise RunAlreadyActive() from exc
    return run.id


def finish_run(user_id: str, run_id: str, found: int, scored: int, docs: int,
               emails: int = 0, follow_ups: int = 0, status: str = "done") -> None:
    _require_user(user_id)
    db.conn.runs.update_one(
        {"user_id": user_id, "_id": run_id},
        {"$set": {
            "finished_at": datetime.now(UTC),
            "jobs_found": found,
            "jobs_scored": scored,
            "docs_generated": docs,
            "emails_sent": emails,
            "follow_ups_sent": follow_ups,
            "status": status,
        }},
    )


def get_run(user_id: str, run_id: str) -> Run | None:
    _require_user(user_id)
    doc = db.conn.runs.find_one({"user_id": user_id, "_id": run_id})
    return Run.from_doc(doc) if doc else None


def has_active_run(user_id: str) -> Run | None:
    _require_user(user_id)
    doc = db.conn.runs.find_one({"user_id": user_id, "status": "running"})
    return Run.from_doc(doc) if doc else None


def request_cancel_run(user_id: str, run_id: str) -> bool:
    """
    Ask a running pipeline to stop at its next safe checkpoint.

    Returns False if there's no matching run currently in the 'running'
    state (already finished, already cancelled, or not this user's).
    """
    _require_user(user_id)
    result = db.conn.runs.update_one(
        {"user_id": user_id, "_id": run_id, "status": "running"},
        {"$set": {"cancel_requested": True}},
    )
    return result.matched_count > 0


def run_cancel_requested(user_id: str, run_id: str) -> bool:
    _require_user(user_id)
    doc = db.conn.runs.find_one(
        {"user_id": user_id, "_id": run_id}, {"cancel_requested": 1}
    )
    return bool(doc and doc.get("cancel_requested"))


def get_recent_runs(user_id: str, limit: int = 10) -> list[Run]:
    _require_user(user_id)
    cursor = db.conn.runs.find({"user_id": user_id}).sort("started_at", -1).limit(limit)
    return [Run.from_doc(d) for d in cursor]


def get_stats(user_id: str) -> dict[str, Any]:
    _require_user(user_id)

    def count(extra: dict | None = None) -> int:
        query = {"user_id": user_id}
        if extra:
            query.update(extra)
        return db.conn.jobs.count_documents(query)

    board_counts: Counter[str] = Counter()
    for doc in db.conn.jobs.find({"user_id": user_id, "source": {"$ne": ""}}, {"source": 1}):
        board_counts[doc["source"]] += 1

    return {
        "total": count(),
        "done": count({"status": "done"}),
        "skipped": count({"status": "skipped"}),
        "emails_sent": count({"email_status": "sent"}),
        "portal_submitted": count({"portal_status": "submitted"}),
        "replies": count({"reply_detected": True}),
        "follow_ups": count({"follow_up_status": "sent"}),
        "by_board": dict(board_counts.most_common()),
    }


# ──────────────────────────────────────────────────────────────
# CV documents
# ──────────────────────────────────────────────────────────────


def deactivate_cv_documents(user_id: str) -> None:
    _require_user(user_id)
    db.conn.cv_documents.update_many({"user_id": user_id}, {"$set": {"is_active": False}})


def find_cv_document(user_id: str, content_hash: str) -> CvDocument | None:
    _require_user(user_id)
    doc = db.conn.cv_documents.find_one({"user_id": user_id, "content_hash": content_hash})
    return CvDocument.from_doc(doc) if doc else None


def insert_cv_document(document: CvDocument) -> None:
    db.conn.cv_documents.insert_one(document.to_doc())


def update_cv_document(user_id: str, cv_id: str, **fields: Any) -> None:
    _require_user(user_id)
    if not fields:
        return
    fields["updated_at"] = datetime.now(UTC)
    db.conn.cv_documents.update_one({"user_id": user_id, "_id": cv_id}, {"$set": fields})


def get_active_cv_document(user_id: str) -> CvDocument | None:
    _require_user(user_id)
    doc = db.conn.cv_documents.find_one({"user_id": user_id, "is_active": True})
    return CvDocument.from_doc(doc) if doc else None


def delete_cv_document(user_id: str, cv_id: str) -> None:
    _require_user(user_id)
    db.conn.cv_documents.delete_one({"user_id": user_id, "_id": cv_id})


# ──────────────────────────────────────────────────────────────
# CV profile cache & ambiguity choices
# ──────────────────────────────────────────────────────────────


def load_cv_profile(user_id: str, content_hash: str) -> dict | None:
    _require_user(user_id)
    doc = db.conn.cv_profiles.find_one({"user_id": user_id, "content_hash": content_hash})
    if not doc:
        return None
    return CvProfile.from_doc(doc).profile


def save_cv_profile(user_id: str, content_hash: str, filename: str, profile: dict) -> None:
    _require_user(user_id)
    db.conn.cv_profiles.update_one(
        {"user_id": user_id, "content_hash": content_hash},
        {
            "$set": {"filename": filename, "profile": profile},
            "$setOnInsert": {
                "_id": gen_uuid(), "user_id": user_id, "content_hash": content_hash,
            },
        },
        upsert=True,
    )


def load_cv_choices(user_id: str, content_hash: str) -> dict[str, str]:
    _require_user(user_id)
    cursor = db.conn.cv_choices.find({"user_id": user_id, "content_hash": content_hash})
    return {d["field"]: d.get("value", "") for d in cursor}


def save_cv_choice(user_id: str, content_hash: str, field: str, value: str) -> None:
    _require_user(user_id)
    db.conn.cv_choices.update_one(
        {"user_id": user_id, "content_hash": content_hash, "field": field},
        {
            "$set": {"value": value},
            "$setOnInsert": {
                "_id": gen_uuid(), "user_id": user_id,
                "content_hash": content_hash, "field": field,
            },
        },
        upsert=True,
    )


def clear_cv_choice(user_id: str, content_hash: str, field: str) -> None:
    _require_user(user_id)
    db.conn.cv_choices.delete_one(
        {"user_id": user_id, "content_hash": content_hash, "field": field}
    )


# ──────────────────────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────────────────────


def get_or_create_settings(user_id: str) -> UserSettings:
    _require_user(user_id)
    doc = db.conn.user_settings.find_one({"user_id": user_id})
    if doc:
        return UserSettings.from_doc(doc)
    settings = UserSettings(user_id=user_id)
    try:
        db.conn.user_settings.insert_one(settings.to_doc())
    except DuplicateKeyError:
        # lost a race with a concurrent first-request settings creation.
        doc = db.conn.user_settings.find_one({"user_id": user_id})
        return UserSettings.from_doc(doc)
    return settings


def update_settings(user_id: str, **fields: Any) -> UserSettings:
    """Persist changed fields on a user's settings, auto-creating the row first."""
    _require_user(user_id)
    get_or_create_settings(user_id)
    if fields:
        fields["updated_at"] = datetime.now(UTC)
        db.conn.user_settings.update_one({"user_id": user_id}, {"$set": fields})
    doc = db.conn.user_settings.find_one({"user_id": user_id})
    return UserSettings.from_doc(doc)


# ──────────────────────────────────────────────────────────────
# Credentials (encrypted at rest)
# ──────────────────────────────────────────────────────────────


def set_credential(user_id: str, provider: str, secret: str,
                   name: str = "default", meta: str = "") -> None:
    _require_user(user_id)
    ciphertext = crypto.encrypt(secret)
    now = datetime.now(UTC)
    db.conn.user_credentials.update_one(
        {"user_id": user_id, "provider": provider, "name": name},
        {
            "$set": {"ciphertext": ciphertext, "meta": meta, "updated_at": now},
            "$setOnInsert": {
                "_id": gen_uuid(), "user_id": user_id, "provider": provider,
                "name": name, "created_at": now,
            },
        },
        upsert=True,
    )


def get_credential(user_id: str, provider: str, name: str = "default") -> str:
    _require_user(user_id)
    doc = db.conn.user_credentials.find_one(
        {"user_id": user_id, "provider": provider, "name": name}
    )
    return crypto.decrypt(doc["ciphertext"]) if doc else ""


def get_credential_meta(user_id: str, provider: str, name: str = "default") -> dict:
    """Non-secret metadata for a credential (e.g. the SMTP host/port/from)."""
    _require_user(user_id)
    doc = db.conn.user_credentials.find_one(
        {"user_id": user_id, "provider": provider, "name": name}
    )
    if not doc or not doc.get("meta"):
        return {}
    try:
        return json.loads(doc["meta"])
    except (ValueError, TypeError):
        return {}


def list_credential_providers(user_id: str) -> dict[str, bool]:
    """Which providers a user has a credential saved for (settings page badges)."""
    _require_user(user_id)
    cursor = db.conn.user_credentials.find({"user_id": user_id}, {"provider": 1})
    return {d["provider"]: True for d in cursor}


def delete_credential(user_id: str, provider: str, name: str = "default") -> bool:
    """Revoke a saved credential. Returns False if there was none to delete."""
    _require_user(user_id)
    result = db.conn.user_credentials.delete_one(
        {"user_id": user_id, "provider": provider, "name": name}
    )
    return result.deleted_count > 0


# ──────────────────────────────────────────────────────────────
# Scheduling fan-out (used by tasks.py)
# ──────────────────────────────────────────────────────────────


def list_users_with_schedule_enabled() -> list[UserSettings]:
    cursor = db.conn.user_settings.find({"schedule_enabled": True})
    return [UserSettings.from_doc(d) for d in cursor]


def list_users_with_followup_enabled() -> list[UserSettings]:
    cursor = db.conn.user_settings.find({"follow_up_enabled": True})
    return [UserSettings.from_doc(d) for d in cursor]


# ──────────────────────────────────────────────────────────────
# Follow-up-cycle lock — prevents two concurrent reply-check/follow-up
# cycles for the same user (e.g. a double-clicked button) from both reading
# "needs follow-up" before either writes, which would send duplicates.
# ──────────────────────────────────────────────────────────────


def claim_followup_lock(user_id: str) -> bool:
    """Atomically claim the per-user follow-up lock. False if already held."""
    _require_user(user_id)
    try:
        db.conn.followup_locks.insert_one({"_id": user_id, "claimed_at": datetime.now(UTC)})
    except DuplicateKeyError:
        return False
    return True


def release_followup_lock(user_id: str) -> None:
    _require_user(user_id)
    db.conn.followup_locks.delete_one({"_id": user_id})


# ──────────────────────────────────────────────────────────────
# Global IDF corpus (intentionally NOT tenant-scoped — deliberately plain
# dicts, not dataclasses: nothing outside this module needs a model for it).
# ──────────────────────────────────────────────────────────────


def bump_token_df(tokens: set[str]) -> None:
    if not tokens:
        return
    for tok in tokens:
        db.conn.token_df.update_one({"_id": tok}, {"$inc": {"df": 1}}, upsert=True)
    db.conn.corpus_meta.update_one({"_id": "doc_count"}, {"$inc": {"count": 1}}, upsert=True)


def load_token_df() -> tuple[dict[str, int], int]:
    df = {d["_id"]: d.get("df", 0) for d in db.conn.token_df.find()}
    meta = db.conn.corpus_meta.find_one({"_id": "doc_count"})
    total = meta.get("count", 0) if meta else 0
    return df, total
