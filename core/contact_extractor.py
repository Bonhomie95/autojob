"""
Contact extractor.

Strategy (in priority order):
  1. Groq extracts anything explicit in the job description.
  2. Hunter.io enrichment — rotates through HUNTER_API_KEY_1…N automatically.
     When a key hits its monthly quota (402 / 429) it is marked exhausted for
     the current process session and the next key takes over. If all keys are
     exhausted the lookup is skipped gracefully.
  3. Fall back to the application URL only — never a fabricated email.
"""

import re
import logging
import threading
import requests
from urllib.parse import urlparse
from core.groq_client import chat_json
from config import config

logger = logging.getLogger(__name__)

# ── Hunter key rotation state ─────────────────────────────────
_hunter_lock       = threading.Lock()
_exhausted_keys:  set[str] = set()   # keys that hit quota this session
_key_index:       int = 0            # round-robin pointer


def _next_hunter_key() -> str | None:
    """Return the next live Hunter key, or None if all are exhausted."""
    global _key_index
    with _hunter_lock:
        keys = config.HUNTER_API_KEYS
        if not keys:
            return None
        live = [k for k in keys if k not in _exhausted_keys]
        if not live:
            return None
        # Round-robin within live keys
        _key_index = _key_index % len(live)
        key = live[_key_index]
        _key_index = (_key_index + 1) % len(live)
        return key


def _mark_key_exhausted(key: str):
    with _hunter_lock:
        _exhausted_keys.add(key)
    total = len(config.HUNTER_API_KEYS)
    remaining = total - len(_exhausted_keys)
    logger.warning(
        f"[Hunter] Key exhausted (quota reached). "
        f"{remaining}/{total} key(s) remaining this session."
    )


# ── Prompts ───────────────────────────────────────────────────
SYSTEM = """You are an expert at extracting recruiter and HR contact information from job postings.
Analyze the text and extract any available contact details.

Return ONLY valid JSON:
{
  "hr_name": "",
  "hr_title": "",
  "hr_email": "",
  "application_email": "",
  "application_url": "",
  "company_domain": "",
  "contact_notes": ""
}
Leave fields as empty string if not found. Do not guess or fabricate data.
Only return an email if it appears literally in the job description text."""

# Job-board domains — not the employer's own domain
_BOARD_DOMAINS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "remoteok.com",
    "weworkremotely.com", "remotive.com", "jobicy.com", "arbeitnow.com",
    "wellfound.com", "greenhouse.io", "lever.co", "workable.com",
    "smartrecruiters.com", "ashbyhq.com", "jobvite.com", "myworkdayjobs.com",
    "icims.com", "bamboohr.com", "breezy.hr", "recruitee.com",
}


def _extract_domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc or parsed.path
        host = re.sub(r"^www\.", "", host).split(":")[0].strip()
        if "." in host and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
            return host
    except Exception:
        pass
    return ""


def _guess_company_domain(company: str, job_url: str, groq_domain: str) -> str:
    if groq_domain:
        return groq_domain
    domain_from_url = _extract_domain_from_url(job_url)
    if domain_from_url and not any(b in domain_from_url for b in _BOARD_DOMAINS):
        return domain_from_url
    return ""


def _is_valid_email_format(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def extract_contacts(job: dict) -> dict:
    description = job.get("description", "")
    title       = job.get("title", "")
    company     = job.get("company", "")
    url         = job.get("url", "")

    user = (
        f"JOB TITLE: {title}\n"
        f"COMPANY: {company}\n"
        f"JOB URL: {url}\n\n"
        f"JOB DESCRIPTION:\n{description[:2000]}"
    )
    result = chat_json(SYSTEM, user)
    if not isinstance(result, dict):
        result = {}

    result.setdefault("hr_name", "")
    result.setdefault("hr_title", "")
    result.setdefault("hr_email", "")
    result.setdefault("application_email", "")
    result.setdefault("application_url", url)
    result.setdefault("company_domain", "")
    result.setdefault("contact_notes", "")

    if not result["application_url"]:
        result["application_url"] = url

    if result["hr_email"] and not _is_valid_email_format(result["hr_email"]):
        result["hr_email"] = ""
    if result["application_email"] and not _is_valid_email_format(result["application_email"]):
        result["application_email"] = ""

    domain = _guess_company_domain(company, url, result.get("company_domain", ""))
    result["company_domain"] = domain

    # ── Hunter.io enrichment (with key rotation) ─────────────
    if config.HUNTER_API_KEYS and domain:
        hunter = _hunter_lookup(domain)
        if hunter:
            if not result["hr_email"] and hunter.get("email"):
                result["hr_email"]    = hunter["email"]
                result["contact_notes"] = "Email from Hunter.io"
            if not result["hr_name"] and hunter.get("name"):
                result["hr_name"]  = hunter["name"]
            if not result["hr_title"] and hunter.get("position"):
                result["hr_title"] = hunter["position"]

    if not (result["hr_email"] or result["application_email"]):
        if not result["contact_notes"]:
            result["contact_notes"] = "No verified email — apply via URL"

    return result


def _hunter_lookup(domain: str) -> dict | None:
    """
    Look up HR contacts for a domain using Hunter.io.
    Automatically rotates to the next key when quota is hit.
    Retries once per call with a fresh key on quota errors.
    """
    for attempt in range(len(config.HUNTER_API_KEYS) or 1):
        key = _next_hunter_key()
        if not key:
            logger.debug("[Hunter] All keys exhausted — skipping lookup")
            return None
        try:
            resp = requests.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain":  domain,
                    "api_key": key,
                    "type":    "personal",
                    "limit":   5,
                },
                timeout=10,
            )

            # Quota exhausted — mark key and rotate
            if resp.status_code in (402, 429):
                _mark_key_exhausted(key)
                continue  # try next key

            if not resp.ok:
                logger.debug(f"[Hunter] {resp.status_code} for {domain}")
                return None

            emails = resp.json().get("data", {}).get("emails", [])
            if not emails:
                return None

            # Sort: prefer verified + high confidence
            emails = sorted(
                emails,
                key=lambda e: (
                    0 if (e.get("verification") or {}).get("status") == "valid" else 1,
                    0 if (e.get("confidence") or 0) >= 70 else 1,
                ),
            )
            # Prefer HR/talent/recruiting roles
            priority = ["hr", "talent", "recruit", "people", "hiring"]
            for e in emails:
                dept = (e.get("department") or "").lower()
                pos  = (e.get("position") or "").lower()
                if any(r in dept or r in pos for r in priority):
                    return {
                        "email":    e.get("value", ""),
                        "name":     f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                        "position": e.get("position", ""),
                    }
            # No priority match — use best available
            e = emails[0]
            return {
                "email":    e.get("value", ""),
                "name":     f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                "position": e.get("position", ""),
            }

        except Exception as ex:
            logger.debug(f"[Hunter] Failed for {domain}: {ex}")
            return None

    return None
