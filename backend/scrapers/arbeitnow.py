"""
Arbeitnow — public JSON API, no auth, great for international/remote roles.
https://www.arbeitnow.com/api/job-board-api
"""
import re
import logging
import requests
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)
API_URL = "https://www.arbeitnow.com/api/job-board-api?page={page}"


class ArbeitnowScraper(BaseScraper):
    name = "arbeitnow"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        logger.info("[Arbeitnow] Fetching job feed…")

        role_words: set[str] = set()
        for role in roles:
            for word in role.lower().split():
                if len(word) > 3:
                    role_words.add(word)

        jobs: list[dict] = []
        seen: set[str] = set()

        for page in range(1, 4):  # 3 pages × ~100 jobs = up to 300
            try:
                resp = requests.get(
                    API_URL.format(page=page),
                    headers={"Accept": "application/json"},
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(f"[Arbeitnow] Page {page} failed: {e}")
                break

            items = data.get("data", [])
            if not items:
                break

            for item in items:
                # Only remote
                if not item.get("remote", False):
                    continue

                url = item.get("url", "")
                if not url or url in seen:
                    continue

                title = self._clean(item.get("title", ""))
                tags  = " ".join(item.get("tags", [])).lower()
                desc  = self._clean(re.sub(r"<[^>]+>", " ",
                                           item.get("description", "")))
                searchable = f"{title} {tags} {desc[:300]}".lower()

                if not any(w in searchable for w in role_words):
                    continue

                seen.add(url)
                jobs.append({
                    "title":       title,
                    "company":     self._clean(item.get("company_name", "")),
                    "location":    "Remote",
                    "url":         url,
                    "description": desc,
                    "salary":      "",
                    "posted_date": item.get("created_at", ""),
                    "source":      self.name,
                })

        logger.info(f"[Arbeitnow] Found {len(jobs)} matching jobs")
        return jobs
