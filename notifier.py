"""
notifier.py — Telegram push notifications for Job Hunter.

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Message @userinfobot → copy your chat_id
  3. Add to .env:
       TELEGRAM_ENABLED=true
       TELEGRAM_BOT_TOKEN=123456:ABC-...
       TELEGRAM_CHAT_ID=987654321

Notifications sent:
  - Pipeline run finished (summary of found/sent)
  - Follow-up cycle finished
  - New reply detected from a recruiter
  - Error if pipeline crashes
"""

import logging
import requests
from config import config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _enabled() -> bool:
    return bool(
        getattr(config, "TELEGRAM_ENABLED", False) and
        getattr(config, "TELEGRAM_BOT_TOKEN", "") and
        getattr(config, "TELEGRAM_CHAT_ID", "")
    )


def send(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a Telegram message. Returns True on success, False on failure.
    Silently no-ops if TELEGRAM_ENABLED is false or credentials missing.
    """
    if not _enabled():
        return False
    try:
        url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN)
        resp = requests.post(
            url,
            json={
                "chat_id":    config.TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        if not resp.ok:
            logger.warning(f"[Notifier] Telegram send failed: {resp.status_code} {resp.text[:100]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"[Notifier] Telegram error: {e}")
        return False


# ── Pre-formatted notification helpers ───────────────────────

def notify_run_complete(result: dict):
    found  = result.get("jobs_found", 0)
    scored = result.get("jobs_scored", 0)
    docs   = result.get("docs_generated", 0)
    sent   = result.get("emails_sent", 0)

    lines = [
        "🎯 <b>Job Hunter — Run Complete</b>",
        "",
        f"📋 Jobs found:    <b>{found}</b>",
        f"✅ Qualified:     <b>{scored}</b>",
        f"📄 Docs generated: <b>{docs}</b>",
    ]
    if sent:
        lines.append(f"📧 Emails sent:   <b>{sent}</b>")
    if found == 0:
        lines.append("")
        lines.append("ℹ️ No new jobs found — all boards may be exhausted or duplicate.")

    send("\n".join(lines))


def notify_run_error(error: str):
    send(
        f"❌ <b>Job Hunter — Pipeline Error</b>\n\n"
        f"<code>{error[:300]}</code>"
    )


def notify_followup_complete(summary: dict):
    sent    = summary.get("follow_ups_sent", 0)
    replies = summary.get("replies_detected", 0)
    skipped = summary.get("follow_ups_skipped", 0)

    if sent == 0 and replies == 0:
        return  # Nothing interesting to report

    lines = ["📨 <b>Job Hunter — Follow-Up Cycle</b>", ""]
    if replies:
        lines.append(f"💬 Replies detected: <b>{replies}</b>")
    if sent:
        lines.append(f"📤 Follow-ups sent:  <b>{sent}</b>")
    if skipped:
        lines.append(f"⏭ Skipped:          <b>{skipped}</b>")

    send("\n".join(lines))


def notify_reply_detected(company: str, from_email: str):
    send(
        f"💬 <b>Recruiter Reply Detected!</b>\n\n"
        f"🏢 Company: <b>{company}</b>\n"
        f"📧 From: <code>{from_email}</code>\n\n"
        f"Check your inbox and respond promptly!"
    )


def test_notification() -> tuple[bool, str]:
    """Send a test notification. Used from Settings page."""
    if not _enabled():
        return False, "Telegram not configured — set TELEGRAM_ENABLED, BOT_TOKEN and CHAT_ID in .env"
    ok = send("✅ <b>Job Hunter</b> — Telegram notifications are working!")
    return (True, "Test notification sent!") if ok else (False, "Failed to send — check your BOT_TOKEN and CHAT_ID")
