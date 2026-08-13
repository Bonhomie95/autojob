"""
Jobs and pipeline runs — tenant-scoped.

Mirrors the legacy ``jobs`` and ``runs`` tables from ``database.py`` but adds a
``user_id`` so every discovered job, generated document, and sent email belongs
to exactly one user. The ``url`` uniqueness that was global before is now
unique *per user* (two users can legitimately both discover the same posting) —
enforced by a compound Mongo index, see db_bootstrap.ensure_indexes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .base import gen_uuid, utcnow


@dataclass
class Job:
    COLLECTION = "jobs"

    user_id: str
    id: str = field(default_factory=gen_uuid)

    # ── Posting ──────────────────────────────────────────────────
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    salary: str = ""
    posted_date: str = ""
    source: str = ""
    score: int = 0

    # ── Contact ──────────────────────────────────────────────────
    hr_name: str = ""
    hr_email: str = ""
    hr_title: str = ""
    application_email: str = ""
    application_url: str = ""
    contact_notes: str = ""

    # ── Workflow state ───────────────────────────────────────────
    status: str = "pending"
    output_dir: str = ""

    # email_status: not_sent | sent | bounced | failed
    email_status: str = "not_sent"
    email_sent_at: datetime | None = None
    email_error: str = ""
    # RFC-822 Message-ID we set when sending, so replies/bounces can be matched
    # to this exact thread rather than guessing by sender address alone.
    email_message_id: str = ""
    # True once a bounce (mailer-daemon/postmaster) is detected → not delivered.
    bounced: bool = False

    follow_up_sent_at: datetime | None = None
    follow_up_status: str = "pending"
    reply_detected: bool = False
    reply_detected_at: datetime | None = None

    portal_status: str = "pending"
    portal_submitted_at: datetime | None = None
    portal_error: str = ""

    created_at: datetime = field(default_factory=utcnow)

    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "description": self.description,
            "salary": self.salary,
            "posted_date": self.posted_date,
            "source": self.source,
            "score": self.score,
            "hr_name": self.hr_name,
            "hr_email": self.hr_email,
            "hr_title": self.hr_title,
            "application_email": self.application_email,
            "application_url": self.application_url,
            "contact_notes": self.contact_notes,
            "status": self.status,
            "output_dir": self.output_dir,
            "email_status": self.email_status,
            "email_sent_at": self.email_sent_at,
            "email_error": self.email_error,
            "email_message_id": self.email_message_id,
            "bounced": self.bounced,
            "follow_up_sent_at": self.follow_up_sent_at,
            "follow_up_status": self.follow_up_status,
            "reply_detected": self.reply_detected,
            "reply_detected_at": self.reply_detected_at,
            "portal_status": self.portal_status,
            "portal_submitted_at": self.portal_submitted_at,
            "portal_error": self.portal_error,
            "created_at": self.created_at,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> Job:
        defaults = cls(user_id=doc["user_id"])
        return cls(
            id=doc["_id"],
            user_id=doc["user_id"],
            title=doc.get("title", defaults.title),
            company=doc.get("company", defaults.company),
            location=doc.get("location", defaults.location),
            url=doc.get("url", defaults.url),
            description=doc.get("description", defaults.description),
            salary=doc.get("salary", defaults.salary),
            posted_date=doc.get("posted_date", defaults.posted_date),
            source=doc.get("source", defaults.source),
            score=doc.get("score", defaults.score),
            hr_name=doc.get("hr_name", defaults.hr_name),
            hr_email=doc.get("hr_email", defaults.hr_email),
            hr_title=doc.get("hr_title", defaults.hr_title),
            application_email=doc.get("application_email", defaults.application_email),
            application_url=doc.get("application_url", defaults.application_url),
            contact_notes=doc.get("contact_notes", defaults.contact_notes),
            status=doc.get("status", defaults.status),
            output_dir=doc.get("output_dir", defaults.output_dir),
            email_status=doc.get("email_status", defaults.email_status),
            email_sent_at=doc.get("email_sent_at"),
            email_error=doc.get("email_error", defaults.email_error),
            email_message_id=doc.get("email_message_id", defaults.email_message_id),
            bounced=doc.get("bounced", defaults.bounced),
            follow_up_sent_at=doc.get("follow_up_sent_at"),
            follow_up_status=doc.get("follow_up_status", defaults.follow_up_status),
            reply_detected=doc.get("reply_detected", defaults.reply_detected),
            reply_detected_at=doc.get("reply_detected_at"),
            portal_status=doc.get("portal_status", defaults.portal_status),
            portal_submitted_at=doc.get("portal_submitted_at"),
            portal_error=doc.get("portal_error", defaults.portal_error),
            created_at=doc.get("created_at", utcnow()),
        )


@dataclass
class Run:
    COLLECTION = "runs"

    user_id: str
    id: str = field(default_factory=gen_uuid)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    jobs_found: int = 0
    jobs_scored: int = 0
    docs_generated: int = 0
    emails_sent: int = 0
    follow_ups_sent: int = 0
    status: str = "running"
    # Cooperative stop signal: the pipeline checks this at each safe checkpoint
    # (between scrapers/jobs) rather than being killed outright, so a job that's
    # mid-write never gets left half-updated.
    cancel_requested: bool = False

    def to_doc(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "jobs_found": self.jobs_found,
            "jobs_scored": self.jobs_scored,
            "docs_generated": self.docs_generated,
            "emails_sent": self.emails_sent,
            "follow_ups_sent": self.follow_ups_sent,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> Run:
        return cls(
            id=doc["_id"],
            user_id=doc["user_id"],
            started_at=doc.get("started_at"),
            finished_at=doc.get("finished_at"),
            jobs_found=doc.get("jobs_found", 0),
            jobs_scored=doc.get("jobs_scored", 0),
            docs_generated=doc.get("docs_generated", 0),
            emails_sent=doc.get("emails_sent", 0),
            follow_ups_sent=doc.get("follow_ups_sent", 0),
            status=doc.get("status", "running"),
            cancel_requested=doc.get("cancel_requested", False),
        )
