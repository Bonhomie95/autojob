import re
import logging
import requests
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


def extract_contacts(job: dict) -> dict:
    """Extract HR contact info from a job posting."""
    description = job.get("description", "")
    title = job.get("title", "")
    company = job.get("company", "")
    url = job.get("url", "")

    user = f"""
JOB TITLE: {title}
COMPANY: {company}
JOB URL: {url}

JOB DESCRIPTION:
{description[:2000]}
"""
    result = chat_json(SYSTEM, user)
    if not result:
        result = {
            "hr_name": "",
            "hr_title": "",
            "hr_email": "",
            "application_email": "",
            "application_url": url,
            "company_domain": "",
            "contact_notes": "",
        }

    # Try Hunter.io if we have API key and a company domain
    if config.HUNTER_API_KEY and result.get("company_domain"):
        hunter_result = _hunter_lookup(result["company_domain"])
        if hunter_result:
            if not result["hr_email"] and hunter_result.get("email"):
                result["hr_email"] = hunter_result["email"]
            if not result["hr_name"] and hunter_result.get("name"):
                result["hr_name"] = hunter_result["name"]
            if not result["hr_title"] and hunter_result.get("position"):
                result["hr_title"] = hunter_result["position"]

    return result


def _hunter_lookup(domain: str) -> dict | None:
    """Use Hunter.io to find HR/talent emails for a domain."""
    try:
        url = "https://api.hunter.io/v2/domain-search"
        params = {
            "domain": domain,
            "api_key": config.HUNTER_API_KEY,
            "type": "personal",
            "limit": 5,
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        emails = data.get("data", {}).get("emails", [])
        # Prefer HR/talent/recruiter roles
        priority_roles = ["hr", "talent", "recruit", "people", "hiring"]
        for email_obj in emails:
            dept = (email_obj.get("department") or "").lower()
            position = (email_obj.get("position") or "").lower()
            if any(r in dept or r in position for r in priority_roles):
                return {
                    "email": email_obj.get("value", ""),
                    "name": f"{email_obj.get('first_name', '')} {email_obj.get('last_name', '')}".strip(),
                    "position": email_obj.get("position", ""),
                }
        # Fall back to first result
        if emails:
            e = emails[0]
            return {
                "email": e.get("value", ""),
                "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                "position": e.get("position", ""),
            }
    except Exception as ex:
        logger.debug(f"[Hunter] Lookup failed for {domain}: {ex}")
    return None
