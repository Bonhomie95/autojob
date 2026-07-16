"""
discovery.py — Derive what to search for from the CV itself.

Replaces the hand-maintained TARGET_ROLES / EXPERIENCE_LEVEL settings. The
queries come from titles the candidate has actually held, expanded through
the adjacency map in vocab, and ranked so the roles they've spent the most
time in are searched first.

Nothing here calls an API.
"""

from __future__ import annotations

import logging

from core import vocab

logger = logging.getLogger(__name__)

# Seniority prefixes to attach to queries, per band. Boards match these as
# plain text, so searching both the bare and prefixed forms widens the net
# without pulling in roles the candidate can't hold.
_SENIORITY_PREFIX: dict[str, list[str]] = {
    "intern":    ["", "Junior", "Graduate"],
    "junior":    ["", "Junior", "Entry Level"],
    "mid":       ["", "Mid Level"],
    "senior":    ["", "Senior"],
    "staff":     ["", "Senior", "Staff", "Lead"],
    "principal": ["", "Senior", "Staff", "Principal"],
}


def _title_weights(profile: dict) -> dict[str, float]:
    """
    Weight each held title by months spent in it. Recent and long-held roles
    should drive the search; a job held for three months a decade ago
    shouldn't pull the whole run toward it.
    """
    weights: dict[str, float] = {}
    for entry in profile.get("experience", []):
        raw = entry.get("title", "")
        months = float(entry.get("months") or 0)
        if not raw:
            continue
        for canonical in vocab.TITLES:
            if canonical.lower() in raw.lower():
                weights[canonical] = weights.get(canonical, 0) + max(months, 1)
                break
        else:
            cleaned = raw.strip().title()
            weights[cleaned] = weights.get(cleaned, 0) + max(months, 1)

    # A title from the profile with no dated entry still counts, just least.
    for title in profile.get("titles", []):
        weights.setdefault(title, 1.0)
    return weights


def derive_queries(profile: dict, max_queries: int = 8) -> list[str]:
    """
    Build the list of role queries to search job boards for, best first.
    """
    weights = _title_weights(profile)
    if not weights:
        logger.warning("[Discovery] No titles in profile — cannot derive queries")
        return []

    # Score adjacent titles at a discount so held roles always rank first.
    scored: dict[str, float] = {}
    for title, weight in weights.items():
        scored[title] = scored.get(title, 0) + weight
        for i, neighbour in enumerate(vocab.adjacent_titles(title)[1:]):
            scored[neighbour] = scored.get(neighbour, 0) + weight * (0.5 / (i + 1))

    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    queries = [title for title, _ in ranked[:max_queries]]

    logger.info(
        f"[Discovery] {len(queries)} queries from CV: {', '.join(queries[:5])}"
        + ("…" if len(queries) > 5 else "")
    )
    return queries


def derive_search_terms(profile: dict) -> list[str]:
    """
    Queries with seniority prefixes applied — for boards that match on free
    text rather than structured role fields.
    """
    prefixes = _SENIORITY_PREFIX.get(profile.get("seniority", "mid"), [""])
    terms: list[str] = []
    for query in derive_queries(profile, max_queries=4):
        for prefix in prefixes:
            term = f"{prefix} {query}".strip()
            if term not in terms:
                terms.append(term)
    return terms


def derive_tags(profile: dict, limit: int = 25) -> list[str]:
    """
    Lowercase tag set for tag-matching boards (RemoteOK et al): the
    candidate's skills plus the words of their target titles.
    """
    tags: list[str] = []
    for skill in profile.get("skills", []):
        tag = skill.lower()
        if tag not in tags:
            tags.append(tag)
        # "Node.js" also needs to match a "node" tag.
        bare = tag.split(".")[0].split("/")[0].strip()
        if bare and bare != tag and bare not in tags:
            tags.append(bare)

    for query in derive_queries(profile, max_queries=6):
        for word in query.lower().split():
            if len(word) > 3 and word not in tags:
                tags.append(word)

    return tags[:limit]


def describe(profile: dict) -> str:
    """One-line summary of what this profile will hunt for. For the run log."""
    from core.tailor import years_phrase

    queries = derive_queries(profile, max_queries=4)
    span = years_phrase(profile.get("years_experience", 0)) or "no dated experience"
    return (
        f"{profile.get('seniority', '?')} · {span} · "
        f"{len(profile.get('skills', []))} skills · "
        f"targeting: {', '.join(queries) or 'nothing'}"
    )
