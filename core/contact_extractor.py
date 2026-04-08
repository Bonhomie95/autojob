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
Leave fields as empty string if not found. Do not guess or fabricate data."""

# Common HR/careers email prefixes to try when no email is found
HR_EMAIL_PREFIXES = [
    "jobs", "careers", "hiring", "hr", "recruit", "talent",
    "apply", "work", "join", "people", "opportunities",
]


def _extract_domain_from_url(url: str) -> str:
    """Extract clean domain from any URL. Returns '' if can't parse."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc or parsed.path
        # Strip www. and port
        host = re.sub(r"^www\.", "", host).split(":")[0].strip()
        # Must look like a real domain (has a dot, not an IP)
        if "." in host and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
            return host
    except Exception:
        pass
    return ""


def _guess_company_domain(company: str, job_url: str, groq_domain: str) -> str:
    """
    Best-effort domain derivation:
    1. Use what Groq found
    2. Parse from the job URL
    3. Slugify company name → company.com
    """
    if groq_domain:
        return groq_domain

    # From job URL (works for direct company career pages)
    domain_from_url = _extract_domain_from_url(job_url)
    # Exclude known job board domains — these aren't the company's own domain
    board_domains = {
        "linkedin.com", "indeed.com", "glassdoor.com", "remoteok.com",
        "weworkremotely.com", "remotive.com", "jobicy.com", "arbeitnow.com",
        "wellfound.com", "greenhouse.io", "lever.co", "workable.com",
        "smartrecruiters.com", "ashbyhq.com", "jobvite.com",
    }
    if domain_from_url and not any(b in domain_from_url for b in board_domains):
        return domain_from_url

    # Slugify company name → company.com (rough guess)
    slug = re.sub(r"[^a-z0-9]", "", company.lower())
    if slug and len(slug) >= 3:
        return f"{slug}.com"

    return ""


def _verify_email(email: str) -> bool:
    """
    Quick MX/HEAD check to reduce false positives before storing.
    Returns True if the email address is plausibly valid.
    Skips slow DNS checks — just validates format.
    """
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def _probe_hr_emails(domain: str) -> str:
    """
    Try common HR email patterns for a domain.
    Sends a lightweight HEAD request to verify the domain exists,
    then returns the most likely email without actually sending anything.
    """
    if not domain:
        return ""
    try:
        # Verify the domain is live
        resp = requests.head(f"https://{domain}", timeout=5, allow_redirects=True)
        if resp.status_code >= 400:
            return ""
    except Exception:
        return ""

    # Return the highest-priority likely email — we can't verify without sending
    return f"{HR_EMAIL_PREFIXES[0]}@{domain}"   # e.g. jobs@company.com


def extract_contacts(job: dict) -> dict:
    """
    Extract HR contact info. Strategy:
    1. Groq extracts anything explicit in the job description
    2. Hunter.io enrichment (if API key configured)
    3. Domain guessing + common HR email patterns
    4. application_url always falls back to the job URL
    """
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

    # Always ensure application_url is set
    if not result["application_url"]:
        result["application_url"] = url

    # ── Hunter.io enrichment ──────────────────────────────────────────────
    domain = _guess_company_domain(company, url, result.get("company_domain", ""))
    result["company_domain"] = domain

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

    # ── Domain-based email guessing (when nothing else worked) ────────────
    if not result["hr_email"] and not result["application_email"] and domain:
        # Only probe if domain doesn't belong to a job board
        board_domains = {"linkedin.com", "indeed.com", "lever.co", "greenhouse.io",
                         "workable.com", "ashbyhq.com", "smartrecruiters.com"}
        if not any(b in domain for b in board_domains):
            guessed = _probe_hr_emails(domain)
            if guessed and _verify_email(guessed):
                result["application_email"] = guessed
                result["contact_notes"] = (
                    f"Email guessed from domain ({domain}) — verify before sending"
                )
                logger.info(f"[Contacts] Guessed email: {guessed} for {company}")

    return result


def _hunter_lookup(domain: str) -> dict | None:
    """Use Hunter.io domain-search API."""
    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": config.HUNTER_API_KEY,
                    "type": "personal", "limit": 5},
            timeout=10,
        )
        emails = resp.json().get("data", {}).get("emails", [])
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
        if emails:
            e = emails[0]
            return {"email": e.get("value",""),
                    "name": f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                    "position": e.get("position","")}
    except Exception as ex:
        logger.debug(f"[Hunter] Failed for {domain}: {ex}")
    return None
