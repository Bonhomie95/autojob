"""
portal_filler.py — Headless browser auto-fill for job application portals.

Handles jobs that have an application_url but no HR email — the largest
untapped volume source. Instead of sending an email, Playwright navigates
to the portal, detects the platform, fills the form, attaches documents,
and submits.

Supported platforms (with tailored strategies):
  - Greenhouse   (boards.greenhouse.io)
  - Lever        (jobs.lever.co)
  - Ashby        (jobs.ashbyhq.com)
  - Workday      (myworkdayjobs.com)
  - SmartRecruiters (jobs.smartrecruiters.com)
  - Generic HTML forms (best-effort fallback for custom portals)

Portal fill is attempted after email sending. If the job already has
email_status='sent' it is skipped — no double-applying.

Dependencies:
  pip install playwright
  playwright install chromium

Set in .env:
  PORTAL_ENABLED=true           # Enable/disable feature entirely
  PORTAL_HEADLESS=true          # false = show browser window (useful for debugging)
  PORTAL_TIMEOUT_MS=30000       # Max ms to wait for page elements
  PORTAL_SUBMIT=true            # false = fill but don't click Submit (dry run)
"""

import logging
import re
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _playwright_available() -> bool:
    try:
        import playwright  # noqa
        return True
    except ImportError:
        return False


def _get_cv_path(output_dir: str) -> Optional[str]:
    """Return the best CV file path from the output directory."""
    folder = Path(output_dir)
    for name in ("CV.pdf", "CV.docx"):
        p = folder / name
        if p.exists():
            return str(p)
    return None


def _get_cover_letter_path(output_dir: str) -> Optional[str]:
    folder = Path(output_dir)
    for name in ("CoverLetter.pdf", "CoverLetter.docx"):
        p = folder / name
        if p.exists():
            return str(p)
    return None


def _detect_platform(url: str) -> str:
    u = url.lower()
    if "greenhouse.io"        in u: return "greenhouse"
    if "lever.co"             in u: return "lever"
    if "ashbyhq.com"          in u: return "ashby"
    if "myworkdayjobs.com"    in u: return "workday"
    if "smartrecruiters.com"  in u: return "smartrecruiters"
    if "workable.com"         in u: return "workable"
    if "bamboohr.com"         in u: return "bamboohr"
    if "recruitee.com"        in u: return "recruitee"
    if "jobvite.com"          in u: return "jobvite"
    return "generic"


# ── Field fill helpers ────────────────────────────────────────

def _fill_if_visible(page, selector: str, value: str, timeout: int = 5000):
    """Fill a field if it exists on the page. Silent on miss."""
    try:
        el = page.locator(selector).first
        if el.is_visible(timeout=timeout):
            el.fill(value)
    except Exception:
        pass


def _upload_if_visible(page, selector: str, filepath: str, timeout: int = 5000):
    """Set a file input if it exists."""
    try:
        el = page.locator(selector).first
        if el.is_visible(timeout=timeout) and filepath:
            el.set_input_files(filepath)
    except Exception:
        pass


def _smart_fill_form(page, candidate: dict, cv_path: str, cl_path: str):
    """
    Best-effort generic form fill — tries common field name/id/label patterns.
    Works on most custom portals that don't match a known platform.
    """
    from config import config

    # Text fields — (CSS selectors, value)
    fields = [
        # Name
        (["input[name*='first']",  "input[id*='first']",  "input[placeholder*='First']",
          "input[aria-label*='first' i]"], candidate.get("first_name", "")),
        (["input[name*='last']",   "input[id*='last']",   "input[placeholder*='Last']",
          "input[aria-label*='last' i]"],  candidate.get("last_name", "")),
        (["input[name*='full']",   "input[id*='full']",   "input[placeholder*='Full name' i]",
          "input[aria-label*='full name' i]"], candidate.get("full_name", "")),
        # Contact
        (["input[type='email']",   "input[name*='email']", "input[id*='email']"],
          candidate.get("email", "")),
        (["input[type='tel']",     "input[name*='phone']", "input[id*='phone']",
          "input[placeholder*='phone' i]"], candidate.get("phone", "")),
        # Location
        (["input[name*='location']", "input[id*='location']", "input[placeholder*='location' i]",
          "input[placeholder*='city' i]"], candidate.get("location", "")),
        # LinkedIn / GitHub
        (["input[name*='linkedin']", "input[id*='linkedin']", "input[placeholder*='LinkedIn' i]"],
          candidate.get("linkedin", "")),
        (["input[name*='github']",   "input[id*='github']",   "input[placeholder*='GitHub' i]"],
          candidate.get("github", "")),
        # Website / portfolio
        (["input[name*='website']",  "input[id*='website']",  "input[placeholder*='website' i]",
          "input[name*='portfolio']"],
          candidate.get("linkedin", "")),
        # Cover letter textarea
        (["textarea[name*='cover']", "textarea[id*='cover']",
          "textarea[placeholder*='cover letter' i]", "textarea[name*='letter']"],
          candidate.get("cover_letter_text", "")),
    ]

    for selectors, value in fields:
        if not value:
            continue
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.fill(value)
                    break
            except Exception:
                continue

    # File uploads
    if cv_path:
        for sel in [
            "input[type='file'][name*='resume']",
            "input[type='file'][name*='cv']",
            "input[type='file'][id*='resume']",
            "input[type='file'][id*='cv']",
            "input[type='file']",   # last resort — first file input on page
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000) or el.count() > 0:
                    el.set_input_files(cv_path)
                    logger.debug(f"[Portal] CV uploaded via {sel}")
                    break
            except Exception:
                continue

    if cl_path:
        for sel in [
            "input[type='file'][name*='cover']",
            "input[type='file'][name*='letter']",
            "input[type='file'][id*='cover']",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.set_input_files(cl_path)
                    break
            except Exception:
                continue


# ── Platform-specific strategies ─────────────────────────────

def _fill_greenhouse(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    _fill_if_visible(page, "#first_name",       candidate["first_name"], timeout)
    _fill_if_visible(page, "#last_name",        candidate["last_name"],  timeout)
    _fill_if_visible(page, "#email",            candidate["email"],      timeout)
    _fill_if_visible(page, "#phone",            candidate["phone"],      timeout)
    _fill_if_visible(page, "input[name='job_application[location]']", candidate["location"], timeout)
    _fill_if_visible(page, "input[id*='linkedin']", candidate["linkedin"], timeout)
    _fill_if_visible(page, "input[id*='website']",  candidate["linkedin"], timeout)
    _upload_if_visible(page, "input[type='file'][id*='resume']", cv_path, timeout)
    _upload_if_visible(page, "input[type='file'][id*='cover']",  cl_path, timeout)


def _fill_lever(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    _fill_if_visible(page, "input[name='name']",    candidate["full_name"], timeout)
    _fill_if_visible(page, "input[name='email']",   candidate["email"],     timeout)
    _fill_if_visible(page, "input[name='phone']",   candidate["phone"],     timeout)
    _fill_if_visible(page, "input[name='org']",     "",                     timeout)
    _fill_if_visible(page, "input[name*='linkedin']", candidate["linkedin"],timeout)
    _fill_if_visible(page, "input[name*='github']",   candidate["github"],  timeout)
    _fill_if_visible(page, "textarea[name='comments']", candidate.get("cover_letter_text", ""), timeout)
    _upload_if_visible(page, "input[type='file'][name='resume']", cv_path, timeout)


def _fill_ashby(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    _fill_if_visible(page, "input[data-testid='name-input']",     candidate["full_name"], timeout)
    _fill_if_visible(page, "input[data-testid='email-input']",    candidate["email"],     timeout)
    _fill_if_visible(page, "input[data-testid='phone-input']",    candidate["phone"],     timeout)
    _fill_if_visible(page, "input[data-testid='linkedin-input']", candidate["linkedin"],  timeout)
    _fill_if_visible(page, "input[data-testid='github-input']",   candidate["github"],    timeout)
    _upload_if_visible(page, "input[type='file']", cv_path, timeout)


def _fill_workday(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    # Workday uses data-automation-id attributes
    _fill_if_visible(page, "[data-automation-id='legalNameSection_firstName']", candidate["first_name"], timeout)
    _fill_if_visible(page, "[data-automation-id='legalNameSection_lastName']",  candidate["last_name"],  timeout)
    _fill_if_visible(page, "[data-automation-id='email']",                       candidate["email"],      timeout)
    _fill_if_visible(page, "[data-automation-id='phone']",                       candidate["phone"],      timeout)
    _fill_if_visible(page, "[data-automation-id='addressSection_addressLine1']", candidate["location"],   timeout)
    _upload_if_visible(page, "input[data-automation-id='file-upload-input-ref']", cv_path, timeout)


def _fill_smartrecruiters(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    _fill_if_visible(page, "input[id='firstName']", candidate["first_name"], timeout)
    _fill_if_visible(page, "input[id='lastName']",  candidate["last_name"],  timeout)
    _fill_if_visible(page, "input[id='email']",     candidate["email"],      timeout)
    _fill_if_visible(page, "input[id='phoneNumber']", candidate["phone"],    timeout)
    _upload_if_visible(page, "input[type='file']",  cv_path, timeout)


# ── Main entry point ──────────────────────────────────────────

def fill_portal(job: dict, emit=None) -> tuple[bool, str]:
    """
    Attempt to auto-fill and submit a job application portal.

    Returns (success, message).
    """
    def log(msg):
        logger.info(msg)
        if emit:
            emit(msg)

    if not _playwright_available():
        return False, (
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    from config import config

    portal_enabled = str(getattr(config, "PORTAL_ENABLED", "false")).lower() == "true"
    if not portal_enabled:
        return False, "Portal auto-fill disabled (PORTAL_ENABLED=false)"

    apply_url  = job.get("application_url") or job.get("url") or ""
    output_dir = job.get("output_dir") or job.get("_output_dir") or ""
    company    = job.get("company", "")
    title      = job.get("title", "")

    if not apply_url:
        return False, "No application URL"
    if not output_dir or not Path(output_dir).exists():
        return False, "No output directory — generate documents first"

    cv_path = _get_cv_path(output_dir)
    cl_path = _get_cover_letter_path(output_dir)
    if not cv_path:
        return False, "No CV file found in output directory"

    # Read cover letter text for textarea fields
    cl_text = ""
    cl_txt = Path(output_dir) / "CoverLetter.txt"
    if not cl_txt.exists():
        # Try to read from docx fallback
        cl_draft = Path(output_dir) / "EMAIL_DRAFT.txt"
        if cl_draft.exists():
            cl_text = cl_draft.read_text(encoding="utf-8")[:3000]
    else:
        cl_text = cl_txt.read_text(encoding="utf-8")[:3000]

    # Build candidate info
    name_parts = config.CANDIDATE_NAME.strip().split(" ", 1)
    candidate = {
        "full_name":         config.CANDIDATE_NAME,
        "first_name":        name_parts[0],
        "last_name":         name_parts[1] if len(name_parts) > 1 else "",
        "email":             config.CANDIDATE_EMAIL,
        "phone":             config.CANDIDATE_PHONE,
        "location":          config.CANDIDATE_LOCATION,
        "linkedin":          config.CANDIDATE_LINKEDIN,
        "github":            config.CANDIDATE_GITHUB,
        "cover_letter_text": cl_text,
    }

    platform   = _detect_platform(apply_url)
    headless   = str(getattr(config, "PORTAL_HEADLESS", "true")).lower() == "true"
    timeout_ms = int(getattr(config, "PORTAL_TIMEOUT_MS", 30000))
    do_submit  = str(getattr(config, "PORTAL_SUBMIT", "true")).lower() == "true"

    log(f"  🌐 Portal fill: {company} — {platform} ({apply_url[:60]}…)")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
            )
            page = context.new_page()

            # Navigate
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2000)  # let JS settle
            except PWTimeout:
                browser.close()
                return False, f"Page load timed out: {apply_url}"

            # Check for auth walls / login pages
            page_text = page.inner_text("body")[:500].lower()
            if any(s in page_text for s in ("sign in", "log in", "create an account", "login required")):
                browser.close()
                return False, "Portal requires login — cannot auto-fill"

            # Fill based on platform
            try:
                if platform == "greenhouse":
                    _fill_greenhouse(page, candidate, cv_path, cl_path, timeout_ms)
                elif platform == "lever":
                    _fill_lever(page, candidate, cv_path, cl_path, timeout_ms)
                elif platform == "ashby":
                    _fill_ashby(page, candidate, cv_path, cl_path, timeout_ms)
                elif platform == "workday":
                    _fill_workday(page, candidate, cv_path, cl_path, timeout_ms)
                elif platform == "smartrecruiters":
                    _fill_smartrecruiters(page, candidate, cv_path, cl_path, timeout_ms)
                else:
                    _smart_fill_form(page, candidate, cv_path, cl_path)
            except Exception as e:
                log(f"  ⚠ Fill error ({platform}): {e} — attempting generic fallback")
                try:
                    _smart_fill_form(page, candidate, cv_path, cl_path)
                except Exception as e2:
                    browser.close()
                    return False, f"Fill failed: {e2}"

            page.wait_for_timeout(1000)  # let uploads settle

            # Screenshot for audit trail (always saved, regardless of submit)
            screenshot_path = Path(output_dir) / "portal_filled.png"
            try:
                page.screenshot(path=str(screenshot_path), full_page=False)
                log(f"  📸 Screenshot saved: portal_filled.png")
            except Exception:
                pass

            # Submit
            if not do_submit:
                browser.close()
                return True, f"Form filled (PORTAL_SUBMIT=false — not submitted). Screenshot saved."

            submitted = False
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Submit')",
                "button:has-text('Apply')",
                "button:has-text('Send application')",
                "button:has-text('Submit application')",
                "[data-testid*='submit']",
                "[aria-label*='submit' i]",
            ]
            for sel in submit_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        page.wait_for_timeout(3000)
                        submitted = True
                        log(f"  ✅ Submitted via '{sel}'")
                        break
                except Exception:
                    continue

            if not submitted:
                browser.close()
                return False, "Could not find submit button — form was filled but not submitted. Check portal_filled.png"

            # Check for success signals
            success_text = page.inner_text("body")[:600].lower()
            success_signals = (
                "thank you", "application received", "successfully submitted",
                "we'll be in touch", "application submitted", "application complete",
            )
            confirmed = any(s in success_text for s in success_signals)

            # Post-submit screenshot
            try:
                post_path = Path(output_dir) / "portal_submitted.png"
                page.screenshot(path=str(post_path), full_page=False)
            except Exception:
                pass

            browser.close()

            if confirmed:
                return True, f"✅ Application submitted successfully to {company} ({platform})"
            else:
                return True, f"Form submitted — confirm manually via portal_submitted.png ({company})"

    except Exception as e:
        logger.exception(f"[Portal] Unexpected error for {company}")
        return False, f"Unexpected error: {e}"


def portal_available() -> bool:
    """Quick check — is Playwright installed and chromium available?"""
    if not _playwright_available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False
