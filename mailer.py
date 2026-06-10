"""
mailer.py — SMTP email sender for Job Hunter.

Improvements in this version:
  * Follow-up emails — auto-sends a polite nudge after FOLLOW_UP_DAYS
    (default 6) if no reply has been detected yet.
  * Duplicate email guard — skips sending if the same HR address
    already received an application within DEDUP_WINDOW_DAYS (default 30).
  * Grouped multi-role sends — if the pipeline finds N jobs at the same
    company with the same HR contact, they are batched into one email
    listing all matching roles rather than N separate messages.
  * Gmail SMTP supported out of the box (port 587 + STARTTLS).
  * RFC headers, proper MIME types, throttle, hard-bounce detection
    all retained from previous version.
"""

import time
import uuid
import socket
import logging
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.utils import formatdate, make_msgid, formataddr
from email.header import Header
from email import encoders
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import config
from database import update_job, email_already_sent_to

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Throttle (process-wide)
# ──────────────────────────────────────────────────────────
_throttle_lock = threading.Lock()
_last_send_at = 0.0


def _wait_throttle():
    global _last_send_at
    delay = max(0, getattr(config, "SMTP_THROTTLE_SECONDS", 8))
    if delay <= 0:
        return
    with _throttle_lock:
        now = time.time()
        wait = (_last_send_at + delay) - now
        if wait > 0:
            logger.info(f"[Mailer] Throttle: sleeping {wait:.1f}s before next send")
            time.sleep(wait)
        _last_send_at = time.time()


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def smtp_configured() -> bool:
    return bool(
        config.SMTP_HOST
        and config.SMTP_USER
        and config.SMTP_PASSWORD
        and config.SMTP_FROM
    )


def _build_smtp():
    host = config.SMTP_HOST
    port = config.SMTP_PORT
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        if config.SMTP_TLS:
            server.starttls()
            server.ehlo()
    server.login(config.SMTP_USER, config.SMTP_PASSWORD)
    return server


_MIME_BY_EXT = {
    ".pdf":  ("application", "pdf"),
    ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".doc":  ("application", "msword"),
    ".txt":  ("text", "plain"),
}


def _get_attachments(output_dir: str) -> list[Path]:
    folder = Path(output_dir)
    if not folder.exists():
        return []
    attach: list[Path] = []
    for stem in ["CV", "CoverLetter"]:
        if config.SMTP_ATTACH_PDF:
            p = folder / f"{stem}.pdf"
            if p.exists():
                attach.append(p)
        if config.SMTP_ATTACH_DOCX:
            d = folder / f"{stem}.docx"
            if d.exists():
                attach.append(d)
    return attach


def _read_draft(output_dir: str) -> tuple[str, str]:
    draft = Path(output_dir) / "EMAIL_DRAFT.txt"
    if not draft.exists():
        return "Job Application", "Please find my attached application documents."

    content = draft.read_text(encoding="utf-8")
    subject = ""
    body_lines: list[str] = []
    in_body = False
    skip_next_separator = False

    for line in content.splitlines():
        if "SUBJECT:" in line and not subject:
            subject = line.split("SUBJECT:", 1)[1].strip()
            continue
        if "EMAIL BODY" in line and "copy" in line.lower():
            in_body = True
            skip_next_separator = True
            continue
        if in_body:
            is_separator = line.startswith("─" * 10) or line.startswith("-" * 10)
            if is_separator and skip_next_separator:
                skip_next_separator = False
                continue
            if is_separator and body_lines:
                break
            if "MATCH ANALYSIS" in line:
                break
            skip_next_separator = False
            body_lines.append(line)

    body = "\n".join(body_lines).strip().replace("\\n", "\n")
    return (subject or "Job Application"), (body or "Please find my attached application documents.")


def _safe_subject(subject: str) -> str:
    try:
        subject.encode("ascii")
        return subject
    except UnicodeEncodeError:
        return str(Header(subject, charset="utf-8"))


def _body_to_html(plain: str) -> str:
    import html as html_mod
    escaped = html_mod.escape(plain)
    paragraphs = escaped.split("\n\n")
    parts = []
    for para in paragraphs:
        lines = para.strip().splitlines()
        if not lines:
            continue
        parts.append("<p>" + "<br>".join(lines) + "</p>")
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<style>body{font-family:Calibri,Arial,sans-serif;font-size:11pt;"
        "color:#111;line-height:1.6}p{margin:0 0 12px 0}</style></head>"
        f"<body>{''.join(parts)}</body></html>"
    )


def _attach_file(outer: MIMEMultipart, filepath: Path):
    ext = filepath.suffix.lower()
    maintype, subtype = _MIME_BY_EXT.get(ext, ("application", "octet-stream"))
    try:
        with open(filepath, "rb") as f:
            part = MIMEBase(maintype, subtype)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=("utf-8", "", filepath.name),
        )
        outer.attach(part)
    except Exception as e:
        logger.warning(f"[Mailer] Could not attach {filepath.name}: {e}")


def _build_message(to_email: str, subject: str, body: str,
                   attachments: list[Path]) -> MIMEMultipart:
    fmt = (getattr(config, "SMTP_FORMAT", "plain") or "plain").lower()
    outer = MIMEMultipart("mixed")
    outer["From"]         = formataddr((str(Header(config.CANDIDATE_NAME, "utf-8")),
                                        config.SMTP_FROM))
    outer["To"]           = to_email
    outer["Subject"]      = _safe_subject(subject)
    outer["Reply-To"]     = config.SMTP_FROM
    outer["Date"]         = formatdate(localtime=True)
    sender_domain         = config.SMTP_FROM.split("@", 1)[-1] if "@" in config.SMTP_FROM else "localhost"
    outer["Message-ID"]   = make_msgid(domain=sender_domain)
    outer["MIME-Version"] = "1.0"
    outer["X-Mailer"]     = "JobHunter/2.0"

    if fmt == "mixed":
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain", "utf-8"))
        alt.attach(MIMEText(_body_to_html(body), "html", "utf-8"))
        outer.attach(alt)
    else:
        outer.attach(MIMEText(body, "plain", "utf-8"))

    for filepath in attachments:
        _attach_file(outer, filepath)

    return outer


# ──────────────────────────────────────────────────────────
# Hard-fail detection
# ──────────────────────────────────────────────────────────
_HARD_FAIL_FRAGMENTS = (
    "user unknown", "no such user", "mailbox not found",
    "mailbox unavailable", "address rejected",
    "recipient address rejected", "domain not found",
    "relay access denied", "not exist",
)


def _is_hard_fail(error_text: str) -> bool:
    e = (error_text or "").lower()
    return any(f in e for f in _HARD_FAIL_FRAGMENTS)


# ──────────────────────────────────────────────────────────
# Core send (with retry)
# ──────────────────────────────────────────────────────────

def send_application(
    to_email: str,
    output_dir: str,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    skip_dedup_check: bool = False,
) -> tuple[bool, str]:
    """
    Send application email with attachments.
    Checks duplicate guard unless skip_dedup_check=True.
    Retries SMTP_RETRY_COUNT times on transient failure.
    Returns (success, message).
    """
    if not smtp_configured():
        return False, "SMTP not configured — set password in Settings"

    # ── Duplicate guard ───────────────────────────────────
    if not skip_dedup_check:
        dedup_days = getattr(config, "DEDUP_WINDOW_DAYS", 30)
        if email_already_sent_to(to_email, within_days=dedup_days):
            return False, (
                f"Duplicate guard: already sent to {to_email} "
                f"within the last {dedup_days} days — skipped"
            )

    attachments = _get_attachments(output_dir)
    if not attachments:
        return False, f"No attachment files found in: {output_dir}"

    if not subject or not body:
        ds, db = _read_draft(output_dir)
        subject = subject or ds
        body    = body or db

    if not body.strip():
        body = "Please find my attached application documents."

    msg = _build_message(to_email, subject, body, attachments)

    logger.info(f"[Mailer] Sending to: {to_email}")
    logger.info(f"[Mailer] Subject: {subject}")
    logger.info(f"[Mailer] Attachments: {[a.name for a in attachments]}")

    _wait_throttle()

    last_error = ""
    for attempt in range(1, config.SMTP_RETRY_COUNT + 2):
        try:
            server = _build_smtp()
            server.sendmail(config.SMTP_FROM, [to_email], msg.as_string())
            server.quit()
            files = ", ".join(f.name for f in attachments)
            return True, f"Sent to {to_email} with: {files}"

        except smtplib.SMTPAuthenticationError:
            return False, "SMTP authentication failed — check username/password in Settings"

        except smtplib.SMTPRecipientsRefused as e:
            return False, f"Recipient refused: {to_email} ({e})"

        except smtplib.SMTPDataError as e:
            last_error = f"{e.smtp_code} {e.smtp_error.decode('utf-8', 'replace') if isinstance(e.smtp_error, bytes) else e.smtp_error}"
            if _is_hard_fail(last_error):
                return False, f"Hard bounce — not retrying: {last_error}"
            logger.warning(f"[Mailer] Attempt {attempt} SMTP error: {last_error}")

        except (smtplib.SMTPSenderRefused, smtplib.SMTPHeloError) as e:
            return False, f"SMTP rejected: {e}"

        except (socket.timeout, smtplib.SMTPConnectError) as e:
            last_error = f"Connection error: {e}"
            logger.warning(f"[Mailer] Attempt {attempt} connection issue: {e}")

        except Exception as e:
            last_error = str(e)
            if _is_hard_fail(last_error):
                return False, f"Hard bounce — not retrying: {last_error}"
            logger.warning(f"[Mailer] Attempt {attempt} failed: {last_error}")

        if attempt <= config.SMTP_RETRY_COUNT:
            time.sleep(5 * attempt)

    return False, f"Failed after {config.SMTP_RETRY_COUNT + 1} attempt(s): {last_error}"


# ──────────────────────────────────────────────────────────
# Follow-up sender
# ──────────────────────────────────────────────────────────

def send_follow_up(job: dict, emit=None) -> bool:
    """
    Send a single polite follow-up for a job application.
    Should only be called after the duplicate/reply checks in the scheduler.
    """
    def log(msg: str):
        logger.info(msg)
        if emit:
            emit(msg)

    job_id    = job.get("id", "")
    to_email  = job.get("hr_email") or job.get("application_email") or ""
    company   = job.get("company", "the company")
    title     = job.get("title", "the role")
    name      = config.CANDIDATE_NAME

    if not to_email:
        return False

    subject = f"Re: {name} — {title} Application Follow-Up"
    body = (
        f"Hi,\n\n"
        f"I wanted to follow up on my application for the {title} position at {company}, "
        f"which I submitted about a week ago.\n\n"
        f"I'm still very interested in the opportunity and would love the chance to discuss "
        f"how my background aligns with what you're looking for. Please let me know if you "
        f"need any additional information.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"Best regards,\n{name}"
    )

    if not smtp_configured():
        log(f"  ⚠ SMTP not configured — cannot send follow-up to {company}")
        return False

    _wait_throttle()

    try:
        msg = _build_message(to_email, subject, body, [])
        server = _build_smtp()
        server.sendmail(config.SMTP_FROM, [to_email], msg.as_string())
        server.quit()
        update_job(
            job_id,
            follow_up_status="sent",
            follow_up_sent_at=datetime.utcnow().isoformat(),
        )
        log(f"  📨 Follow-up sent → {company} ({to_email})")
        return True
    except Exception as e:
        update_job(job_id, follow_up_status="failed")
        log(f"  ❌ Follow-up failed for {company}: {e}")
        return False


# ──────────────────────────────────────────────────────────
# Grouped multi-role send
# ──────────────────────────────────────────────────────────

def group_jobs_by_contact(jobs: list[dict]) -> list[list[dict]]:
    """
    Group jobs that share the same HR email into batches.
    Jobs without an email are returned as individual single-item groups.
    """
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    no_email: list[list[dict]] = []

    for job in jobs:
        email = job.get("_contact", {}).get("hr_email") or \
                job.get("_contact", {}).get("application_email") or \
                job.get("hr_email") or job.get("application_email") or ""
        if email:
            grouped[email].append(job)
        else:
            no_email.append([job])

    return list(grouped.values()) + no_email


def send_grouped_application(jobs: list[dict], emit=None) -> tuple[int, int]:
    """
    Send one application email for a group of jobs at the same company/contact.
    If the group has multiple jobs, lists all matching roles in a single email.
    Returns (sent_count, skipped_count).
    """
    def log(msg):
        logger.info(msg)
        if emit:
            emit(msg)

    if not jobs:
        return 0, 0

    # Use the first job's contact info as the "to" address
    first = jobs[0]
    contact = first.get("_contact", {})
    to_email = (
        contact.get("hr_email") or contact.get("application_email") or
        first.get("hr_email") or first.get("application_email") or ""
    )

    if not to_email:
        return 0, len(jobs)

    # Duplicate guard on the whole group
    dedup_days = getattr(config, "DEDUP_WINDOW_DAYS", 30)
    if email_already_sent_to(to_email, within_days=dedup_days):
        log(f"  ⚠ Duplicate guard: {to_email} already contacted — skipping {len(jobs)} job(s)")
        return 0, len(jobs)

    sent = 0
    skipped = 0

    if len(jobs) == 1:
        # Single job — normal individual send
        job = jobs[0]
        output_dir = job.get("output_dir") or job.get("_output_dir", "")
        if not output_dir:
            return 0, 1
        ok, msg = send_application(to_email, output_dir, skip_dedup_check=True)
        if ok:
            update_job(
                job["_job_id"],
                email_status="sent",
                email_sent_at=datetime.utcnow().isoformat(),
                email_error="",
                status="applied",
            )
            log(f"  ✅ {msg}")
            sent = 1
        else:
            update_job(job["_job_id"], email_status="failed", email_error=msg)
            log(f"  ❌ {msg}")
            skipped = 1
    else:
        # Multi-role — build a combined email
        company = first.get("company", "your company")
        hr_name = contact.get("hr_name") or ""
        greeting = f"Hi {hr_name}," if hr_name else "Hi,"

        roles_lines = "\n".join(
            f"  • {j.get('title', 'Role')} — {j.get('url', '')}"
            for j in jobs
        )
        subject = f"{config.CANDIDATE_NAME} — Application for {len(jobs)} Roles at {company}"
        body = (
            f"{greeting}\n\n"
            f"I came across {len(jobs)} open positions at {company} that align closely with "
            f"my background and I'd like to apply for all of them:\n\n"
            f"{roles_lines}\n\n"
            f"I've attached my CV which covers all of these roles. I'd be happy to tailor a "
            f"cover letter for whichever position is the best fit — just let me know.\n\n"
            f"Thank you for your time.\n\n"
            f"Best regards,\n{config.CANDIDATE_NAME}"
        )

        # Collect attachments from all output dirs (CV only, deduplicated)
        all_attachments: list[Path] = []
        seen_names: set[str] = set()
        for job in jobs:
            output_dir = job.get("output_dir") or job.get("_output_dir", "")
            if output_dir:
                for att in _get_attachments(output_dir):
                    if att.name not in seen_names:
                        all_attachments.append(att)
                        seen_names.add(att.name)
                    break  # one CV per job is enough for multi-role

        if not all_attachments:
            log(f"  ⚠ No attachments found for grouped send to {company}")
            return 0, len(jobs)

        msg = _build_message(to_email, subject, body, all_attachments)
        _wait_throttle()

        try:
            server = _build_smtp()
            server.sendmail(config.SMTP_FROM, [to_email], msg.as_string())
            server.quit()
            now_str = datetime.utcnow().isoformat()
            for job in jobs:
                update_job(
                    job["_job_id"],
                    email_status="sent",
                    email_sent_at=now_str,
                    email_error="",
                    status="applied",
                )
            log(f"  ✅ Grouped send → {company} ({len(jobs)} roles) to {to_email}")
            sent = len(jobs)
        except Exception as e:
            err = str(e)
            for job in jobs:
                update_job(job["_job_id"], email_status="failed", email_error=err)
            log(f"  ❌ Grouped send failed for {company}: {e}")
            skipped = len(jobs)

    return sent, skipped


# ──────────────────────────────────────────────────────────
# Pipeline helper
# ──────────────────────────────────────────────────────────

def send_for_job(job: dict, emit=None) -> bool:
    def log(msg: str):
        logger.info(msg)
        if emit:
            emit(msg)

    job_id     = job.get("_job_id") or job.get("id", "")
    output_dir = job.get("output_dir") or job.get("_output_dir", "")
    contact    = job.get("_contact") or {}

    to_email = (
        contact.get("hr_email") or contact.get("application_email") or
        job.get("hr_email") or job.get("application_email") or ""
    )

    if not to_email:
        log(f"  📭 No email address for {job.get('company')} — skipping auto-send")
        update_job(job_id, email_status="no_contact")
        return False

    if not output_dir or not Path(output_dir).exists():
        log(f"  ⚠ Output folder missing for {job.get('company')} — skipping auto-send")
        update_job(job_id, email_status="no_files")
        return False

    log(f"  📧 Sending to {to_email} ({job.get('company')})…")
    ok, msg = send_application(to_email, output_dir)

    if ok:
        update_job(
            job_id,
            email_status="sent",
            email_sent_at=datetime.utcnow().isoformat(),
            email_error="",
            status="applied",
        )
        log(f"  ✅ {msg}")
    else:
        update_job(job_id, email_status="failed", email_error=msg)
        log(f"  ❌ {msg}")

    return ok


# ──────────────────────────────────────────────────────────
# SMTP connection test
# ──────────────────────────────────────────────────────────

def test_smtp() -> tuple[bool, str]:
    if not smtp_configured():
        return False, "SMTP settings incomplete — fill in host, user, password and from address"
    try:
        server = _build_smtp()
        server.quit()
        return True, f"✅ Connected to {config.SMTP_HOST}:{config.SMTP_PORT} and authenticated successfully"
    except smtplib.SMTPAuthenticationError:
        return False, "❌ Authentication failed — check your username and password"
    except Exception as e:
        return False, f"❌ {e}"
