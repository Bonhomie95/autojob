import logging
from core.groq_client import chat_json

logger = logging.getLogger(__name__)

SYSTEM = """You are a professional job-matching AI. Given a candidate's CV and a job description,
score the match from 0-100 and explain briefly.

Return ONLY valid JSON — no markdown, no explanation outside the JSON:
{
  "score": <integer 0-100>,
  "match_reasons": ["reason 1", "reason 2"],
  "gaps": ["gap 1"],
  "ats_keywords": ["keyword1", "keyword2"],
  "seniority": "junior|mid|senior",
  "is_blacklisted": false,
  "blacklist_reason": ""
}"""


def score_job(cv_text: str, job: dict, blacklist: list[str]) -> dict:
    """Score a job against the CV. Returns score dict."""
    description = job.get("description", "")[:3000]  # Truncate for token efficiency
    title = job.get("title", "")
    company = job.get("company", "")

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
            }

    user = f"""
CANDIDATE CV:
{cv_text[:1500]}

JOB: {title} at {company}
JOB DESCRIPTION:
{description[:1500]}
"""
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
        }
    return result
