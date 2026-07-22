"""
Per-user reply detection and follow-ups.

Reply detection connects to *the user's own* IMAP mailbox (from their Settings),
scans recent inbox messages, and marks any job whose HR contact has replied.
Policy, per the product spec:

- If a recruiter has **replied**, we do NOT auto-follow-up. The job is flagged
  ``reply_detected`` and surfaced on the dashboard so the user follows up
  personally.
- For contacts that have NOT replied after ``follow_up_days``, one polite
  follow-up is sent automatically, and ``follow_up_status`` is set to ``sent``
  so it never fires twice.
"""

from __future__ import annotations

import imaplib
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select

from ..extensions import db
from ..models import Job
from . import mailer
from . import repository as repo
from .runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def imap_ready(cfg: RuntimeConfig) -> bool:
    i = cfg.imap
    return bool(i.get("host") and i.get("user") and i.get("password"))


def detect_replies(cfg: RuntimeConfig, emit=None) -> int:
    """Scan the user's inbox; mark replied jobs. Returns count of new replies."""
    def log(m):
        logger.info(m)
        if emit:
            emit(m)

    if not imap_ready(cfg):
        log("  ℹ IMAP not configured — skipping reply detection")
        return 0

    # Contacts we're still waiting to hear from.
    pending = db.session.scalars(
        select(Job).where(
            Job.user_id == cfg.user_id,
            Job.email_status == "sent",
            Job.reply_detected.is_(False),
        )
    ).all()
    contact_map: dict[str, list[Job]] = {}
    for job in pending:
        for addr in (job.hr_email, job.application_email):
            if addr:
                contact_map.setdefault(addr.lower(), []).append(job)
    if not contact_map:
        return 0

    i = cfg.imap
    found = 0
    try:
        conn = imaplib.IMAP4_SSL(i["host"], int(i.get("port") or 993))
        conn.login(i["user"], i["password"])
        conn.select("INBOX")
        # Look at recent messages only.
        typ, data = conn.search(None, "ALL")
        ids = data[0].split()[-500:] if data and data[0] else []
        for mid in reversed(ids):
            typ, msg_data = conn.fetch(mid, "(BODY[HEADER.FIELDS (FROM)])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            header = msg_data[0][1].decode("utf-8", "ignore")
            addrs = {a.lower() for a in _EMAIL_RE.findall(header)}
            for addr in addrs & set(contact_map):
                for job in contact_map[addr]:
                    if not job.reply_detected:
                        job.reply_detected = True
                        found += 1
                        log(f"  📩 Reply from {addr} → {job.company} ({job.title})")
        db.session.commit()
        conn.logout()
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠ IMAP error: {exc}")
    return found


def run_follow_up_cycle(cfg: RuntimeConfig, emit=None) -> dict:
    """Detect replies, then follow up only with non-responders."""
    def log(m):
        logger.info(m)
        if emit:
            emit(m)

    replies = detect_replies(cfg, emit=emit)

    if not cfg.follow_up_enabled:
        return {"replies": replies, "follow_ups": 0}

    due = repo.get_jobs_needing_follow_up(cfg.user_id, follow_up_days=cfg.follow_up_days)
    sent = 0
    for job in due:
        if job.reply_detected:
            continue  # replied → user handles manually, never auto-follow-up
        result = mailer.send_follow_up(cfg, job)
        if result.ok:
            job.follow_up_status = "sent"
            job.follow_up_sent_at = datetime.now(UTC)
            sent += 1
            log(f"  📨 {result.message}")
        else:
            log(f"  ⚠ {result.message}")
    db.session.commit()
    log(f"✅ Follow-up cycle — {replies} reply(ies), {sent} follow-up(s) sent")
    return {"replies": replies, "follow_ups": sent}
