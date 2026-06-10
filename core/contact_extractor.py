"""
Contact extractor.

Strategy (in priority order):
  1. Groq extracts anything explicit in the job description
     (this is the only reliable source — text the company itself wrote).
  2. Hunter.io enrichment if HUNTER_API_KEY is set.
  3. Fall back to the application URL only — never a fabricated email.

Why no email-pattern guessing?
  The previous version did `HEAD https://{domain}` to check the domain
  was alive, and if so returned "jobs@{domain}" as the application
  email. That HEAD verifies a website, not a mailbox. Sending to a
  fabricated address produces a hard bounce, which damages the sending
  domain's reputation — which then sends your *real* emails to spam.

  An empty email is far better than a fake one. The mailer skips
  auto-send when no email is found, and the user can apply via
  application_url instead.
"""

import re
import logging
import requests
from urllib.parse import urlparse
from core.groq_client import chat_json
from config import config

logger = logging.getLogger(__name__)

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


# Job-board domains we should NOT treat as the employer's own domain
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
    """
    Domain derivation (NOT email derivation — domain only):
      1. What Groq found in the description
      2. The job URL, if it's not a job board
      3. (No slugify fallback — that produced too many wrong domains.)
    """
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

    # Defaults
    result.setdefault("hr_name", "")
    result.setdefault("hr_title", "")
    result.setdefault("hr_email", "")
    result.setdefault("application_email", "")
    result.setdefault("application_url", url)
    result.setdefault("company_domain", "")
    result.setdefault("contact_notes", "")

    if not result["application_url"]:
        result["application_url"] = url

    # Validate any emails Groq returned — drop garbage
    if result["hr_email"] and not _is_valid_email_format(result["hr_email"]):
        result["hr_email"] = ""
    if result["application_email"] and not _is_valid_email_format(result["application_email"]):
        result["application_email"] = ""

    domain = _guess_company_domain(company, url, result.get("company_domain", ""))
    result["company_domain"] = domain

    # ── Hunter.io enrichment ──────────────────────────────────────────────
    if config.HUNTER_API_KEY and domain:
        hunter = _hunter_lookup(domain)
        if hunter:
            if not result["hr_email"] and hunter.get("email"):
                result["hr_email"] = hunter["email"]
                result["contact_notes"] = "Email from Hunter.io"
            if not result["hr_name"] and hunter.get("name"):
                result["hr_name"] = hunter["name"]
            if not result["hr_title"] and hunter.get("position"):
                result["hr_title"] = hunter["position"]

    # NOTE: We deliberately do NOT guess emails like "jobs@<domain>".
    # If no real address was found, we leave hr_email and
    # application_email empty. The mailer will skip auto-send and the
    # user can apply via application_url.
    if not (result["hr_email"] or result["application_email"]):
        result.setdefault("contact_notes", "")
        if not result["contact_notes"]:
            result["contact_notes"] = "No verified email — apply via URL"

    return result


def _hunter_lookup(domain: str) -> dict | None:
    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={
                "domain":  domain,
                "api_key": config.HUNTER_API_KEY,
                "type":    "personal",
                "limit":   5,
            },
            timeout=10,
        )
        emails = resp.json().get("data", {}).get("emails", [])
        priority = ["hr", "talent", "recruit", "people", "hiring"]
        # Prefer addresses Hunter actually verified
        emails = sorted(
            emails,
            key=lambda e: (
                0 if (e.get("verification") or {}).get("status") == "valid" else 1,
                0 if (e.get("confidence") or 0) >= 70 else 1,
            ),
        )
        for e in emails:
            dept = (e.get("department") or "").lower()
            pos  = (e.get("position") or "").lower()
            if any(r in dept or r in pos for r in priority):
                return {
                    "email":    e.get("value", ""),
                    "name":     f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                    "position": e.get("position", ""),
                }
        if emails:
            e = emails[0]
            return {
                "email":    e.get("value", ""),
                "name":     f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                "position": e.get("position", ""),
            }
    except Exception as ex:
        logger.debug(f"[Hunter] Failed for {domain}: {ex}")
    return None
