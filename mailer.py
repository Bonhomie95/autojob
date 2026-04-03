"""
mailer.py — SMTP email sender for Job Hunter.
Configured for Namecheap cPanel webmail (SSL port 465).
"""

import time
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.header import Header
from email import encoders
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import config
from database import update_job

logger = logging.getLogger(__name__)


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
    """
    Parse subject and body from EMAIL_DRAFT.txt.
    The file structure is:
        ...
        ────────────────────
          EMAIL BODY  (copy from here)
        ────────────────────
        [blank]
        [actual email body text]
        [blank]
        ────────────────────
        ...MATCH ANALYSIS...
    """
    draft = Path(output_dir) / "EMAIL_DRAFT.txt"
    if not draft.exists():
        return "Job Application", "Please find my attached application documents."

    content = draft.read_text(encoding="utf-8")
    subject = ""
    body_lines: list[str] = []
    in_body = False
    # skip_next_separator: the separator line immediately after the EMAIL BODY marker
    # must be skipped — it is structural, not content
    skip_next_separator = False

    for line in content.splitlines():
        # Extract subject from the header block
        if "SUBJECT:" in line and not subject:
            subject = line.split("SUBJECT:", 1)[1].strip()
            continue

        # Detect the EMAIL BODY marker line
        if "EMAIL BODY" in line and "copy" in line.lower():
            in_body = True
            skip_next_separator = True  # next separator line is structural, skip it
            continue

        if in_body:
            is_separator = line.startswith("─" * 10) or line.startswith("-" * 10)

            if is_separator and skip_next_separator:
                # This is the structural separator right after the marker — skip it
                skip_next_separator = False
                continue

            if is_separator and body_lines:
                # This is the closing separator — body is done
                break

            if "MATCH ANALYSIS" in line:
                break

            skip_next_separator = False
            body_lines.append(line)

    body = "\n".join(body_lines).strip()

    # Replace literal \n escape sequences with real newlines (from Groq output)
    body = body.replace("\\n", "\n")

    return (subject or "Job Application"), (body or "Please find my attached application documents.")


def _safe_subject(subject: str) -> str:
    """
    Encode subject properly for email headers.
    Uses RFC 2047 UTF-8 encoding only when needed (non-ASCII chars present).
    This prevents garbled subjects in strict email clients.
    """
    try:
        subject.encode("ascii")
        return subject  # Pure ASCII — no encoding needed
    except UnicodeEncodeError:
        # Has non-ASCII — encode the whole thing as UTF-8 QP
        return str(Header(subject, charset="utf-8"))


def _body_to_html(plain: str) -> str:
    """Convert plain text body to simple HTML for better email client rendering."""
    import html as html_mod
    escaped = html_mod.escape(plain)
    paragraphs = escaped.split("\n\n")
    html_parts = []
    for para in paragraphs:
        lines = para.strip().splitlines()
        if not lines:
            continue
        html_parts.append("<p>" + "<br>".join(lines) + "</p>")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #111; line-height: 1.6; }}
  p {{ margin: 0 0 12px 0; }}
</style>
</head>
<body>
{"".join(html_parts)}
</body></html>"""


def _build_message(to_email: str, subject: str, body: str,
                   attachments: list[Path]) -> MIMEMultipart:
    """
    Build a properly structured multipart/mixed email:
      - multipart/alternative inside (plain + html)
      - file attachments outside
    This structure ensures the body always renders in all email clients.
    """
    outer = MIMEMultipart("mixed")
    outer["From"]     = f"{config.CANDIDATE_NAME} <{config.SMTP_FROM}>"
    outer["To"]       = to_email
    outer["Subject"]  = _safe_subject(subject)
    outer["Reply-To"] = config.SMTP_FROM

    # Inner alternative part — plain + HTML
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", "utf-8"))
    alt.attach(MIMEText(_body_to_html(body), "html", "utf-8"))
    outer.attach(alt)

    # Attachments
    for filepath in attachments:
        try:
            with open(filepath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{filepath.name}"',
            )
            outer.attach(part)
        except Exception as e:
            logger.warning(f"[Mailer] Could not attach {filepath.name}: {e}")

    return outer


# ──────────────────────────────────────────────────────────
# Core send (with retry)
# ──────────────────────────────────────────────────────────

def send_application(
    to_email: str,
    output_dir: str,
    subject: Optional[str] = None,
    body: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Send application email with attachments.
    Retries SMTP_RETRY_COUNT times on transient failure.
    Returns (success, message).
    """
    if not smtp_configured():
        return False, "SMTP not configured — set password in Settings"

    attachments = _get_attachments(output_dir)
    if not attachments:
        return False, f"No attachment files found in: {output_dir}"

    if not subject or not body:
        ds, db = _read_draft(output_dir)
        subject = subject or ds
        body    = body or db

    if not body.strip():
        logger.warning("[Mailer] Body is empty after parsing — using fallback")
        body = "Please find my attached application documents."

    msg = _build_message(to_email, subject, body, attachments)

    logger.info(f"[Mailer] Sending to: {to_email}")
    logger.info(f"[Mailer] Subject: {subject}")
    logger.info(f"[Mailer] Body preview: {body[:120].replace(chr(10), ' ')}")
    logger.info(f"[Mailer] Attachments: {[a.name for a in attachments]}")

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

        except smtplib.SMTPRecipientsRefused:
            return False, f"Recipient refused by mail server: {to_email}"

        except Exception as e:
            last_error = str(e)
            logger.warning(f"[Mailer] Attempt {attempt} failed: {last_error}")
            if attempt <= config.SMTP_RETRY_COUNT:
                wait = 5 * attempt
                logger.info(f"[Mailer] Retrying in {wait}s…")
                time.sleep(wait)

    return False, f"Failed after {config.SMTP_RETRY_COUNT + 1} attempt(s): {last_error}"


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
        contact.get("hr_email")
        or contact.get("application_email")
        or job.get("hr_email")
        or job.get("application_email")
        or ""
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
