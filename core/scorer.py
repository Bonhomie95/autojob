"""
scorer.py — Job match scoring with optional company research enrichment.

Improvement: when ENRICH_COMPANY_DATA=true (default), a brief company
summary is fetched before scoring. This allows Groq to produce more
personalised match reasons and cover letters that reference the company
specifically, rather than generic fit language.
"""

import logging
import requests
from core.groq_client import chat_json, chat
from config import config

logger = logging.getLogger(__name__)

SYSTEM = """You are a professional job-matching AI. Given a candidate's CV, a job description,
and optionally a company summary, score the match from 0-100 and explain briefly.

Return ONLY valid JSON — no markdown, no explanation outside the JSON:
{
  "score": <integer 0-100>,
  "match_reasons": ["reason 1", "reason 2"],
  "gaps": ["gap 1"],
  "ats_keywords": ["keyword1", "keyword2"],
  "seniority": "junior|mid|senior",
  "is_blacklisted": false,
  "blacklist_reason": "",
  "company_insight": ""
}

company_insight should be 1-2 sentences about why this company is interesting
for the candidate specifically, based on the company summary if provided.
Leave it empty string if no company info was given."""


def _fetch_company_summary(company: str, domain: str = "") -> str:
    """
    Fetch a brief company summary using a lightweight Groq call.
    Uses the company name + domain to ask for a concise blurb.
    Returns empty string on failure.
    """
    enrich = str(getattr(config, "ENRICH_COMPANY_DATA", "true")).lower()
    if enrich != "true":
        return ""

    try:
        prompt = (
            f"In 2-3 sentences, describe the company '{company}'"
            + (f" (website: {domain})" if domain else "")
            + ". Include: what they do, their tech stack or industry focus, "
            "and why a software engineer might want to work there. "
            "If you don't know the company, say so briefly. "
            "Return only the description, no preamble."
        )
        result = chat(
            system="You are a concise company research assistant.",
            user=prompt,
            temperature=0.2,
            max_tokens=200,
        )
        return result.strip() if result else ""
    except Exception as e:
        logger.debug(f"[Scorer] Company research failed for {company}: {e}")
        return ""


def score_job(cv_text: str, job: dict, blacklist: list[str]) -> dict:
    """Score a job against the CV. Returns score dict."""
    description = job.get("description", "")[:3000]
    title       = job.get("title", "")
    company     = job.get("company", "")
    domain      = job.get("_company_domain", "") or ""

    # Quick blacklist check before hitting the API
    combined = f"{title} {description}".lower()
    for kw in blacklist:
        if kw and kw in combined:
            logger.info(f"[Scorer] Blacklisted '{title}' — matched '{kw}'")
            return {
                "score": 0,
                "match_reasons": [],
                "gaps": [],
                "ats_keywords": [],
                "seniority": "unknown",
                "is_blacklisted": True,
                "blacklist_reason": kw,
                "company_insight": "",
            }

    # Optional company research enrichment
    company_summary = _fetch_company_summary(company, domain)

    company_section = ""
    if company_summary:
        company_section = f"\nCOMPANY SUMMARY:\n{company_summary}\n"
        logger.debug(f"[Scorer] Company insight for {company}: {company_summary[:80]}…")

    user = f"""
CANDIDATE CV:
{cv_text[:1500]}

JOB: {title} at {company}
JOB DESCRIPTION:
{description[:1500]}
{company_section}"""

    result = chat_json(SYSTEM, user)
    if not result:
        return {
            "score": 0,
            "match_reasons": [],
            "gaps": [],
            "ats_keywords": [],
            "seniority": "unknown",
            "is_blacklisted": False,
            "blacklist_reason": "",
            "company_insight": "",
        }

    # Attach company summary so document_generator can use it
    if company_summary and not result.get("company_insight"):
        result["company_insight"] = company_summary

    return result
