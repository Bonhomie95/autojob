"""
scorer.py — Score how well a job matches the candidate's profile.

Default mode is fully offline: no API calls, no quota, works with the
network down. LLM scoring remains available behind LLM_SCORING=true but is
not needed and is not the default.

How the offline score works:

  Skill overlap (0-55)
      Canonical skills shared between profile and posting, weighted by IDF
      so that a shared "Kubernetes" counts far more than a shared "Git".
      IDF is learned from the descriptions of every job ever scraped into
      this database, so it adapts to the boards you actually read rather
      than to a generic corpus.

  Title match (0-25)
      How close the posting's title is to a role the candidate has held.

  Seniority fit (-40 to +20)
      The realism gate. A posting demanding materially more experience than
      the candidate has is rejected outright; one well beneath them is
      penalised. This is what keeps a 3-year CV from applying to Principal
      roles, and it is applied to the final score rather than reported and
      ignored.

  Hard filters
      Blacklist keywords, minimum salary, and location. A job failing any of
      these scores 0 and is never worth spending a token on.
"""

from __future__ import annotations

import logging
import math
import re

from config import config
from core import vocab

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Tokenisation
# ──────────────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "have", "will", "are", "you",
    "our", "your", "from", "not", "but", "also", "they", "their", "been",
    "its", "can", "all", "more", "who", "work", "role", "team", "join",
    "about", "well", "into", "as", "an", "a", "in", "of", "to", "is", "be",
    "we", "or", "on", "it", "at", "by", "do", "if", "so", "up", "out", "one",
    "use", "new", "may", "per", "get", "has", "any", "was", "how", "than",
    "what", "such", "when", "then", "some", "other", "time", "very", "used",
    "high", "good", "strong", "great", "able", "us", "own", "across",
    "within", "both", "while", "should", "must", "each", "including",
    "experience", "skills", "looking", "based", "help", "using", "build",
    "building", "make", "working", "opportunity", "ensure", "support",
    "develop", "provide", "manage", "create", "years", "year", "job",
    "company", "position", "candidate", "requirements", "responsibilities",
}

# Short tokens that are real skills — the old scorer's blanket len>=3 filter
# silently dropped every one of these.
_SHORT_SKILLS = {"go", "r", "c", "ai", "ml", "js", "ts", "qa", "ci", "cd",
                 "aws", "gcp", "sql", "k8s", "sre", "api", "ui", "ux"}


def tokenize(text: str) -> set[str]:
    tokens = re.findall(r"\b[a-z][a-z0-9+#.]*\b", (text or "").lower())
    return {
        t for t in tokens
        if t not in _STOPWORDS and (len(t) >= 3 or t in _SHORT_SKILLS)
    }


# ──────────────────────────────────────────────────────────────
# IDF
# ──────────────────────────────────────────────────────────────

_idf_cache: dict[str, float] | None = None
_idf_docs = 0


def _load_idf() -> tuple[dict[str, float], int]:
    """
    Inverse document frequency over every job description scraped so far.
    Cached per process; call reset_idf_cache() after a scrape to pick up
    newly learned counts.
    """
    global _idf_cache, _idf_docs
    if _idf_cache is not None:
        return _idf_cache, _idf_docs

    from database import load_token_df

    df, total = load_token_df()
    _idf_docs = total
    _idf_cache = {}
    if total >= 20:
        for token, count in df.items():
            # Smoothed IDF, floored at 0 so a token in every document
            # contributes nothing rather than going negative.
            _idf_cache[token] = max(math.log((total + 1) / (count + 1)), 0.0)
    return _idf_cache, _idf_docs


def reset_idf_cache():
    global _idf_cache, _idf_docs
    _idf_cache = None
    _idf_docs = 0


def _idf(token: str) -> float:
    """
    Weight for a token. Falls back to a flat 1.0 until the corpus is big
    enough to be meaningful — a handful of jobs teaches nothing about which
    words are common.
    """
    idf, total = _load_idf()
    if total < 20:
        return 1.0
    # Unseen token: treat as rare, which is usually right for a niche stack.
    return idf.get(token, math.log(total + 1))


def learn_from_jobs(jobs: list[dict]):
    """Fold newly scraped descriptions into the IDF corpus."""
    from database import bump_token_df

    for job in jobs:
        text = f"{job.get('title', '')} {job.get('description', '')}"
        tokens = tokenize(text)
        if tokens:
            bump_token_df(tokens)
    reset_idf_cache()


# ──────────────────────────────────────────────────────────────
# Requirement parsing
# ──────────────────────────────────────────────────────────────

_YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?"
    r"(?:\s+of)?(?:\s+\w+){0,3}?\s*(?:experience|exp\b)",
    re.I,
)


def required_years(text: str) -> float:
    """
    Smallest "N years of experience" demanded anywhere in the posting.

    The minimum is the right read: a JD listing "3+ years" in one place and
    "5+ years" in another is describing a range, and the lower bound is the
    bar to clear.
    """
    found: list[float] = []
    for m in _YEARS_RE.finditer(text or ""):
        try:
            low = float(m.group(1))
            if 0 < low <= 30:
                found.append(low)
        except (TypeError, ValueError):
            continue
    return min(found) if found else 0.0


_SALARY_RE = re.compile(
    r"(?:[$€£]\s?|\busd\b\s*|\beur\b\s*)?(\d{2,3})(?:[,.](\d{3}))?\s*(k\b)?",
    re.I,
)


def parse_salary(text: str) -> int:
    """
    Lowest annual figure mentioned, normalised to whole units. Returns 0 when
    nothing parseable is present — callers must treat 0 as "unknown", never
    as "free".
    """
    if not text:
        return 0
    values: list[int] = []
    for m in _SALARY_RE.finditer(text):
        whole, thousands, k_suffix = m.group(1), m.group(2), m.group(3)
        try:
            if thousands:
                value = int(whole) * 1000 + int(thousands)
            elif k_suffix:
                value = int(whole) * 1000
            else:
                continue  # a bare 2-3 digit number is not a salary
        except (TypeError, ValueError):
            continue
        if 10_000 <= value <= 1_000_000:
            values.append(value)
    return min(values) if values else 0


# ──────────────────────────────────────────────────────────────
# Component scores
# ──────────────────────────────────────────────────────────────

# IDF-weighted matched skill weight at which the "depth" factor saturates —
# roughly a handful of genuine, distinctive shared skills. Below this a posting
# that coincidentally names one or two skills the CV happens to have cannot earn
# full marks just because those were the only skills it mentioned. This is what
# stops a secretary post that says "Agile" and "Jira" from scoring like a real
# engineering role, without needing the posting to list a specific number.
_SKILL_DEPTH_TARGET = 6.0

# Domain-relevance gate thresholds (see score_offline). A job is treated as
# off-domain only when it clears *both* bars: its title barely relates to any
# role the candidate has held, and it shares fewer than this many known skills
# with the CV. _TITLE_RELATED_FLOOR is on the 0-25 title scale — ~0.3 title
# overlap — so a genuinely adjacent title is never mistaken for off-domain.
_TITLE_RELATED_FLOOR = 8.0
_MIN_DOMAIN_SKILLS = 3


def _skill_score(profile: dict, job_text: str) -> tuple[float, list[str], list[str]]:
    """
    IDF-weighted overlap between profile skills and posting skills.

    The score rewards *absolute* demonstrated overlap first (depth) and the
    fraction of the posting's stack covered second (coverage). Depth-first is
    deliberate: matching twelve real technologies is a strong signal no matter
    how many other skills the JD lists, whereas a pure coverage ratio perversely
    rewarded thin, off-target posts (one coincidental match → ratio 1.0 → full
    marks) and penalised rich, on-target ones (match half of a long stack →
    ratio 0.5 → half marks).
    """
    job_skills = vocab.match_skills(job_text)
    cv_skills = set(profile.get("skills", []))

    if not job_skills:
        return 0.0, [], []

    matched = [s for s in job_skills if s in cv_skills]
    missing = [s for s in job_skills if s not in cv_skills]

    def weight(skill: str) -> float:
        # Weight a canonical skill by the rarest of its constituent tokens.
        tokens = tokenize(skill)
        return max((_idf(t) for t in tokens), default=1.0)

    got = sum(weight(s) for s in matched)
    total = sum(weight(s) for s in job_skills)

    coverage = got / total if total else 0.0
    depth = min(got / _SKILL_DEPTH_TARGET, 1.0)
    points = 55.0 * (0.7 * depth + 0.3 * coverage)

    return points, matched, missing


def _title_score(profile: dict, job_title: str) -> float:
    """How close the posting's title is to something the candidate has held."""
    if not job_title:
        return 0.0
    job_words = tokenize(job_title)
    if not job_words:
        return 0.0

    best = 0.0
    for held in profile.get("titles", []):
        held_words = tokenize(held)
        if not held_words:
            continue
        overlap = len(job_words & held_words) / len(held_words)
        best = max(best, overlap)

    # Adjacent roles count, at a discount.
    if best < 1.0:
        for held in profile.get("titles", []):
            for neighbour in vocab.adjacent_titles(held)[1:]:
                n_words = tokenize(neighbour)
                if n_words and (job_words & n_words):
                    overlap = len(job_words & n_words) / len(n_words)
                    best = max(best, overlap * 0.7)

    return best * 25.0


def _seniority_fit(profile: dict, job: dict, job_text: str,
                   relaxed: bool = False) -> tuple[float, str]:
    """
    The realism gate. Returns (adjustment, reason).

    An adjustment of REJECT means the posting is out of reach and should
    never be applied to, no matter how well the stack lines up.

    When ``relaxed`` is set (the SaaS default — "list jobs based on the CV
    regardless of experience"), an experience mismatch heavily penalises the
    score instead of hard-rejecting, so the posting still surfaces and the user
    decides. A large fixed penalty is applied so these rank below true matches.
    """
    def _out(adj: float, reason: str) -> tuple[float, str]:
        if relaxed and adj == REJECT:
            return -35.0, reason
        return adj, reason

    cv_years = float(profile.get("years_experience") or 0)
    cv_level = profile.get("seniority", "mid")
    cv_idx = vocab.SENIORITY_ORDER.index(cv_level) if cv_level in vocab.SENIORITY_ORDER else 2

    demanded = required_years(job_text)
    job_level = vocab.detect_seniority(job.get("title", "")) or \
        vocab.detect_seniority(job_text[:600])

    # Explicit years requirement is the hardest signal available.
    if demanded:
        if demanded > cv_years + 2:
            return _out(REJECT, (
                f"needs {demanded:.0f}y experience, CV has {cv_years:.1f}y"
            ))
        if demanded > cv_years:
            return -12.0, f"slightly under: needs {demanded:.0f}y, has {cv_years:.1f}y"

    if job_level:
        job_idx = vocab.SENIORITY_ORDER.index(job_level)
        gap = job_idx - cv_idx

        if gap >= 2:
            return _out(REJECT, f"{job_level} role, candidate is {cv_level}")
        if gap == 1:
            # One band up is a stretch, not a fantasy — worth applying to.
            return -10.0, f"stretch: {job_level} role, candidate is {cv_level}"
        if gap == 0:
            return 20.0, f"seniority match ({job_level})"
        if gap == -1:
            # One band down is a strong application, not a penalty. A staff
            # engineer is exactly who gets hired into a senior role.
            return 5.0, f"comfortably qualified ({job_level} role)"
        if gap == -2:
            return -10.0, f"overqualified: {job_level} role, candidate is {cv_level}"
        return -25.0, f"far below level: {job_level} role, candidate is {cv_level}"

    return 0.0, "seniority not stated"


REJECT = -1000.0


# ──────────────────────────────────────────────────────────────
# Hard filters
# ──────────────────────────────────────────────────────────────

def _blacklisted(title: str, description: str, blacklist: list[str]) -> str:
    combined = f"{title} {description}".lower()
    for kw in blacklist:
        if kw and kw.lower() in combined:
            return kw
    return ""


def _fails_salary(job: dict, job_text: str) -> str:
    """
    Reject only when a salary is stated and is below the floor. An unstated
    salary is not a rejection — most postings don't list one.
    """
    floor = int(getattr(config, "MIN_SALARY", 0) or 0)
    if not floor:
        return ""
    stated = parse_salary(job.get("salary", "")) or parse_salary(job_text[:1500])
    if stated and stated < floor:
        return f"salary {stated:,} below floor {floor:,}"
    return ""


def _fails_location(job: dict) -> str:
    location = (job.get("location") or "").lower()
    if not location:
        return ""
    if getattr(config, "REMOTE_ONLY", True):
        if "remote" in location or "anywhere" in location or "worldwide" in location:
            return ""
        countries = [c.strip().lower() for c in getattr(config, "TARGET_COUNTRIES", [])
                     if c.strip() and c.strip().lower() != "remote"]
        if countries and any(c in location for c in countries):
            return ""
        if countries or "remote" not in location:
            return f"not remote ({job.get('location')})"
    return ""


def _empty_score(**overrides) -> dict:
    base = {
        "score": 0,
        "match_reasons": [],
        "gaps": [],
        "ats_keywords": [],
        "seniority": "unknown",
        "seniority_note": "",
        "is_blacklisted": False,
        "blacklist_reason": "",
        "company_insight": "",
        "rejected_reason": "",
        "required_years": 0.0,
        "salary_parsed": 0,
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────
# Offline scorer
# ──────────────────────────────────────────────────────────────

def score_offline(profile: dict, job: dict, blacklist: list[str],
                  relaxed_experience: bool = False) -> dict:
    title = job.get("title", "")
    description = (job.get("description", "") or "")[:6000]
    job_text = f"{title}\n{description}"

    hit = _blacklisted(title, description, blacklist)
    if hit:
        return _empty_score(is_blacklisted=True, blacklist_reason=hit,
                            rejected_reason=f"blacklisted: {hit}")

    for check in (_fails_salary(job, job_text), _fails_location(job)):
        if check:
            return _empty_score(rejected_reason=check)

    if not description.strip():
        return _empty_score(rejected_reason="empty description")

    skill_pts, matched, missing = _skill_score(profile, job_text)
    title_pts = _title_score(profile, title)
    job_level = vocab.detect_seniority(title) or vocab.detect_seniority(job_text[:600])

    # Domain-relevance gate. A posting the candidate has no title relationship
    # to *and* barely any skill overlap with is off-domain — a secretary or
    # data-entry role on a software/devops run. Reject it outright rather than
    # let a coincidental skill or two float it above real matches. The check is
    # an AND: a clearly related title survives thin skills, and strong skill
    # overlap survives an unrecognised title, so genuine matches are never lost.
    off_domain = title_pts < _TITLE_RELATED_FLOOR and len(matched) < _MIN_DOMAIN_SKILLS
    if off_domain:
        shared = ", ".join(matched) if matched else "none"
        return _empty_score(
            score=0,
            seniority=job_level or "unstated",
            gaps=[f"Not in CV: {s}" for s in missing[:6]],
            rejected_reason=(
                f"off-domain: title unrelated to your roles and only "
                f"{len(matched)} shared skill(s) ({shared})"
            ),
        )

    seniority_pts, seniority_note = _seniority_fit(profile, job, job_text,
                                                   relaxed=relaxed_experience)

    if seniority_pts == REJECT:
        return _empty_score(
            seniority=job_level or "unknown",
            seniority_note=seniority_note,
            rejected_reason=f"unrealistic: {seniority_note}",
            required_years=required_years(job_text),
        )

    total = skill_pts + title_pts + seniority_pts
    score = max(0, min(round(total), 100))

    reasons = [f"Shared: {s}" for s in matched[:6]]
    if title_pts > 12:
        reasons.append(f"Title close to held role ({title})")
    if seniority_pts > 0:
        reasons.append(seniority_note.capitalize())

    logger.debug(
        f"[Scorer] {job.get('company')} / {title}: skills={skill_pts:.1f} "
        f"title={title_pts:.1f} seniority={seniority_pts:+.1f} → {score}"
    )

    return _empty_score(
        score=score,
        match_reasons=reasons,
        gaps=[f"Not in CV: {s}" for s in missing[:6]],
        ats_keywords=matched[:12],
        seniority=job_level or "unstated",
        seniority_note=seniority_note,
        required_years=required_years(job_text),
        salary_parsed=parse_salary(job.get("salary", "")) or parse_salary(job_text[:1500]),
    )


# ──────────────────────────────────────────────────────────────
# LLM scorer (opt-in via LLM_SCORING=true)
# ──────────────────────────────────────────────────────────────

_LLM_SYSTEM = """You are a job-matching assistant. Given a candidate profile and a
job description, score the match 0-100.

Return ONLY valid JSON, no markdown:
{
  "score": <integer 0-100>,
  "match_reasons": ["reason"],
  "gaps": ["gap"],
  "ats_keywords": ["keyword"],
  "seniority": "junior|mid|senior|staff|principal"
}

Be strict about seniority: if the role demands materially more experience
than the candidate has, score below 40."""


def _score_llm(profile: dict, job: dict, blacklist: list[str]) -> dict:
    from core.groq_client import chat_json

    title = job.get("title", "")
    description = (job.get("description", "") or "")[:3000]

    hit = _blacklisted(title, description, blacklist)
    if hit:
        return _empty_score(is_blacklisted=True, blacklist_reason=hit,
                            rejected_reason=f"blacklisted: {hit}")

    user = (
        f"CANDIDATE: {profile.get('seniority')}, "
        f"{profile.get('years_experience')} years\n"
        f"TITLES HELD: {', '.join(profile.get('titles', []))}\n"
        f"SKILLS: {', '.join(profile.get('skills', [])[:40])}\n\n"
        f"JOB: {title} at {job.get('company', '')}\n"
        f"DESCRIPTION:\n{description[:1500]}"
    )
    result = chat_json(_LLM_SYSTEM, user)
    if not isinstance(result, dict) or "score" not in result:
        logger.warning("[Scorer] LLM returned no usable score — using offline scorer")
        return score_offline(profile, job, blacklist)

    merged = _empty_score(**{k: v for k, v in result.items()
                             if k in _empty_score()})
    # The realism gate is not delegated to the model.
    adjustment, note = _seniority_fit(profile, job, f"{title}\n{description}")
    if adjustment == REJECT:
        return _empty_score(rejected_reason=f"unrealistic: {note}",
                            seniority_note=note)
    merged["seniority_note"] = note
    merged["score"] = max(0, min(int(merged.get("score", 0)), 100))
    return merged


# ──────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────

def score_job(profile: dict, job: dict, blacklist: list[str] | None = None,
              relaxed_experience: bool = False) -> dict:
    """
    Score a job against a parsed CV profile.

    Offline by default. Set LLM_SCORING=true to use Groq/Ollama instead — the
    realism gate still applies either way. With ``relaxed_experience`` (the SaaS
    default), an experience mismatch penalises rather than rejects, so jobs are
    surfaced based on CV fit regardless of seniority.
    """
    blacklist = blacklist if blacklist is not None else config.BLACKLIST_KEYWORDS

    if str(getattr(config, "LLM_SCORING", "false")).lower() == "true":
        return _score_llm(profile, job, blacklist)
    return score_offline(profile, job, blacklist, relaxed_experience=relaxed_experience)
