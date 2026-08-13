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

from ..models import Job
from . import mailer
from . import repository as repo
from .net_safety import assert_safe_host
from .runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MSGID_RE = re.compile(r"<[^>\s]+>")
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery")
_BOUNCE_SUBJECTS = ("delivery status", "undeliverable", "delivery failure",
                    "returned mail", "failure notice", "mail delivery failed")

_FETCH_FIELDS = "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES)])"


def imap_ready(cfg: RuntimeConfig) -> bool:
    i = cfg.imap
    return bool(i.get("host") and i.get("user") and i.get("password"))


def _mark_replied(user_id: str, job: Job) -> None:
    now = datetime.now(UTC)
    job.reply_detected = True
    job.reply_detected_at = now
    repo.update_job(user_id, job.id, reply_detected=True, reply_detected_at=now)


def detect_replies(cfg: RuntimeConfig, emit=None) -> dict:
    """
    Scan the user's inbox and update jobs.

    Matching, most-reliable first:
      1. Thread match — the message's In-Reply-To/References contains the exact
         Message-ID we set when sending (a genuine reply to our application).
      2. Sender match — the message is from the contacted address (fallback for
         mailboxes/servers that drop threading headers).
    Bounces (from mailer-daemon/postmaster, or delivery-failure subjects) that
    reference our Message-ID mark the job as ``bounced`` (not delivered).

    Returns {"replies": n, "bounces": n}.
    """
    def log(m):
        logger.info(m)
        if emit:
            emit(m)

    if not imap_ready(cfg):
        log("  ℹ IMAP not configured — skipping reply detection")
        return {"replies": 0, "bounces": 0}

    # Jobs still awaiting an outcome: sent, not yet replied, not yet bounced.
    pending = repo.get_jobs_pending_reply_check(cfg.user_id)

    contact_map: dict[str, list[Job]] = {}
    msgid_map: dict[str, list[Job]] = {}
    for job in pending:
        for addr in (job.hr_email, job.application_email):
            if addr:
                contact_map.setdefault(addr.lower(), []).append(job)
        if job.email_message_id:
            msgid_map.setdefault(job.email_message_id.strip(), []).append(job)
    if not contact_map and not msgid_map:
        return {"replies": 0, "bounces": 0}

    i = cfg.imap
    replies = bounces = 0
    try:
        assert_safe_host(i["host"])
        conn = imaplib.IMAP4_SSL(i["host"], int(i.get("port") or 993))
        conn.login(i["user"], i["password"])
        conn.select("INBOX")
        typ, data = conn.search(None, "ALL")
        ids = data[0].split()[-500:] if data and data[0] else []

        for mid in reversed(ids):
            typ, msg_data = conn.fetch(mid, _FETCH_FIELDS)
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            header = msg_data[0][1].decode("utf-8", "ignore")
            hl = header.lower()
            from_addrs = {a.lower() for a in _EMAIL_RE.findall(_field(header, "from"))}
            refs = set(_MSGID_RE.findall(_field(header, "references")
                                         + " " + _field(header, "in-reply-to")))

            is_bounce = (any(s in hl for s in _BOUNCE_SENDERS)
                         or any(s in _field(hl, "subject") for s in _BOUNCE_SUBJECTS))

            # Which of our jobs does this message concern? Thread first.
            hit_jobs: list[Job] = []
            for ref in refs & set(msgid_map):
                hit_jobs.extend(msgid_map[ref])
            if not hit_jobs and not is_bounce:
                for addr in from_addrs & set(contact_map):
                    hit_jobs.extend(contact_map[addr])

            if is_bounce and not hit_jobs:
                # Bounces often omit clean threading headers — pull the body and
                # look for our Message-ID or a contacted address.
                hit_jobs = _match_bounce_body(conn, mid, msgid_map, contact_map)

            for job in hit_jobs:
                if is_bounce and not job.bounced:
                    job.bounced = True
                    job.email_status = "bounced"
                    repo.update_job(cfg.user_id, job.id, bounced=True, email_status="bounced")
                    bounces += 1
                    log(f"  ↩ Bounce → {job.company} ({job.title}) not delivered")
                elif not is_bounce and not job.reply_detected:
                    _mark_replied(cfg.user_id, job)
                    replies += 1
                    log(f"  📩 Reply → {job.company} ({job.title})")

        conn.logout()
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠ IMAP error: {exc}")
    return {"replies": replies, "bounces": bounces}


def _field(header: str, name: str) -> str:
    """Return the value of a single header field from a small header blob."""
    out, capture = [], False
    for line in header.splitlines():
        low = line.lower()
        if low.startswith(name + ":"):
            capture = True
            out.append(line.split(":", 1)[1])
        elif capture and (line.startswith(" ") or line.startswith("\t")):
            out.append(line)
        elif capture:
            break
    return " ".join(out)


def _match_bounce_body(conn, mid, msgid_map, contact_map) -> list[Job]:
    try:
        typ, data = conn.fetch(mid, "(BODY.PEEK[TEXT])")
        if typ != "OK" or not data or not data[0]:
            return []
        body = data[0][1].decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return []
    hits: list[Job] = []
    for ref in set(_MSGID_RE.findall(body)) & set(msgid_map):
        hits.extend(msgid_map[ref])
    if not hits:
        addrs = {a.lower() for a in _EMAIL_RE.findall(body)}
        for addr in addrs & set(contact_map):
            hits.extend(contact_map[addr])
    return hits


def run_follow_up_cycle(cfg: RuntimeConfig, emit=None) -> dict:
    """Detect replies, then follow up only with non-responders."""
    def log(m):
        logger.info(m)
        if emit:
            emit(m)

    detection = detect_replies(cfg, emit=emit)
    replies, bounces = detection["replies"], detection["bounces"]

    if not cfg.follow_up_enabled:
        return {"replies": replies, "bounces": bounces, "follow_ups": 0}

    due = repo.get_jobs_needing_follow_up(cfg.user_id, follow_up_days=cfg.follow_up_days)
    sent = 0
    for job in due:
        # Never chase a reply (user handles it) or an address that bounced.
        if job.reply_detected or job.bounced:
            continue
        result = mailer.send_follow_up(cfg, job)
        if result.ok:
            now = datetime.now(UTC)
            repo.update_job(cfg.user_id, job.id, follow_up_status="sent", follow_up_sent_at=now)
            sent += 1
            log(f"  📨 {result.message}")
        else:
            log(f"  ⚠ {result.message}")
    log(f"✅ Follow-up cycle — {replies} reply(ies), {bounces} bounce(s), "
        f"{sent} follow-up(s) sent")
    return {"replies": replies, "bounces": bounces, "follow_ups": sent}
