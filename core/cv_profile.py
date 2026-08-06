"""
cv_profile.py — Parse a CV into a structured profile, with no API calls.

This is the source of truth for "who is this candidate": the skills they
have, the titles they've held, how long they've worked, and what they've
built. Everything downstream — search query generation, match scoring,
document rendering — reads from the profile rather than from .env.

The parse is cached in the cv_profiles table keyed by a SHA-256 of the CV
file's bytes, so a given CV is parsed exactly once no matter how many runs
touch it. Editing the CV changes the hash and re-parses automatically.

Design notes:
  - Years of experience merges overlapping date ranges rather than summing
    them. Two concurrent jobs in 2020-2022 is two years of experience, not
    four, and naive summing is the single easiest way to inflate a profile
    into applying for roles it can't hold.
  - Seniority is derived primarily from years, and a title may lift it by at
    most one band. "Senior Engineer" on a two-year CV reads as mid, not
    senior.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from core import vocab
from core.cv_text import extract_cv_text, find_all, find_emails, find_phones

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Section splitting
# ──────────────────────────────────────────────────────────────

_SECTION_ALIASES: dict[str, list[str]] = {
    "summary": ["profile", "summary", "professional summary", "about",
                "objective", "profile summary"],
    "skills": ["skills", "core skills", "technical skills", "technologies",
               "tech stack", "competencies", "expertise"],
    "experience": ["experience", "professional experience", "work experience",
                   "employment", "employment history", "work history",
                   "career history"],
    "projects": ["projects", "featured projects", "key projects",
                 "selected projects", "personal projects", "portfolio"],
    "education": ["education", "academic background", "academic qualifications"],
    "certifications": ["certifications", "certificates", "licenses",
                       "certifications & licenses"],
}

def _stem(word: str) -> str:
    """
    Crude singulariser — enough to make "experiences" match "experience"
    and "certifications" match "certification". Not a real stemmer, and
    doesn't need to be: it only ever sees CV section headings.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _normalise_heading(line: str) -> str:
    """
    Map a heading line onto a canonical section name, or "" if not one.

    Matching is token-based rather than exact, because real CVs never use the
    exact words you expect: "PROFESSIONAL EXPERIENCES & PROJECTS",
    "Professional Objective:", and "CORE COMPETENCIES" all need to land on
    the right section.
    """
    bare = line.strip()
    # A dated line is an employment entry ("Landmark Technologies June 2021 –
    # June 2026"), never a section heading — and "Technologies" would
    # otherwise match the skills section.
    if not bare or len(bare) > 45 or _parse_ranges(bare):
        return ""

    stripped = re.sub(r"[^a-z& ]", " ", bare.lower())
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if not stripped:
        return ""

    words = {_stem(w) for w in stripped.split()} - {"and", "&", "the", "my"}
    if not words:
        return ""

    best, best_score = "", 0.0
    for canonical, aliases in _SECTION_ALIASES.items():
        for alias in aliases:
            alias_words = {_stem(w) for w in alias.split()}
            overlap = words & alias_words
            if not overlap:
                continue
            # Coverage asks "is the alias present?", precision asks "is this
            # line actually the heading, or just a line mentioning the word?".
            # Both matter: without precision, "CompTIA Security+ ce
            # Certification" reads as the Certifications heading.
            coverage = len(overlap) / len(alias_words)
            precision = len(overlap) / len(words)
            if coverage < 0.5 or precision < 0.5:
                continue
            score = coverage * precision
            if score > best_score:
                best, best_score = canonical, score

    return best


def split_sections(cv_text: str) -> dict[str, str]:
    """
    Split CV text into canonical sections. Text before the first recognised
    heading is returned under "header" — that's where the name and contact
    details almost always live.
    """
    sections: dict[str, list[str]] = {"header": []}
    current = "header"

    for line in cv_text.split("\n"):
        bare = line.strip()
        # A heading is short, title-ish, and matches a known alias.
        if bare and len(bare) <= 45:
            canonical = _normalise_heading(bare)
            if canonical:
                current = canonical
                sections.setdefault(current, [])
                continue
        sections.setdefault(current, []).append(line)

    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


# ──────────────────────────────────────────────────────────────
# Contact details
# ──────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d[\d\s.-]{7,14}\d")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+", re.I)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w\-]+", re.I)
_URL_RE = re.compile(r"https?://[^\s<>()\"',]+")


def _titlecase_name(name: str) -> str:
    """
    Title-case an all-caps name without mangling initials. str.title() turns
    "AMOS .O. ADEWOPO" into "Amos .O. Adewopo" correctly but "O'BRIEN" into
    "O'Brien" — which is right — and "MCDONALD" into "Mcdonald", which is the
    best a rule can do without a name database.
    """
    parts = []
    for word in name.split():
        stripped = word.strip(".")
        # A single letter is an initial: keep it upper and keep its dots.
        parts.append(word.upper() if len(stripped) <= 1 else word.capitalize())
    return " ".join(parts)


def _normalise_for_compare(field: str, value: str) -> str:
    """
    Canonical form used to decide whether two contact values are the same
    thing written differently. "(409)655-2769" and "+1 409 655 2769" are one
    number, and must not be offered as two choices.
    """
    if field == "phone":
        digits = re.sub(r"\D", "", value)
        return digits[-10:] if len(digits) >= 10 else digits
    if field in ("email", "linkedin", "github"):
        return value.strip().lower().rstrip("/")
    return " ".join(value.split()).lower()


def _extract_name(header: str, email: str) -> str:
    """
    The name is nearly always the first substantial line of the CV, before
    any contact details. Fall back to the email local-part.
    """
    for line in header.split("\n"):
        bare = line.strip()
        if not bare or len(bare) > 60:
            continue
        if _EMAIL_RE.search(bare) or _URL_RE.search(bare):
            continue
        if re.search(r"\d", bare):
            continue
        words = bare.split()
        # Middle initials carry punctuation — "AMOS .O. ADEWOPO" is a name,
        # and requiring every word to begin with a letter rejects it.
        if 1 < len(words) <= 5 and all(w.strip(".,") [:1].isalpha() for w in words if w.strip(".,")):
            return _titlecase_name(bare) if bare.isupper() else bare

    if email:
        local = email.split("@")[0]
        parts = re.split(r"[._-]", local)
        return " ".join(p.capitalize() for p in parts if p.isalpha())
    return ""


# Contact fields that can legitimately appear more than once in a CV, and the
# .env key that supplies a value when the CV has none.
CONTACT_FIELDS: dict[str, str] = {
    "name":     "CANDIDATE_NAME",
    "email":    "CANDIDATE_EMAIL",
    "phone":    "CANDIDATE_PHONE",
    "linkedin": "CANDIDATE_LINKEDIN",
    "github":   "CANDIDATE_GITHUB",
    "location": "CANDIDATE_LOCATION",
}


def _contact_options(cv_text: str, header: str) -> dict[str, list[str]]:
    """
    Every contact value the CV offers, per field, in order of appearance.

    Nothing is chosen here. A CV that lists two email addresses — an old one
    left in a template, a personal one beside a work one — is genuinely
    ambiguous, and picking for the user risks putting the wrong reply-to on a
    real application. Collect the candidates; let the choice happen later.
    """
    emails = find_emails(cv_text)
    # Phones live in the header block; scanning the whole document turns
    # every date and metric in the bullets into a phone-number candidate.
    phones = find_phones(header) or find_phones(cv_text[:1200])

    return {
        "name":     [n for n in [_extract_name(header, emails[0] if emails else "")] if n],
        "email":    emails,
        "phone":    phones,
        "linkedin": find_all(_LINKEDIN_RE, cv_text),
        "github":   find_all(_GITHUB_RE, cv_text),
        "location": [],
    }


def resolve_contacts(profile: dict, choices: dict[str, str] | None = None) -> dict:
    """
    Settle each contact field to a single value, and record what was ambiguous.

    Precedence, highest first:
      1. The user's saved choice for this CV.
      2. An explicit CANDIDATE_* value in .env.
      3. The first value found in the CV.

    A field is "ambiguous" when the CV offered more than one value and the
    user has not yet chosen. Those get surfaced in the UI rather than guessed
    at silently.
    """
    from config import config

    choices = choices or {}
    options = profile.get("contact_options") or {}
    ambiguous: list[str] = []

    for field, env_key in CONTACT_FIELDS.items():
        found: list[str] = []
        seen: set[str] = set()
        for value in (options.get(field) or []):
            key = _normalise_for_compare(field, value)
            if key and key not in seen:
                seen.add(key)
                found.append(value)

        # The .env value is a legitimate option even when the CV never says
        # it — but only if it's genuinely a different value, not the same one
        # formatted differently.
        env_value = (getattr(config, env_key, "") or "").strip()
        if env_value and _normalise_for_compare(field, env_value) not in seen:
            found.append(env_value)

        chosen = choices.get(field, "")
        match = next(
            (v for v in found
             if _normalise_for_compare(field, v) == _normalise_for_compare(field, chosen)),
            "",
        ) if chosen else ""

        if match:
            profile[field] = match
        elif env_value:
            # The .env value may be the same value the CV gives, written
            # differently. Resolve to the option that's actually in the list,
            # so the selected value is always one the user can see and click.
            profile[field] = next(
                (v for v in found
                 if _normalise_for_compare(field, v)
                 == _normalise_for_compare(field, env_value)),
                env_value,
            )
        else:
            profile[field] = found[0] if found else ""

        if len(found) > 1 and not match:
            ambiguous.append(field)

        profile.setdefault("contact_choices", {})[field] = {
            "value": profile[field],
            "options": found,
            "chosen": bool(match),
        }

    profile["ambiguous_fields"] = ambiguous
    profile["issues"] = validate_profile(profile)
    return profile


# ──────────────────────────────────────────────────────────────
# Date ranges
# ──────────────────────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_PRESENT = r"(?:present|current|now|date|ongoing)"
_DASH = r"[–—\-−]|to"

_RANGE_PATTERNS = [
    # Jan 2020 – Mar 2022  /  January 2020 to Present
    re.compile(
        rf"([a-z]{{3,9}})\.?\s+(\d{{4}})\s*(?:{_DASH})\s*(?:([a-z]{{3,9}})\.?\s+(\d{{4}})|{_PRESENT})",
        re.I,
    ),
    # 03/2020 – 05/2021
    re.compile(
        rf"(\d{{1,2}})/(\d{{4}})\s*(?:{_DASH})\s*(?:(\d{{1,2}})/(\d{{4}})|{_PRESENT})",
        re.I,
    ),
    # 2019 – Present  /  2018-2023
    re.compile(rf"(\d{{4}})\s*(?:{_DASH})\s*(?:(\d{{4}})|{_PRESENT})", re.I),
]


def _abs_month(year: int, month: int) -> int:
    return year * 12 + month


def _parse_ranges(text: str) -> list[tuple[int, int]]:
    """
    Find every date range in text, as (start, end) in absolute months.
    Open-ended ranges ("Present") end at today.
    """
    now = _abs_month(datetime.now().year, datetime.now().month)
    ranges: list[tuple[int, int]] = []

    for pattern in _RANGE_PATTERNS:
        for m in pattern.finditer(text):
            groups = m.groups()
            try:
                if pattern is _RANGE_PATTERNS[0]:
                    smon = _MONTHS.get(groups[0][:3].lower())
                    if not smon:
                        continue
                    start = _abs_month(int(groups[1]), smon)
                    if groups[2] and groups[3]:
                        emon = _MONTHS.get(groups[2][:3].lower())
                        if not emon:
                            continue
                        end = _abs_month(int(groups[3]), emon)
                    else:
                        end = now
                elif pattern is _RANGE_PATTERNS[1]:
                    start = _abs_month(int(groups[1]), int(groups[0]))
                    end = (_abs_month(int(groups[3]), int(groups[2]))
                           if groups[2] and groups[3] else now)
                else:
                    start = _abs_month(int(groups[0]), 1)
                    end = _abs_month(int(groups[1]), 12) if groups[1] else now
            except (ValueError, TypeError):
                continue

            # Reject nonsense: reversed, pre-1980, or in the future.
            if start > end or start < _abs_month(1980, 1) or end > now + 1:
                continue
            ranges.append((start, end))

        if ranges:
            break  # first pattern that matches wins — don't double-count

    return ranges


def _merge_months(ranges: list[tuple[int, int]]) -> float:
    """
    Total months covered by the union of ranges. Overlapping employment
    counts once — this is what keeps concurrent roles from inflating the
    profile's years of experience.
    """
    if not ranges:
        return 0.0
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return float(sum(end - start for start, end in merged))


_STATED_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*years?", re.I)


def _stated_years(text: str) -> float:
    """
    The largest "N+ years" figure the candidate claims in prose.

    Used only as a fallback when the experience section carries no employment
    date ranges to compute a span from. Many strong CVs open with "DevOps
    Engineer with 9+ years of experience" in the summary yet list their roles
    without inline dates (or the dates live in a sidebar the text extractor
    drops). Without this, such a profile reads as 0 years, and the scorer's
    realism gate then treats a senior candidate as junior — rejecting the very
    senior/mid roles they are most qualified for. Capped at 45 to ignore stray
    numbers, and only ever trusted when dated experience yields nothing.
    """
    best = 0.0
    for m in _STATED_YEARS_RE.finditer(text or ""):
        try:
            value = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if 0 < value <= 45:
            best = max(best, value)
    return best


# ──────────────────────────────────────────────────────────────
# Experience & projects
# ──────────────────────────────────────────────────────────────

_BULLET_RE = re.compile(r"^\s*[•▪◦*·]\s*(.+)$")

# Matches the date range itself so it can be cut out of a header line
# without splitting on the en-dash inside it.
_DATE_SPAN_RE = re.compile(
    rf"(?:[a-z]{{3,9}}\.?\s+)?(?:\d{{1,2}}/)?\d{{4}}\s*(?:{_DASH})\s*"
    rf"(?:(?:[a-z]{{3,9}}\.?\s+)?(?:\d{{1,2}}/)?\d{{4}}|{_PRESENT})",
    re.I,
)


def _looks_like_title(line: str) -> bool:
    bare = line.strip()
    if not bare or len(bare) > 70 or _BULLET_RE.match(line):
        return False
    words = re.sub(r"[^a-z ]", " ", bare.lower()).split()
    return any(w in vocab.TITLE_TOKENS for w in words) or bool(
        vocab.detect_seniority(bare)
    )


def _parse_experience(section: str) -> list[dict]:
    """
    Parse experience entries.

    Handles both real-world layouts:
        Sr DevOps Engineer                      <- title on its own line
        Landmark Technologies June 2021 – 2026  <- company + dates below
        • bullet

        Senior Engineer | Acme Corp | 2020 – Present   <- all on one line
        • bullet

    An entry is anchored by the line carrying the date range. The title is
    taken from that line if it holds one, otherwise from the line above.

    When the section carries no date ranges at all — a common layout where
    roles are listed as a bare title over bullets — fall back to a parser that
    anchors on the title line instead, so the roles are still captured.
    """
    if not section:
        return []

    if not _parse_ranges(section):
        return _parse_undated_experience(section)

    lines = [ln for ln in section.split("\n") if ln.strip()]
    entries: list[dict] = []
    current: dict | None = None
    prev_line = ""

    for line in lines:
        bare = line.strip()
        bullet = _BULLET_RE.match(line)
        ranges = _parse_ranges(bare)

        if ranges and not bullet:
            if current:
                entries.append(current)

            span = _DATE_SPAN_RE.search(bare)
            period = span.group(0).strip() if span else ""
            remainder = (bare.replace(period, " ") if period else bare)
            parts = [p.strip(" ,|·—-") for p in re.split(r"[|·]|\s{2,}", remainder)
                     if p.strip(" ,|·—-")]

            title, company = "", ""
            for part in parts:
                if not title and _looks_like_title(part):
                    title = part
                elif not company:
                    company = part
            # Company-and-dates on one line, title on the line above.
            if not title and _looks_like_title(prev_line):
                title = prev_line.strip()
                if not company and parts:
                    company = parts[0]

            current = {
                "title": title,
                "company": company,
                "period": period or bare,
                "bullets": [],
                "months": _merge_months(ranges),
            }
        elif current is not None:
            if bullet:
                text = bullet.group(1).strip()
                if len(text) > 15:
                    current["bullets"].append(text)
            elif current["bullets"]:
                # A wrapped continuation of the bullet above. Length is not a
                # useful filter here: a bullet ending "...deployed within" and
                # wrapping onto a line reading "environment" is exactly the
                # case that must be joined, and that tail is 11 characters.
                current["bullets"][-1] += " " + bare
            elif len(bare) > 15:
                current["bullets"].append(bare)

        if not bullet:
            prev_line = bare

    if current:
        entries.append(current)
    return entries


def _parse_undated_experience(section: str) -> list[dict]:
    """
    Parse experience from a section that carries no date ranges.

    Anchors each entry on a line that looks like a job title, then attaches the
    bullets beneath it. This is the fallback for CVs that list roles as a bare
    title over bullets with the dates omitted — the dated parser would return
    nothing for those, which reads downstream as "no experience" and, via a
    0-year profile, mislabels a senior candidate as junior.
    """
    entries: list[dict] = []
    current: dict | None = None

    for line in section.split("\n"):
        bare = line.strip()
        if not bare:
            continue
        bullet = _BULLET_RE.match(line)

        if bullet:
            if current is not None:
                text = bullet.group(1).strip()
                if len(text) > 10:
                    current["bullets"].append(text)
            continue

        if _looks_like_title(bare):
            if current:
                entries.append(current)
            parts = [p.strip(" ,|·—-") for p in re.split(r"[|·]|\s{2,}", bare)
                     if p.strip(" ,|·—-")]
            title, company = bare, ""
            if len(parts) > 1:
                title = next((p for p in parts if _looks_like_title(p)), parts[0])
                company = next((p for p in parts if p != title), "")
            current = {
                "title": title,
                "company": company,
                "period": "",
                "bullets": [],
                "months": 0.0,
            }
        elif current is not None:
            if current["bullets"]:
                # A wrapped continuation of the bullet above.
                current["bullets"][-1] += " " + bare
            elif not current["company"] and len(bare) <= 60:
                current["company"] = bare
            elif len(bare) > 10:
                current["bullets"].append(bare)

    if current:
        entries.append(current)
    return entries


def _parse_projects(section: str) -> list[dict]:
    """
    Parse projects. A project header is a short line that isn't a bullet;
    everything under it until the next header is its detail.
    """
    if not section:
        return []

    projects: list[dict] = []
    current: dict | None = None

    for line in section.split("\n"):
        bare = line.strip()
        if not bare:
            continue
        bullet = _BULLET_RE.match(line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)

        is_header = (
            not bullet
            and (numbered or (len(bare) <= 70 and not bare.endswith(".")))
        )

        if is_header:
            if current:
                projects.append(current)
            name = numbered.group(1).strip() if numbered else bare
            # "PulseQuiz — React Native, Firebase" → name + stack
            stack = ""
            split = re.split(r"\s+[–—|]\s+|\s+-\s+", name, maxsplit=1)
            if len(split) == 2:
                name, stack = split[0].strip(), split[1].strip()
            url_match = _URL_RE.search(line)
            current = {
                "name": name,
                "stack": stack,
                "url": url_match.group(0) if url_match else "",
                "bullets": [],
            }
        elif current is not None:
            text = bullet.group(1).strip() if bullet else bare
            url_match = _URL_RE.search(text)
            if url_match and not current["url"]:
                current["url"] = url_match.group(0)
            if len(text) > 15 and not _URL_RE.fullmatch(text):
                current["bullets"].append(text)

    if current:
        projects.append(current)

    return [p for p in projects if p["name"]]


_DEGREE_RE = re.compile(
    r"^\s*(bachelor|master|b\.?sc|m\.?sc|b\.?a\b|m\.?a\b|b\.?eng|m\.?eng|mba|"
    r"ph\.?d|doctorate|doctoral|diploma|associate degree|hnd|ond|"
    r"higher national|certificate in)\b",
    re.I,
)
_SCHOOL_RE = re.compile(
    r"\b(university|college|school|institute|polytechnic|academy|universit)\b",
    re.I,
)
_YEAR_RE = re.compile(r"\d{4}(?:\s*[–—\-]\s*(?:\d{4}|present))?", re.I)


def _parse_education(section: str) -> list[dict]:
    """
    Parse education entries.

    A degree is routinely spread over several lines with no bullets and no
    separators:

        Bachelor of Science
        Computer Science          <- field of study, not a second degree
        Tai Solarin University    <- school, not a third degree

    So lines are classified rather than counted: a degree keyword starts a
    new entry, a school keyword attaches to the open one, and anything else
    is the field of study.
    """
    if not section:
        return []

    entries: list[dict] = []
    current: dict | None = None

    def flush():
        nonlocal current
        if current and (current["degree"] or current["school"]):
            entries.append(current)
        current = None

    for line in section.split("\n"):
        bare = line.strip().lstrip("•▪◦-*· ")
        if len(bare) < 4:
            continue

        year_match = _YEAR_RE.search(bare)
        year = year_match.group(0) if year_match else ""

        # Single-line form: "BSc Computer Science | Lagos State Uni | 2018-2023"
        parts = [p.strip() for p in re.split(r"[|·]|\s{3,}", bare)
                 if p.strip() and p.strip() != year]
        if len(parts) > 1:
            flush()
            entries.append({
                "degree": parts[0],
                "school": next((p for p in parts[1:] if _SCHOOL_RE.search(p)),
                               parts[1]),
                "year": year,
            })
            continue

        text = bare.replace(year, "").strip(" ,-–—|") if year else bare

        if _DEGREE_RE.match(text):
            flush()
            current = {"degree": text, "school": "", "year": year}
        elif _SCHOOL_RE.search(text):
            if current is None:
                current = {"degree": "", "school": text, "year": year}
            else:
                current["school"] = text
                current["year"] = current["year"] or year
                flush()
        elif current is not None and not current["school"]:
            # A bare line under an open degree is its field of study.
            current["degree"] = f"{current['degree']}, {text}"
            current["year"] = current["year"] or year

    flush()
    return entries


def _parse_certifications(section: str) -> list[str]:
    """
    Certifications, one per line. These matter more than the CV template
    suggests — a CKA or an AWS Solutions Architect is a stronger signal for
    an infrastructure role than most experience bullets.
    """
    if not section:
        return []
    certs: list[str] = []
    for line in section.split("\n"):
        bare = line.strip().lstrip("•▪◦-*· ")
        if 4 < len(bare) <= 90 and not _RULE_ONLY.match(bare):
            certs.append(bare)
    return certs


_RULE_ONLY = re.compile(r"^[\W_]+$")


# ──────────────────────────────────────────────────────────────
# Derived signals
# ──────────────────────────────────────────────────────────────

def _derive_titles(experience: list[dict], summary: str) -> list[str]:
    """
    Titles the candidate has actually held, most recent first, normalised
    against the vocabulary where possible.
    """
    seen: list[str] = []

    def add(raw: str):
        cleaned = re.sub(r"\b(senior|sr|junior|jr|lead|staff|principal|intern)\b",
                         "", raw, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-|")
        if not cleaned:
            return
        # A known title always wins, whether or not it's already recorded —
        # falling through here would re-add it in a different case.
        for canonical in vocab.TITLES:
            if canonical.lower() in cleaned.lower():
                if canonical not in seen:
                    seen.append(canonical)
                return
        words = cleaned.lower().split()
        if any(w in vocab.TITLE_TOKENS for w in words):
            titled = cleaned.title()
            if not any(t.lower() == titled.lower() for t in seen):
                seen.append(titled)

    for entry in experience:
        add(entry.get("title", ""))
    if not seen:
        for line in summary.split("\n")[:4]:
            add(line.strip())
    return seen


def _derive_seniority(years: float, experience: list[dict]) -> str:
    """
    Seniority from years, allowing a held title to lift it by one band.
    Years lead because a title is self-reported and cheap; time isn't.
    """
    # Bands are deliberately wide. Staff and principal are scope-and-title
    # bands more than time-served ones — a ten-year engineer is a senior
    # engineer in almost every market, and treating them as principal makes
    # the scorer reject the senior roles they should actually be applying to.
    if years < 2:
        base = "junior"
    elif years < 5:
        base = "mid"
    elif years < 10:
        base = "senior"
    elif years < 15:
        base = "staff"
    else:
        base = "principal"

    titled = ""
    for entry in experience:
        level = vocab.detect_seniority(entry.get("title", ""))
        if level and (not titled or
                      vocab.SENIORITY_ORDER.index(level) >
                      vocab.SENIORITY_ORDER.index(titled)):
            titled = level

    base_idx = vocab.SENIORITY_ORDER.index(base)
    if titled:
        titled_idx = vocab.SENIORITY_ORDER.index(titled)
        if titled_idx > base_idx:
            return vocab.SENIORITY_ORDER[min(titled_idx, base_idx + 1)]
    return base


def _group_skills(skills: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for canonical, category in skills.items():
        grouped.setdefault(category, []).append(canonical)
    return grouped


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────

def build_profile(cv_text: str) -> dict:
    """Parse CV text into a structured profile. No network, no API."""
    sections = split_sections(cv_text)
    header = sections.get("header", "")
    summary = sections.get("summary", "")

    options = _contact_options(cv_text, header)
    experience = _parse_experience(sections.get("experience", ""))
    projects = _parse_projects(sections.get("projects", ""))
    education = _parse_education(sections.get("education", ""))
    certifications = _parse_certifications(sections.get("certifications", ""))

    # Skills are matched over the whole document: a stack named only in an
    # experience bullet counts just as much as one under a Skills heading.
    skills = vocab.match_skills(cv_text)

    # Years from the experience section only — project and education dates
    # are not employment.
    exp_ranges = _parse_ranges(sections.get("experience", ""))
    years = round(_merge_months(exp_ranges) / 12.0, 1)
    # No dated employment to measure? Trust a "N+ years" claim in the summary
    # rather than defaulting to 0, which would misread a senior CV as junior.
    if years <= 0:
        stated = _stated_years(f"{summary}\n{header}")
        if stated:
            years = stated

    titles = _derive_titles(experience, summary or header)
    seniority = _derive_seniority(years, experience)

    profile = {
        # Contact fields are filled in by resolve_contacts(), which needs the
        # user's saved choices and so cannot run at parse time.
        "contact_options": options,
        "summary": summary.strip(),
        "titles": titles,
        "skills": sorted(skills.keys()),
        "skills_by_category": _group_skills(skills),
        "years_experience": years,
        "seniority": seniority,
        "experience": experience,
        "projects": projects,
        "education": education,
        "certifications": certifications,
        # Whether the CV even had a projects section, so validation can tell
        # "you have no projects" from "your projects failed to parse".
        "had_projects_section": bool(sections.get("projects", "").strip()),
        "parsed_at": datetime.utcnow().isoformat(),
    }
    return resolve_contacts(profile)


def validate_profile(profile: dict) -> list[str]:
    """
    Structural sanity checks on a parsed profile.

    This is the automated gate that stands in for a human reviewer: the
    pipeline refuses to generate or send anything from a profile with
    blocking issues, because a bad parse reaches real employers otherwise.
    Returned strings are prefixed "BLOCK:" or "WARN:".
    """
    issues: list[str] = []

    if not profile.get("name"):
        issues.append("BLOCK: no candidate name found in CV")
    if not profile.get("email"):
        issues.append("BLOCK: no email address found in CV")
    if len(profile.get("skills", [])) < 3:
        issues.append(
            f"BLOCK: only {len(profile.get('skills', []))} known skill(s) matched — "
            "CV text may have failed to extract"
        )
    if not profile.get("titles"):
        issues.append("BLOCK: no job titles identified — cannot target a search")
    if not profile.get("experience") and not profile.get("projects"):
        issues.append("BLOCK: no experience or projects parsed — nothing to write about")

    # An unresolved email is a blocker, not a warning: sending applications
    # with the wrong reply-to address is unrecoverable, and with no human in
    # the loop there is nobody to catch it downstream.
    for field in profile.get("ambiguous_fields", []):
        options = (profile.get("contact_choices", {}).get(field, {})
                   .get("options", []))
        detail = f"CV gives {len(options)} values for {field} " \
                 f"({', '.join(options[:3])}) — pick the one to use"
        issues.append(("BLOCK: " if field == "email" else "WARN: ") + detail)

    if profile.get("years_experience", 0) <= 0:
        issues.append("WARN: no dated experience found — seniority is a guess")
    # Distinguish a parse failure from a CV that simply has no projects. The
    # first is a bug worth flagging; the second is just a fact about the CV,
    # and reporting it as a warning trains you to ignore warnings.
    if not profile.get("projects"):
        if profile.get("had_projects_section"):
            issues.append(
                "WARN: a projects section was found but no projects could be "
                "read from it — check its formatting"
            )
        else:
            issues.append(
                "INFO: no projects section in this CV — applications will lean "
                "on experience bullets alone. Adding one gives the tailor more "
                "to match against a posting."
            )
    if not profile.get("summary"):
        issues.append("WARN: no profile/summary section found")
    if profile.get("years_experience", 0) > 45:
        issues.append("WARN: implausible years of experience — check date parsing")

    return issues


def profile_is_sendable(profile: dict) -> tuple[bool, list[str]]:
    """True when no BLOCK-level issue is present."""
    issues = profile.get("issues") or validate_profile(profile)
    blockers = [i for i in issues if i.startswith("BLOCK:")]
    return (not blockers), blockers


# ──────────────────────────────────────────────────────────────
# Caching
# ──────────────────────────────────────────────────────────────

def cv_hash(cv_path: str) -> str:
    return hashlib.sha256(Path(cv_path).read_bytes()).hexdigest()


def get_profile(cv_path: str, cv_text: str = "") -> dict:
    """
    Return the profile for a CV file, parsing only when the file's contents
    have changed since the last parse.
    """
    from database import load_cv_profile, save_cv_profile, load_cv_choices

    digest = cv_hash(cv_path)
    choices = load_cv_choices(digest)

    cached = load_cv_profile(digest)
    if cached:
        logger.info(f"[Profile] Cache hit for {Path(cv_path).name}")
        # Choices are applied on read, not baked into the cached parse, so
        # changing one takes effect without re-reading the file.
        return resolve_contacts(cached, choices)

    text = cv_text or extract_cv_text(cv_path)
    if not text.strip():
        logger.error(f"[Profile] No text extracted from {cv_path}")
        return {"issues": [
                    "BLOCK: Couldn't read any text from this CV. It looks like a "
                    "scanned or image-only PDF that even OCR couldn't read. Try "
                    "exporting it as a text-based PDF (File → Save as PDF from Word/"
                    "Google Docs), or upload a .docx instead."
                ],
                "skills": [], "titles": [], "projects": [], "experience": [],
                "contact_options": {}, "ambiguous_fields": []}

    logger.info(f"[Profile] Parsing {Path(cv_path).name}…")
    profile = build_profile(text)
    profile["source_file"] = Path(cv_path).name
    profile["cv_hash"] = digest
    save_cv_profile(digest, Path(cv_path).name, profile)
    profile = resolve_contacts(profile, choices)

    logger.info(
        f"[Profile] {profile['name'] or '?'} — {len(profile['skills'])} skills, "
        f"{profile['years_experience']}y, {profile['seniority']}, "
        f"titles: {', '.join(profile['titles'][:3]) or 'none'}"
    )
    for issue in profile["issues"]:
        logger.warning(f"[Profile] {issue}")
    return profile
