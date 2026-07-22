"""
Per-user email sending.

Unlike the legacy ``mailer.py`` (which reads SMTP settings from a single global
``.env`` and tracks state in the legacy DB), this sends through *the user's own*
SMTP credentials from their Settings, and records every send in the tenant's
own rows via the repository. Dedup and the daily cap are enforced per user, so
one tenant can neither email through another's mailbox nor spend another's quota.

The message body/subject come from the ``email.json`` the document generator
writes into each job's output folder; CV.pdf and CoverLetter.pdf are attached.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from . import repository as repo
from .runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)


class SendResult:
    def __init__(self, ok: bool, message: str):
        self.ok = ok
        self.message = message


def smtp_ready(cfg: RuntimeConfig) -> bool:
    s = cfg.smtp
    return bool(s.get("host") and s.get("user") and s.get("password") and s.get("from"))


def _load_email(output_dir: str) -> dict | None:
    p = Path(output_dir) / "email.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _attachments(output_dir: str) -> list[Path]:
    folder = Path(output_dir)
    return [p for name in ("CV.pdf", "CoverLetter.pdf") if (p := folder / name).exists()]


def send_application(cfg: RuntimeConfig, job, to_email: str,
                     skip_dedup: bool = False) -> SendResult:
    """
    Send one application for ``job`` to ``to_email`` using the user's SMTP.

    Returns a SendResult; the caller records job state. Enforces the per-user
    dedup window and daily cap before sending.
    """
    if not smtp_ready(cfg):
        return SendResult(False, "Your SMTP sender isn't configured (Settings → Credentials).")
    if not to_email:
        return SendResult(False, "No recipient email.")

    if not skip_dedup and repo.email_already_sent_to(
        cfg.user_id, to_email, within_days=cfg.dedup_window_days
    ):
        return SendResult(False, f"Skipped — already emailed {to_email} recently.")

    if repo.emails_sent_today(cfg.user_id) >= cfg.email_daily_limit:
        return SendResult(False, "Daily send limit reached.")

    email = _load_email(job.output_dir if hasattr(job, "output_dir") else job["output_dir"])
    if not email:
        return SendResult(False, "No generated email found — run the pipeline first.")

    out_dir = job.output_dir if hasattr(job, "output_dir") else job["output_dir"]
    msg = EmailMessage()
    msg["From"] = cfg.smtp["from"]
    msg["To"] = to_email
    msg["Subject"] = email.get("subject", "Application")
    reply_to = cfg.smtp.get("reply_to") or cfg.smtp.get("from")
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(email.get("body", ""))

    for path in _attachments(out_dir):
        msg.add_attachment(
            path.read_bytes(), maintype="application", subtype="pdf", filename=path.name
        )

    try:
        _deliver(cfg.smtp, msg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SMTP send failed for user %s: %s", cfg.user_id, exc)
        return SendResult(False, f"Send failed: {exc}")

    return SendResult(True, f"Sent to {to_email}")


def _deliver(smtp: dict, msg: EmailMessage) -> None:
    host = smtp["host"]
    port = int(smtp.get("port") or 587)
    user = smtp["user"]
    password = smtp["password"]
    context = ssl.create_default_context()

    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)


def send_follow_up(cfg: RuntimeConfig, job) -> SendResult:
    """Send a short plain-text follow-up to a non-responding contact."""
    if not smtp_ready(cfg):
        return SendResult(False, "SMTP not configured.")
    to_email = job.hr_email or job.application_email
    if not to_email:
        return SendResult(False, "No contact to follow up.")

    msg = EmailMessage()
    msg["From"] = cfg.smtp["from"]
    msg["To"] = to_email
    msg["Subject"] = f"Following up — {job.title} application"
    msg["Reply-To"] = cfg.smtp.get("reply_to") or cfg.smtp["from"]
    name = cfg.smtp.get("from_name") or ""
    msg.set_content(
        f"Hi{(' ' + job.hr_name) if job.hr_name else ''},\n\n"
        f"I wanted to gently follow up on my application for the {job.title} role "
        f"at {job.company}. I remain very interested and would welcome the chance "
        f"to discuss how I can contribute.\n\n"
        f"Thank you for your time.\n\n{name}".rstrip()
    )
    try:
        _deliver(cfg.smtp, msg)
    except Exception as exc:  # noqa: BLE001
        return SendResult(False, f"Follow-up failed: {exc}")
    return SendResult(True, f"Follow-up sent to {to_email}")
