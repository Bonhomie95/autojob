"""
tailor.py — Build tailored application content from a CV profile, offline.

This module is what replaced three Groq calls per job. Everything a job
application needs — a reordered CV, a cover letter, a covering email — is
assembled here from the parsed profile plus the match data the scorer
already computed. No API call, no quota, no network.

What "tailoring" actually means, mechanically:

  Selection   Pick the experience entries and projects whose stacks overlap
              the posting, and rank them most-relevant-first. A DevOps
              posting should lead with the DevOps role, not with whatever
              happens to be at the top of the CV.

  Emphasis    Within an entry, order bullets by how many of the posting's
              skills they mention, so the first thing read is the most
              relevant thing there is.

  Slotting    Compose prose from the profile's own words and the specific
              overlap with this posting. The candidate's real bullets, real
              project names, and real stack — recombined, never invented.

Nothing here fabricates content. Every string that reaches an employer
traces back to something in the CV. That property is what makes it safe to
run without a human reviewing each send, and it is the reason to prefer
templates over generation for the bulk of jobs.

An optional LLM polish pass (see document_generator) rewrites the prose for
high-scoring jobs only, so the token budget goes where it changes an outcome.
"""

from __future__ import annotations

import logging
import random
import re

from core import vocab
from core.scorer import tokenize

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Relevance ranking
# ──────────────────────────────────────────────────────────────

def years_phrase(years: float) -> str:
    """
    Years as a person would say them. Nobody writes "10.2 years of
    experience" in a cover letter.

    Always rounds down, never up: 10.8 years reads as "10+ years". Rounding
    up would overstate the candidate's experience to an employer, and the
    "+" carries the remainder honestly.
    """
    whole = int(years)  # floor — never round experience upward
    if whole < 1:
        return ""
    if whole == 1:
        return "1+ year"
    return f"{whole}+ years"


def _entry_text(entry: dict) -> str:
    return " ".join([
        entry.get("title", ""),
        entry.get("stack", ""),
        entry.get("company", ""),
        " ".join(entry.get("bullets", [])),
    ])


def _relevance(entry: dict, job_skills: set[str], job_tokens: set[str]) -> float:
    """
    How relevant one CV entry is to this posting: shared canonical skills
    weigh most, raw token overlap breaks ties.
    """
    text = _entry_text(entry)
    entry_skills = set(vocab.match_skills(text).keys())
    skill_hits = len(entry_skills & job_skills)
    token_hits = len(tokenize(text) & job_tokens)
    return skill_hits * 10.0 + token_hits * 0.5


def rank_bullets(bullets: list[str], job_skills: set[str],
                 job_tokens: set[str], limit: int = 5) -> list[str]:
    """Order bullets by relevance to the posting, keeping the best few."""
    if not bullets:
        return []

    def weight(bullet: str) -> float:
        skills = set(vocab.match_skills(bullet).keys())
        return len(skills & job_skills) * 10.0 + \
            len(tokenize(bullet) & job_tokens) * 0.5

    # Stable sort keeps original CV order among equally relevant bullets,
    # which preserves the candidate's own narrative ordering.
    ranked = sorted(bullets, key=weight, reverse=True)
    return ranked[:limit]


def rank_entries(entries: list[dict], job_skills: set[str],
                 job_tokens: set[str], limit: int) -> list[dict]:
    scored = [(e, _relevance(e, job_skills, job_tokens)) for e in entries]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [e for e, score in scored[:limit] if score > 0] or \
        [e for e, _ in scored[:limit]]


# ──────────────────────────────────────────────────────────────
# CV assembly
# ──────────────────────────────────────────────────────────────

def _job_context(job: dict, score_data: dict) -> tuple[set[str], set[str]]:
    text = f"{job.get('title', '')} {job.get('description', '')}"
    job_skills = set(vocab.match_skills(text).keys())
    # The scorer already worked out which of these the candidate actually
    # has; those matter most for emphasis.
    job_skills |= set(score_data.get("ats_keywords", []))
    return job_skills, tokenize(text)


def _profile_summary(profile: dict, job: dict, matched: list[str]) -> str:
    """
    A tailored profile paragraph, composed from the candidate's real
    seniority, years, and the specific overlap with this posting.
    """
    years = profile.get("years_experience", 0)
    level = profile.get("seniority", "mid")
    role = job.get("title", "the role")
    company = job.get("company", "your team")
    title = (profile.get("titles") or ["Engineer"])[0]

    level_word = {
        "junior": "", "mid": "", "senior": "Senior ",
        "staff": "Staff-level ", "principal": "Principal-level ",
    }.get(level, "")

    span = years_phrase(years)
    opening = (
        f"{level_word}{title} with {span} of hands-on experience"
        if span else f"{level_word}{title}"
    )

    top = [s for s in matched[:5]]
    if top:
        stack = ", ".join(top[:-1]) + f", and {top[-1]}" if len(top) > 1 else top[0]
        middle = f" Day-to-day work centres on {stack}."
    else:
        middle = ""

    # The candidate's own summary is deliberately not spliced in here. It
    # carries its own experience claim ("9+ years") which contradicts the
    # computed one, and two different numbers in one paragraph is worse than
    # a slightly plainer paragraph.
    closing = f" Applying for the {role} position at {company}."
    return f"{opening}.{middle}{closing}".strip()


def _core_skills(profile: dict, job_skills: set[str]) -> dict[str, list[str]]:
    """
    The candidate's skills, grouped by category, with skills the posting
    asks for listed first inside each group.
    """
    grouped: dict[str, list[str]] = {}
    for category, skills in (profile.get("skills_by_category") or {}).items():
        ordered = sorted(skills, key=lambda s: (s not in job_skills, s))
        if ordered:
            grouped[category] = ordered
    # Categories with a requested skill lead.
    return dict(sorted(
        grouped.items(),
        key=lambda kv: (not any(s in job_skills for s in kv[1]), kv[0]),
    ))


def build_cv_data(profile: dict, job: dict, score_data: dict) -> dict:
    """
    Assemble the CV payload for this posting. Same shape the DOCX writer
    already expects, so the rendering layer is unchanged.
    """
    job_skills, job_tokens = _job_context(job, score_data)
    matched = [s for s in score_data.get("ats_keywords", [])] or \
        sorted(set(profile.get("skills", [])) & job_skills)

    experience = rank_entries(profile.get("experience", []), job_skills,
                              job_tokens, limit=4)
    projects = rank_entries(profile.get("projects", []), job_skills,
                            job_tokens, limit=4)

    return {
        "identity": {
            "name": profile.get("name", ""),
            "email": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            "linkedin": profile.get("linkedin", ""),
            "github": profile.get("github", ""),
            "location": profile.get("location", ""),
            "titles": profile.get("titles", []),
        },
        "profile_summary": _profile_summary(profile, job, matched),
        "core_skills": _core_skills(profile, job_skills),
        "experience": [
            {
                "title": e.get("title", ""),
                "company": e.get("company", ""),
                "period": e.get("period", ""),
                "bullets": rank_bullets(e.get("bullets", []), job_skills,
                                        job_tokens, limit=5),
            }
            for e in experience
        ],
        "projects": [
            {
                "name": p.get("name", ""),
                "stack": p.get("stack", ""),
                "url": _project_url(p),
                "bullets": rank_bullets(p.get("bullets", []), job_skills,
                                        job_tokens, limit=3),
            }
            for p in projects
        ],
        "education": profile.get("education", []),
        "certifications": _rank_certifications(profile, job_skills),
        "ats_keywords_used": matched[:12],
    }


def _project_url(project: dict) -> str:
    """
    A project's URL: the one in the CV, else an explicit override from
    CANDIDATE_PROJECT_URLS. URLs are never guessed — a wrong link on a CV is
    worse than no link.
    """
    from config import config

    url = (project.get("url") or "").strip()
    if url:
        return url

    name = (project.get("name") or "").strip().lower()
    for key, mapped in (config.CANDIDATE_PROJECT_URLS or {}).items():
        if key.strip().lower() == name:
            return mapped
    return ""


def _rank_certifications(profile: dict, job_skills: set[str]) -> list[str]:
    """
    Certifications relevant to this posting first. A Kubernetes posting
    should lead with the CKA, not with whatever the CV listed first.
    """
    certs = profile.get("certifications", []) or []
    if not certs:
        return []

    def weight(cert: str) -> int:
        return len(set(vocab.match_skills(cert).keys()) & job_skills)

    return sorted(certs, key=weight, reverse=True)[:6]


# ──────────────────────────────────────────────────────────────
# Cover letter
# ──────────────────────────────────────────────────────────────

_OPENERS = [
    "I'm writing to apply for the {role} position at {company}.",
    "I'd like to be considered for the {role} role at {company}.",
    "I'm applying for the {role} opening at {company}.",
]

_CLOSERS = [
    "I'd welcome the chance to talk through how this experience maps onto "
    "what {company} needs. Thank you for your time.",
    "I'd be glad to discuss the role in more detail whenever suits you. "
    "Thanks for considering my application.",
    "Happy to walk through any of the above on a call. Thank you for "
    "reading.",
]


def _variant(options: list[str], seed: str) -> str:
    """
    Pick a variant deterministically from a seed, so re-running a job
    produces identical output while different jobs get different phrasing.
    """
    return options[hash(seed) % len(options)]


def _evidence_sentence(entry: dict, shared: list[str]) -> str:
    """One sentence of concrete evidence drawn from a real CV bullet."""
    bullets = entry.get("bullets") or []
    if not bullets:
        return ""
    bullet = bullets[0].rstrip(".")
    # Lowercase the leading verb so it reads inside a sentence.
    if bullet and bullet[0].isupper() and not bullet.startswith(("I ", "AWS", "CI")):
        bullet = bullet[0].lower() + bullet[1:]
    where = entry.get("company", "")
    prefix = f"At {where}, I " if where else "I "
    return f"{prefix}{bullet}."


def build_cover_letter(profile: dict, job: dict, contact: dict,
                       score_data: dict) -> dict:
    job_skills, job_tokens = _job_context(job, score_data)
    matched = score_data.get("ats_keywords", [])
    role = job.get("title", "the role")
    company = job.get("company", "your company")
    seed = f"{company}{role}"

    entries = rank_entries(profile.get("experience", []), job_skills,
                           job_tokens, limit=2)

    opening = _variant(_OPENERS, seed).format(role=role, company=company)
    span = years_phrase(profile.get("years_experience", 0))
    if span:
        opening += (
            f" I've spent {span} working as a "
            f"{(profile.get('titles') or ['engineer'])[0]}, and the overlap "
            f"with what you've described is direct."
        )

    body_1 = ""
    if matched:
        top = matched[:4]
        stack = ", ".join(top[:-1]) + f", and {top[-1]}" if len(top) > 1 else top[0]
        body_1 = f"You're asking for {stack}. That's the core of my day-to-day work. "
    if entries:
        body_1 += _evidence_sentence(entries[0], matched)

    body_2 = ""
    if len(entries) > 1:
        body_2 = _evidence_sentence(entries[1], matched)
    projects = rank_entries(profile.get("projects", []), job_skills,
                            job_tokens, limit=1)
    if projects and projects[0].get("name"):
        proj = projects[0]
        stack_note = f" ({proj['stack']})" if proj.get("stack") else ""
        body_2 += f" I also built {proj['name']}{stack_note}."

    closing = _variant(_CLOSERS, seed).format(company=company)

    return {
        "opening_paragraph": opening.strip(),
        "body_paragraph_1": body_1.strip(),
        "body_paragraph_2": body_2.strip(),
        "closing_paragraph": closing.strip(),
    }


# ──────────────────────────────────────────────────────────────
# Email
# ──────────────────────────────────────────────────────────────

_SUBJECTS = [
    "Application — {role} ({first})",
    "{role} role — quick intro",
    "{role} at {company} — application from {first}",
]


def build_email(profile: dict, job: dict, contact: dict,
                score_data: dict) -> dict:
    role = job.get("title", "the role")
    company = job.get("company", "your company")
    name = profile.get("name", "")
    first = name.split()[0] if name else "me"
    hr = contact.get("hr_name") or "Hiring Manager"
    seed = f"{company}{role}"

    job_skills, job_tokens = _job_context(job, score_data)
    matched = score_data.get("ats_keywords", [])
    entries = rank_entries(profile.get("experience", []), job_skills,
                           job_tokens, limit=1)

    subject = _variant(_SUBJECTS, seed).format(
        role=role, company=company, first=first)

    lines = [f"Hi {hr},", ""]
    source = job.get("source", "")
    where = f" on {source}" if source else ""
    lines.append(f"I saw the {role} opening at {company}{where} and would like to apply.")
    lines.append("")

    if matched:
        top = matched[:3]
        stack = ", ".join(top[:-1]) + f", and {top[-1]}" if len(top) > 1 else top[0]
        span = years_phrase(profile.get("years_experience", 0))
        sentence = f"I've worked with {stack}"
        if span:
            sentence += f" across {span}"
        lines.append(sentence + ".")
    if entries:
        lines.append(_evidence_sentence(entries[0], matched))
    lines.append("")
    lines.append("My CV is attached. Happy to jump on a call if it looks like a fit.")
    lines.append("")
    lines.append("Best,")
    for detail in [name, profile.get("email", ""), profile.get("phone", ""),
                   profile.get("linkedin", "")]:
        if detail:
            lines.append(detail)

    return {"subject": subject, "body": "\n".join(lines)}


# ──────────────────────────────────────────────────────────────
# Output validation
# ──────────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(
    r"\b(not specified|no details available|n/?a|unknown|tbd|placeholder|"
    r"lorem ipsum|xxx+|yyyy|\{[a-z_]+\})\b",
    re.IGNORECASE,
)


def validate_output(cv_data: dict, cl_data: dict, email_data: dict) -> list[str]:
    """
    Sanity-check generated content before it can reach an employer.

    With no human reviewing each send, this is the last line of defence: it
    catches unrendered template slots, placeholder text, and empty documents
    that would otherwise go out under the candidate's name.
    """
    problems: list[str] = []

    if not cv_data.get("experience") and not cv_data.get("projects"):
        problems.append("CV has neither experience nor projects")
    if len(cv_data.get("profile_summary", "")) < 40:
        problems.append("CV profile summary is empty or too short")
    if not cv_data.get("core_skills"):
        problems.append("CV has no skills listed")

    for key in ("opening_paragraph", "closing_paragraph"):
        if len(cl_data.get(key, "")) < 20:
            problems.append(f"cover letter {key} is empty")

    if not email_data.get("subject"):
        problems.append("email has no subject")
    if len(email_data.get("body", "")) < 80:
        problems.append("email body is too short")

    blob = " ".join([
        cv_data.get("profile_summary", ""),
        " ".join(cl_data.get(k, "") for k in cl_data),
        email_data.get("subject", ""),
        email_data.get("body", ""),
    ])
    for match in _PLACEHOLDER_RE.finditer(blob):
        problems.append(f"placeholder text in output: {match.group(0)!r}")
        break

    return problems
