"""
follow_up_scheduler.py — Daily follow-up engine for Job Hunter.

Runs two tasks:
  1. Reply detection — scans your Gmail SENT folder via IMAP to check
     whether any application thread has received a reply. If detected,
     marks the job reply_detected=1 so no follow-up is sent.

  2. Follow-up sender — for jobs that were sent N days ago (default 6),
     have no reply detected, and haven't had a follow-up yet, sends a
     short polite follow-up email.

Usage:
  # Run once manually
  python follow_up_scheduler.py

  # Or call from the Flask API / cron:
  from follow_up_scheduler import run_follow_up_cycle
  run_follow_up_cycle()

Environment variables (all optional — feature degrades gracefully):
  FOLLOW_UP_DAYS    Days after first send before follow-up fires (default 6)
  FOLLOW_UP_ENABLED true/false (default true)
  IMAP_HOST         IMAP server for reply detection (default: imap.gmail.com)
  IMAP_PORT         IMAP port (default: 993)
  IMAP_USER         Usually same as SMTP_USER
  IMAP_PASSWORD     Usually same as SMTP_PASSWORD / Gmail App Password
"""

import imaplib
import email
import logging
import threading
from datetime import datetime, timezone
from email.header import decode_header

from config import config
from database import (
    get_jobs_needing_follow_up,
    update_job,
    get_all_jobs,
)
from mailer import send_follow_up, smtp_configured

logger = logging.getLogger(__name__)

# ── Scheduler state ──────────────────────────────────────
_scheduler_lock = threading.Lock()
_scheduler_running = False


# ────────────────────────────────────────────────────────
# Reply detection via IMAP
# ────────────────────────────────────────────────────────

def _imap_configured() -> bool:
    return bool(
        getattr(config, "IMAP_HOST", "") and
        getattr(config, "IMAP_USER", config.SMTP_USER) and
        getattr(config, "IMAP_PASSWORD", config.SMTP_PASSWORD)
    )


def _decode_str(raw) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def detect_replies(emit=None) -> int:
    """
    Connect to IMAP INBOX and look for replies to our sent applications.
    A reply is identified by matching the From address in the jobs table
    against emails in the INBOX.

    Returns the number of new replies detected.
    """
    def log(msg):
        logger.info(msg)
        if emit:
            emit(msg)

    if not _imap_configured():
        log("  ℹ️  IMAP not configured — skipping reply detection")
        return 0

    imap_host = getattr(config, "IMAP_HOST", "imap.gmail.com")
    imap_port = int(getattr(config, "IMAP_PORT", 993))
    imap_user = getattr(config, "IMAP_USER", config.SMTP_USER)
    imap_pass = getattr(config, "IMAP_PASSWORD", config.SMTP_PASSWORD)

    # Build lookup: email address → list of job_ids we sent to that address
    sent_jobs = [
        j for j in get_all_jobs(500)
        if j.get("email_status") == "sent" and j.get("reply_detected") == 0
    ]
    if not sent_jobs:
        return 0

    # Map sender email → job ids
    email_to_jobs: dict[str, list[str]] = {}
    for j in sent_jobs:
        addr = (j.get("hr_email") or j.get("application_email") or "").lower()
        if addr:
            email_to_jobs.setdefault(addr, []).append(j["id"])

    if not email_to_jobs:
        return 0

    new_replies = 0
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(imap_user, imap_pass)
        mail.select("INBOX")

        # Search last 60 days
        _, data = mail.search(None, 'SINCE "01-Apr-2025"')
        msg_ids = data[0].split()
        log(f"  📬 Scanning {len(msg_ids)} inbox messages for replies…")

        for num in msg_ids[-500:]:  # cap at 500 most recent
            try:
                _, msg_data = mail.fetch(num, "(RFC822.HEADER)")
                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw)
                from_addr = _decode_str(parsed.get("From", "")).lower()

                for sender_email, job_ids in email_to_jobs.items():
                    if sender_email in from_addr:
                        for jid in job_ids:
                            update_job(jid, reply_detected=1)
                            new_replies += 1
                        log(f"  💬 Reply detected from {sender_email}")
            except Exception:
                continue

        mail.logout()
    except Exception as e:
        log(f"  ⚠ IMAP error during reply detection: {e}")

    return new_replies


# ────────────────────────────────────────────────────────
# Main follow-up cycle
# ────────────────────────────────────────────────────────

def run_follow_up_cycle(emit=None) -> dict:
    """
    Full follow-up cycle:
      1. Detect replies (IMAP)
      2. Send follow-ups to eligible jobs

    Returns summary dict.
    """
    global _scheduler_running

    with _scheduler_lock:
        if _scheduler_running:
            return {"error": "Follow-up cycle already running"}
        _scheduler_running = True

    def log(msg):
        logger.info(msg)
        if emit:
            emit(msg)

    summary = {"replies_detected": 0, "follow_ups_sent": 0, "follow_ups_skipped": 0}

    try:
        follow_up_enabled = str(getattr(config, "FOLLOW_UP_ENABLED", "true")).lower() == "true"
        if not follow_up_enabled:
            log("  ℹ️  Follow-ups disabled (FOLLOW_UP_ENABLED=false)")
            return summary

        log("🔎 Starting follow-up cycle…")

        # Step 1: reply detection
        replies = detect_replies(emit=emit)
        summary["replies_detected"] = replies
        if replies:
            log(f"  ✅ {replies} new repl{'y' if replies == 1 else 'ies'} detected — those jobs will not get follow-ups")

        # Step 2: collect eligible jobs
        follow_up_days = int(getattr(config, "FOLLOW_UP_DAYS", 6))
        eligible = get_jobs_needing_follow_up(follow_up_days=follow_up_days)
        log(f"  📋 {len(eligible)} job(s) eligible for follow-up (sent {follow_up_days}+ days ago, no reply)")

        if not eligible:
            log("  ✓ Nothing to follow up on")
            return summary

        if not smtp_configured():
            log("  ⚠ SMTP not configured — cannot send follow-ups")
            return summary

        # Step 3: send follow-ups
        for job in eligible:
            sent = send_follow_up(job, emit=emit)
            if sent:
                summary["follow_ups_sent"] += 1
            else:
                summary["follow_ups_skipped"] += 1

        log(
            f"\n📨 Follow-up cycle done — "
            f"{summary['follow_ups_sent']} sent, "
            f"{summary['follow_ups_skipped']} skipped, "
            f"{summary['replies_detected']} replies detected"
        )

    except Exception as e:
        log(f"❌ Follow-up cycle error: {e}")
        logger.exception("Follow-up cycle error")
    finally:
        with _scheduler_lock:
            _scheduler_running = False

    return summary


# ────────────────────────────────────────────────────────
# CLI entry point
# ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = run_follow_up_cycle(emit=print)
    print(f"\nSummary: {result}")
