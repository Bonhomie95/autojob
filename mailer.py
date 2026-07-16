"""
mailer.py — API email sender for Auto Job.

Improvements in this version:
  * Follow-up emails — auto-sends a polite nudge after FOLLOW_UP_DAYS
    (default 6) if no reply has been detected yet.
  * Duplicate email guard — skips sending if the same HR address
    already received an application within DEDUP_WINDOW_DAYS (default 30).
  * Grouped multi-role sends — if the pipeline finds N jobs at the same
    company with the same HR contact, they are batched into one email
    listing all matching roles rather than N separate messages.
  * API sender rotation supports Brevo and SMTP2GO.
  * Reply-To, attachments, throttle, hard-bounce detection retained.
"""

import base64
import time
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional
import requests

from config import config
from database import update_job, email_already_sent_to, emails_sent_today

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Throttle (process-wide)
# ──────────────────────────────────────────────────────────
_throttle_lock = threading.Lock()
_last_send_at = 0.0
_provider_lock = threading.Lock()
_dead_providers: set[str] = set()
_provider_index = 0


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
    return bool(getattr(config, "EMAIL_PROVIDERS", []))


def _next_email_provider() -> dict | None:
    global _provider_index
    providers = getattr(config, "EMAIL_PROVIDERS", [])
    with _provider_lock:
        live = [p for p in providers if p.get("name") not in _dead_providers]
        if not live:
            return None
        _provider_index %= len(live)
        provider = live[_provider_index]
        _provider_index = (_provider_index + 1) % len(live)
        return provider


def _mark_provider_dead(provider: dict, reason: str):
    name = provider.get("name", "smtp")
    with _provider_lock:
        _dead_providers.add(name)
    logger.warning(f"[Mailer] Provider '{name}' disabled for this session: {reason}")


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


def _encoded_attachments(attachments: list[Path]) -> list[dict]:
    encoded = []
    for path in attachments:
        try:
            content = base64.b64encode(path.read_bytes()).decode("ascii")
            maintype, subtype = _MIME_BY_EXT.get(path.suffix.lower(), ("application", "octet-stream"))
            encoded.append({
                "name": path.name,
                "content": content,
                "mime": f"{maintype}/{subtype}",
            })
        except Exception as e:
            logger.warning(f"[Mailer] Could not attach {path.name}: {e}")
    return encoded


def _send_brevo(provider: dict, to_email: str, subject: str, body: str,
                attachments: list[Path]) -> tuple[bool, str]:
    reply_to = getattr(config, "SMTP_REPLY_TO", "") or config.CANDIDATE_EMAIL
    payload = {
        "sender": {"name": provider.get("from_name") or config.CANDIDATE_NAME, "email": provider["from"]},
        "to": [{"email": to_email}],
        "replyTo": {"email": reply_to},
        "subject": subject,
        "textContent": body,
    }
    if getattr(config, "SMTP_FORMAT", "plain") == "mixed":
        payload["htmlContent"] = _body_to_html(body)
    encoded = _encoded_attachments(attachments)
    if encoded:
        payload["attachment"] = [{"name": a["name"], "content": a["content"]} for a in encoded]

    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": provider["api_key"], "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.status_code in (200, 201, 202):
        return True, "Brevo accepted message"
    return False, f"Brevo {resp.status_code}: {resp.text[:300]}"


def _send_smtp2go(provider: dict, to_email: str, subject: str, body: str,
                  attachments: list[Path]) -> tuple[bool, str]:
    reply_to = getattr(config, "SMTP_REPLY_TO", "") or config.CANDIDATE_EMAIL
    payload = {
        "sender": f"{provider.get('from_name') or config.CANDIDATE_NAME} <{provider['from']}>",
        "to": [to_email],
        "subject": subject,
        "text_body": body,
        "custom_headers": [{"header": "Reply-To", "value": reply_to}],
    }
    if getattr(config, "SMTP_FORMAT", "plain") == "mixed":
        payload["html_body"] = _body_to_html(body)
    encoded = _encoded_attachments(attachments)
    if encoded:
        payload["attachments"] = [
            {"filename": a["name"], "fileblob": a["content"], "mimetype": a["mime"]}
            for a in encoded
        ]

    resp = requests.post(
        "https://api.smtp2go.com/v3/email/send",
        headers={"X-Smtp2go-Api-Key": provider["api_key"], "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.status_code == 200:
        data = resp.json()
        failures = data.get("data", {}).get("failures", 0)
        if failures:
            return False, f"SMTP2GO failures: {data}"
        return True, "SMTP2GO accepted message"
    return False, f"SMTP2GO {resp.status_code}: {resp.text[:300]}"


def _send_via_provider(provider: dict, to_email: str, subject: str, body: str,
                       attachments: list[Path]) -> tuple[bool, str]:
    if provider["name"] == "brevo":
        return _send_brevo(provider, to_email, subject, body, attachments)
    if provider["name"] == "smtp2go":
        return _send_smtp2go(provider, to_email, subject, body, attachments)
    return False, f"Unknown provider: {provider.get('name')}"


def _send_api_email(to_email: str, subject: str, body: str,
                    attachments: list[Path]) -> tuple[bool, str]:
    if not smtp_configured():
        return False, "No email API sender configured"

    last_error = ""
    max_attempts = max(config.SMTP_RETRY_COUNT + 1, len(getattr(config, "EMAIL_PROVIDERS", [])))
    for attempt in range(1, max_attempts + 1):
        provider = _next_email_provider()
        if not provider:
            return False, f"All email providers exhausted. Last error: {last_error}"
        try:
            logger.info(f"[Mailer] Provider: {provider.get('name')} API")
            ok, msg = _send_via_provider(provider, to_email, subject, body, attachments)
            if ok:
                return True, f"Sent via {provider.get('name')}: {msg}"
            last_error = msg
            if any(code in last_error.lower() for code in ("401", "402", "403", "429", "rate", "quota", "limit", "suspended")):
                _mark_provider_dead(provider, last_error)
            logger.warning(f"[Mailer] Attempt {attempt} API error: {last_error}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[Mailer] Attempt {attempt} failed: {last_error}")
        if attempt <= max_attempts:
            time.sleep(5 * attempt)
    return False, f"Failed after {max_attempts} provider attempt(s): {last_error}"


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
        return False, "No email API sender configured - set BREVO_API_KEY or SMTP2GO_API_KEY plus verified from address"

    daily_limit = int(getattr(config, "EMAIL_DAILY_LIMIT", 100))
    if daily_limit > 0 and emails_sent_today() >= daily_limit:
        return False, f"Daily send limit reached ({daily_limit})"

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

    logger.info(f"[Mailer] Sending to: {to_email}")
    logger.info(f"[Mailer] Subject: {subject}")
    logger.info(f"[Mailer] Attachments: {[a.name for a in attachments]}")

    _wait_throttle()

    last_error = ""
    max_attempts = max(config.SMTP_RETRY_COUNT + 1, len(getattr(config, "EMAIL_PROVIDERS", [])))
    for attempt in range(1, max_attempts + 1):
        provider = _next_email_provider()
        if not provider:
            return False, f"All email providers exhausted. Last error: {last_error}"
        try:
            logger.info(f"[Mailer] Provider: {provider.get('name')} API")
            ok, msg = _send_via_provider(provider, to_email, subject, body, attachments)
            if ok:
                files = ", ".join(f.name for f in attachments)
                return True, f"Sent to {to_email} via {provider.get('name')} with: {files}"
            last_error = msg
            if _is_hard_fail(last_error):
                return False, f"Hard bounce - not retrying: {last_error}"
            if any(code in last_error for code in ("401", "402", "403", "429", "rate", "quota", "limit", "suspended")):
                _mark_provider_dead(provider, last_error)
            logger.warning(f"[Mailer] Attempt {attempt} API error: {last_error}")

        except Exception as e:
            last_error = str(e)
            if _is_hard_fail(last_error):
                return False, f"Hard bounce - not retrying: {last_error}"
            logger.warning(f"[Mailer] Attempt {attempt} failed: {last_error}")

        if attempt <= max_attempts:
            time.sleep(5 * attempt)

    return False, f"Failed after {max_attempts} provider attempt(s): {last_error}"


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
        log(f"  ⚠ Email API not configured — cannot send follow-up to {company}")
        return False

    _wait_throttle()

    try:
        ok, msg = _send_api_email(to_email, subject, body, [])
        if not ok:
            raise RuntimeError(msg)
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

    daily_limit = int(getattr(config, "EMAIL_DAILY_LIMIT", 100))
    if daily_limit > 0 and emails_sent_today() >= daily_limit:
        log(f"  ⚠ Daily send limit reached ({daily_limit}) — skipping {len(jobs)} job(s)")
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

        _wait_throttle()

        try:
            ok, msg = _send_api_email(to_email, subject, body, all_attachments)
            if not ok:
                raise RuntimeError(msg)
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
        return False, "Email API settings incomplete - fill BREVO_API_KEY or SMTP2GO_API_KEY and a verified from address"
    providers = [p.get("name") for p in getattr(config, "EMAIL_PROVIDERS", []) if p.get("name") not in _dead_providers]
    return True, f"✅ Email API provider(s) configured: {', '.join(providers)}"
