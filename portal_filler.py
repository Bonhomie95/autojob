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

# ── Login-wall signals ────────────────────────────────────────
_LOGIN_SIGNALS = (
    "sign in",
    "log in",
    "create an account",
    "login required",
    "please sign in",
    "please log in",
    "join now",
    "continue with linkedin",
)

# URL patterns that are inherently behind a login wall — we attempt login
_LINKEDIN_JOB_URL_RE = re.compile(r"linkedin\.com/(jobs|authwall)", re.I)


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
    if "greenhouse.io" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "ashbyhq.com" in u:
        return "ashby"
    if "myworkdayjobs.com" in u:
        return "workday"
    if "smartrecruiters.com" in u:
        return "smartrecruiters"
    if "workable.com" in u:
        return "workable"
    if "bamboohr.com" in u:
        return "bamboohr"
    if "recruitee.com" in u:
        return "recruitee"
    if "jobvite.com" in u:
        return "jobvite"
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
        (
            [
                "input[name*='first']",
                "input[id*='first']",
                "input[placeholder*='First']",
                "input[aria-label*='first' i]",
            ],
            candidate.get("first_name", ""),
        ),
        (
            [
                "input[name*='last']",
                "input[id*='last']",
                "input[placeholder*='Last']",
                "input[aria-label*='last' i]",
            ],
            candidate.get("last_name", ""),
        ),
        (
            [
                "input[name*='full']",
                "input[id*='full']",
                "input[placeholder*='Full name' i]",
                "input[aria-label*='full name' i]",
            ],
            candidate.get("full_name", ""),
        ),
        # Contact
        (
            ["input[type='email']", "input[name*='email']", "input[id*='email']"],
            candidate.get("email", ""),
        ),
        (
            [
                "input[type='tel']",
                "input[name*='phone']",
                "input[id*='phone']",
                "input[placeholder*='phone' i]",
            ],
            candidate.get("phone", ""),
        ),
        # Location
        (
            [
                "input[name*='location']",
                "input[id*='location']",
                "input[placeholder*='location' i]",
                "input[placeholder*='city' i]",
            ],
            candidate.get("location", ""),
        ),
        # LinkedIn / GitHub
        (
            [
                "input[name*='linkedin']",
                "input[id*='linkedin']",
                "input[placeholder*='LinkedIn' i]",
            ],
            candidate.get("linkedin", ""),
        ),
        (
            [
                "input[name*='github']",
                "input[id*='github']",
                "input[placeholder*='GitHub' i]",
            ],
            candidate.get("github", ""),
        ),
        # Website / portfolio
        (
            [
                "input[name*='website']",
                "input[id*='website']",
                "input[placeholder*='website' i]",
                "input[name*='portfolio']",
            ],
            candidate.get("linkedin", ""),
        ),
        # Cover letter textarea
        (
            [
                "textarea[name*='cover']",
                "textarea[id*='cover']",
                "textarea[placeholder*='cover letter' i]",
                "textarea[name*='letter']",
            ],
            candidate.get("cover_letter_text", ""),
        ),
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
            "input[type='file']",  # last resort — first file input on page
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
    _fill_if_visible(page, "#first_name", candidate["first_name"], timeout)
    _fill_if_visible(page, "#last_name", candidate["last_name"], timeout)
    _fill_if_visible(page, "#email", candidate["email"], timeout)
    _fill_if_visible(page, "#phone", candidate["phone"], timeout)
    _fill_if_visible(
        page, "input[name='job_application[location]']", candidate["location"], timeout
    )
    _fill_if_visible(page, "input[id*='linkedin']", candidate["linkedin"], timeout)
    _fill_if_visible(page, "input[id*='website']", candidate["linkedin"], timeout)
    _upload_if_visible(page, "input[type='file'][id*='resume']", cv_path, timeout)
    _upload_if_visible(page, "input[type='file'][id*='cover']", cl_path, timeout)


def _fill_lever(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    _fill_if_visible(page, "input[name='name']", candidate["full_name"], timeout)
    _fill_if_visible(page, "input[name='email']", candidate["email"], timeout)
    _fill_if_visible(page, "input[name='phone']", candidate["phone"], timeout)
    _fill_if_visible(page, "input[name='org']", "", timeout)
    _fill_if_visible(page, "input[name*='linkedin']", candidate["linkedin"], timeout)
    _fill_if_visible(page, "input[name*='github']", candidate["github"], timeout)
    _fill_if_visible(
        page,
        "textarea[name='comments']",
        candidate.get("cover_letter_text", ""),
        timeout,
    )
    _upload_if_visible(page, "input[type='file'][name='resume']", cv_path, timeout)


def _fill_ashby(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    _fill_if_visible(
        page, "input[data-testid='name-input']", candidate["full_name"], timeout
    )
    _fill_if_visible(
        page, "input[data-testid='email-input']", candidate["email"], timeout
    )
    _fill_if_visible(
        page, "input[data-testid='phone-input']", candidate["phone"], timeout
    )
    _fill_if_visible(
        page, "input[data-testid='linkedin-input']", candidate["linkedin"], timeout
    )
    _fill_if_visible(
        page, "input[data-testid='github-input']", candidate["github"], timeout
    )
    _upload_if_visible(page, "input[type='file']", cv_path, timeout)


def _fill_workday(page, candidate: dict, cv_path: str, cl_path: str, timeout: int):
    # Workday uses data-automation-id attributes
    _fill_if_visible(
        page,
        "[data-automation-id='legalNameSection_firstName']",
        candidate["first_name"],
        timeout,
    )
    _fill_if_visible(
        page,
        "[data-automation-id='legalNameSection_lastName']",
        candidate["last_name"],
        timeout,
    )
    _fill_if_visible(page, "[data-automation-id='email']", candidate["email"], timeout)
    _fill_if_visible(page, "[data-automation-id='phone']", candidate["phone"], timeout)
    _fill_if_visible(
        page,
        "[data-automation-id='addressSection_addressLine1']",
        candidate["location"],
        timeout,
    )
    _upload_if_visible(
        page, "input[data-automation-id='file-upload-input-ref']", cv_path, timeout
    )


def _fill_smartrecruiters(
    page, candidate: dict, cv_path: str, cl_path: str, timeout: int
):
    _fill_if_visible(page, "input[id='firstName']", candidate["first_name"], timeout)
    _fill_if_visible(page, "input[id='lastName']", candidate["last_name"], timeout)
    _fill_if_visible(page, "input[id='email']", candidate["email"], timeout)
    _fill_if_visible(page, "input[id='phoneNumber']", candidate["phone"], timeout)
    _upload_if_visible(page, "input[type='file']", cv_path, timeout)


# ── Login helpers ─────────────────────────────────────────────


def _try_linkedin_login(page, timeout: int) -> bool:
    """
    Attempt LinkedIn login using optional PORTAL_LINKEDIN_EMAIL / PORTAL_LINKEDIN_PASSWORD.
    Returns True if login appears to have succeeded.
    """
    from config import config

    email = getattr(config, "PORTAL_LINKEDIN_EMAIL", "")
    password = getattr(config, "PORTAL_LINKEDIN_PASSWORD", "")
    if not email or not password:
        logger.warning("[Portal] LinkedIn credentials not set in .env — cannot log in")
        return False

    try:
        # Navigate to LinkedIn login page
        page.goto(
            "https://www.linkedin.com/login",
            wait_until="domcontentloaded",
            timeout=timeout,
        )
        page.wait_for_timeout(1500)

        # Fill credentials
        email_el = page.locator("input#username").first
        if email_el.is_visible(timeout=5000):
            email_el.fill(email)
        else:
            logger.warning("[Portal] LinkedIn login: email field not found")
            return False

        pass_el = page.locator("input#password").first
        if pass_el.is_visible(timeout=5000):
            pass_el.fill(password)
        else:
            logger.warning("[Portal] LinkedIn login: password field not found")
            return False

        # Submit
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(4000)

        # Check if login worked — no login form and feed/jobs visible
        post_url = page.url.lower()
        if "checkpoint" in post_url or "challenge" in post_url:
            logger.warning("[Portal] LinkedIn login: 2FA / security challenge required")
            return False
        if (
            "linkedin.com/feed" in post_url
            or "linkedin.com/jobs" in post_url
            or "linkedin.com/in/" in post_url
        ):
            logger.info("[Portal] LinkedIn login: successful")
            return True

        # Fallback: check page text
        body = page.inner_text("body")[:400].lower()
        if "sign in" not in body and "log in" not in body:
            logger.info("[Portal] LinkedIn login: appears successful (no login form)")
            return True

        logger.warning("[Portal] LinkedIn login: still on login page after submit")
        return False

    except Exception as e:
        logger.warning(f"[Portal] LinkedIn login error: {e}")
        return False


def _try_google_login(page, timeout: int) -> bool:
    """
    Attempt Google SSO login using optional GOOGLE_EMAIL / GOOGLE_PASSWORD.
    This only handles the standard Google sign-in flow.
    Returns True if login appears to have succeeded.
    """
    from config import config

    email = getattr(config, "GOOGLE_EMAIL", "")
    password = getattr(config, "GOOGLE_PASSWORD", "")
    if not email or not password:
        logger.warning("[Portal] Google credentials not set — cannot log in")
        return False

    try:
        page.wait_for_timeout(1500)

        # Look for Google sign-in button on the current page
        google_btn = None
        for sel in [
            "a[href*='accounts.google.com']",
            "button:has-text('Continue with Google')",
            "button:has-text('Sign in with Google')",
            "[data-provider='google']",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    google_btn = el
                    break
            except Exception:
                continue

        if google_btn:
            google_btn.click()
            page.wait_for_timeout(2000)

        # Now fill Google's own sign-in form
        email_el = page.locator("input[type='email']").first
        if email_el.is_visible(timeout=5000):
            email_el.fill(email)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
        else:
            return False

        pass_el = page.locator("input[type='password']").first
        if pass_el.is_visible(timeout=5000):
            pass_el.fill(password)
            page.keyboard.press("Enter")
            page.wait_for_timeout(3000)
        else:
            return False

        post_url = page.url.lower()
        if "accounts.google.com" not in post_url:
            logger.info("[Portal] Google login: appears successful")
            return True

        logger.warning("[Portal] Google login: still on Google accounts page")
        return False

    except Exception as e:
        logger.warning(f"[Portal] Google login error: {e}")
        return False


def _is_login_wall(page_text: str) -> bool:
    return any(s in page_text for s in _LOGIN_SIGNALS)


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

    apply_url = job.get("application_url") or job.get("url") or ""
    output_dir = job.get("output_dir") or job.get("_output_dir") or ""
    company = job.get("company", "")
    title = job.get("title", "")

    if not apply_url:
        return False, "No application URL"

    # Ensure URL has a valid scheme — bare domains like "build.a.team/..." crash Playwright
    if not apply_url.startswith(("http://", "https://")):
        apply_url = "https://" + apply_url

    # ── Fix 3: Regenerate missing documents rather than hard-failing ──────────
    if not output_dir or not Path(output_dir).exists() or not _get_cv_path(output_dir):
        log(f"  📄 No documents for {company} — attempting to regenerate…")
        try:
            from core.document_generator import generate_documents, _safe_dirname
            from core.cv_profile import get_profile
            from pipeline import _find_cv

            cv_path = _find_cv()
            if not cv_path:
                return (
                    False,
                    "No CV source found in input/ — cannot regenerate documents",
                )
            profile = get_profile(cv_path)
            contact = {
                "hr_name": job.get("hr_name", ""),
                "hr_email": job.get("hr_email", ""),
                "hr_title": job.get("hr_title", ""),
                "application_email": job.get("application_email", ""),
                "application_url": apply_url,
            }
            score_data = {
                "score": job.get("score", 0),
                "ats_keywords": [],
                "match_reasons": [],
                "gaps": [],
                "company_insight": "",
            }
            out_root = str(Path(config.OUTPUT_DIR))
            ok, detail = generate_documents(job, profile, contact, score_data, out_root)
            if not ok:
                return False, f"Document regeneration failed: {detail}"
            output_dir = detail
            job["output_dir"] = output_dir
            log(f"  ✓ Documents regenerated into {Path(output_dir).name}/")
            try:
                from database import update_job

                update_job(job.get("id", ""), output_dir=output_dir)
            except Exception:
                pass
        except Exception as regen_err:
            return False, f"Document regeneration error: {regen_err}"

    cv_path = _get_cv_path(output_dir)
    cl_path = _get_cover_letter_path(output_dir)
    if not cv_path:
        return False, "No CV file after regeneration attempt — skipping portal fill"

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
        "full_name": config.CANDIDATE_NAME,
        "first_name": name_parts[0],
        "last_name": name_parts[1] if len(name_parts) > 1 else "",
        "email": config.CANDIDATE_EMAIL,
        "phone": config.CANDIDATE_PHONE,
        "location": config.CANDIDATE_LOCATION,
        "linkedin": config.CANDIDATE_LINKEDIN,
        "github": config.CANDIDATE_GITHUB,
        "cover_letter_text": cl_text,
    }

    platform = _detect_platform(apply_url)
    headless = str(getattr(config, "PORTAL_HEADLESS", "true")).lower() == "true"
    timeout_ms = int(getattr(config, "PORTAL_TIMEOUT_MS", 30000))
    do_submit = str(getattr(config, "PORTAL_SUBMIT", "true")).lower() == "true"

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

            # ── Fix 1: Handle login walls — attempt LinkedIn or Google login ──────
            page_text = page.inner_text("body")[:500].lower()
            current_url = page.url.lower()
            is_linkedin_url = bool(_LINKEDIN_JOB_URL_RE.search(apply_url))

            if is_linkedin_url or _is_login_wall(page_text):
                logged_in = False

                # LinkedIn URLs → try LinkedIn login first
                if is_linkedin_url or "linkedin.com" in current_url:
                    log(f"  🔑 Attempting LinkedIn login for {company}…")
                    logged_in = _try_linkedin_login(page, timeout_ms)
                    if logged_in:
                        # Navigate back to the job URL after login
                        try:
                            page.goto(
                                apply_url,
                                wait_until="domcontentloaded",
                                timeout=timeout_ms,
                            )
                            page.wait_for_timeout(2000)
                        except PWTimeout:
                            pass

                # Generic login wall → also try Google SSO if LinkedIn failed
                if not logged_in and _is_login_wall(
                    page.inner_text("body")[:500].lower()
                ):
                    log(f"  🔑 Attempting Google login for {company}…")
                    logged_in = _try_google_login(page, timeout_ms)
                    if logged_in:
                        page.wait_for_timeout(2000)

                # ── Fix 2: If login still fails, mark as skipped (not stuck) ────
                if not logged_in or _is_login_wall(
                    page.inner_text("body")[:500].lower()
                ):
                    browser.close()
                    return (
                        False,
                        "SKIP:login_required — portal needs manual authentication",
                    )

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
                return (
                    True,
                    f"Form filled (PORTAL_SUBMIT=false — not submitted). Screenshot saved.",
                )

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
                return (
                    False,
                    "Could not find submit button — form was filled but not submitted. Check portal_filled.png",
                )

            # Check for success signals
            success_text = page.inner_text("body")[:600].lower()
            success_signals = (
                "thank you",
                "application received",
                "successfully submitted",
                "we'll be in touch",
                "application submitted",
                "application complete",
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
                return (
                    True,
                    f"✅ Application submitted successfully to {company} ({platform})",
                )
            else:
                return (
                    True,
                    f"Form submitted — confirm manually via portal_submitted.png ({company})",
                )

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
