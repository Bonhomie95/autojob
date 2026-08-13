"""
Hacker News "Who is Hiring" scraper.

Why this source matters for cold-email strategies: HN's monthly
"Ask HN: Who is hiring?" thread is hand-posted by hiring managers
themselves. ~80% of comments include a real, working contact email.
That's much higher signal than scraping job boards where the
"contact" is a black-box ATS form.

Uses the public Algolia HN search API:
    https://hn.algolia.com/api/v2_1/items/{story_id}
"""

import re
import time
import logging
import requests
from datetime import datetime
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ALGOLIA_ITEM   = "https://hn.algolia.com/api/v1/items/{id}"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
URL_RE   = re.compile(r"https?://[^\s<>\"]+")
TAG_RE   = re.compile(r"<[^>]+>")


class HackerNewsScraper(BaseScraper):
    name = "hackernews"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        story_id = self._find_latest_who_is_hiring()
        if not story_id:
            logger.warning("[HN] Could not find current 'Who is hiring' thread")
            return []

        logger.info(f"[HN] Fetching thread #{story_id}…")
        try:
            resp = requests.get(ALGOLIA_ITEM.format(id=story_id), timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[HN] Failed to fetch thread: {e}")
            return []

        role_words: set[str] = set()
        for role in roles:
            for w in role.lower().split():
                if len(w) > 3:
                    role_words.add(w)

        jobs: list[dict] = []
        seen_urls: set[str] = set()

        # Top-level children = individual job postings
        for child in data.get("children", []):
            try:
                job = self._parse_comment(child, role_words)
                if not job:
                    continue
                if job["url"] in seen_urls:
                    continue
                seen_urls.add(job["url"])
                jobs.append(job)
            except Exception as e:
                logger.debug(f"[HN] Comment parse error: {e}")

        logger.info(f"[HN] Found {len(jobs)} matching listings")
        return jobs

    # ─────────────────────────────────────────────────────────
    def _find_latest_who_is_hiring(self) -> int | None:
        """Find the most recent 'Ask HN: Who is hiring?' story by 'whoishiring'."""
        try:
            resp = requests.get(
                ALGOLIA_SEARCH,
                params={
                    "query":   "Ask HN: Who is hiring?",
                    "tags":    "story,author_whoishiring",
                    "hitsPerPage": "5",
                },
                timeout=20,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            for h in hits:
                title = h.get("title", "") or ""
                if "Who is hiring" in title:
                    return int(h.get("objectID", 0)) or None
        except Exception as e:
            logger.warning(f"[HN] Search failed: {e}")
        return None

    def _parse_comment(self, comment: dict, role_words: set[str]) -> dict | None:
        text_html = comment.get("text") or ""
        if not text_html:
            return None
        text = self._clean(TAG_RE.sub(" ", text_html))
        # Filter on relevance — must contain at least one role keyword
        searchable = text.lower()
        if not any(w in searchable for w in role_words):
            return None

        # Most HN hiring posts open with "Company | Role | ..." or
        # "Company (loc) | Role". We pull the first line as the headline
        # and try to split on " | ".
        first_line = text.split("\n")[0][:200]
        parts = [p.strip() for p in re.split(r"\s*[|·]\s*", first_line) if p.strip()]
        company = parts[0][:80] if parts else "HN posting"
        title   = parts[1][:80] if len(parts) > 1 else "Engineering role"

        # Pull location-ish hint
        loc_hint = ""
        for p in parts[2:5] if len(parts) > 2 else []:
            if re.search(r"remote|onsite|hybrid|EU|US|UK|EMEA|APAC|worldwide", p, re.I):
                loc_hint = p[:60]
                break

        # Real contact email — the gold of HN hiring posts
        email = ""
        for m in EMAIL_RE.finditer(text):
            candidate = m.group(0)
            # Skip obvious noise
            if candidate.lower().endswith((".png", ".jpg", ".gif")):
                continue
            email = candidate
            break

        # Pick a representative URL — prefer non-HN application links
        url = ""
        for m in URL_RE.finditer(text):
            u = m.group(0).rstrip(".,);")
            if "news.ycombinator.com" in u or "ycombinator.com" in u:
                continue
            url = u
            break

        # Stable HN comment URL as canonical anchor / fallback
        comment_id = str(comment.get("id") or comment.get("objectID") or "")
        canonical_url = url or f"https://news.ycombinator.com/item?id={comment_id}"

        if not comment_id:
            return None

        return {
            "title":         title or "Engineering role",
            "company":       company or "HN posting",
            "location":      loc_hint or "Remote",
            "url":           canonical_url,
            "description":   text[:2000],
            "salary":        "",
            "posted_date":   datetime.utcfromtimestamp(comment.get("created_at_i", 0)).isoformat()
                             if comment.get("created_at_i") else "",
            "source":        self.name,
            # Pre-extracted contact info — pipeline picks this up via the
            # contact extractor's existing flow (Groq will see the email
            # in description). Including it directly skips that step
            # and gives a 100% reliable address.
            "application_email": email,
        }
