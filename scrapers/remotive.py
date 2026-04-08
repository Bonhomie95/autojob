"""
Remotive — public JSON API, no auth, no scraping.
https://remotive.com/api/remote-jobs
"""
import re
import logging
import requests
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)
API_URL = "https://remotive.com/api/remote-jobs?limit=100"

CATEGORY_MAP = {
    "react native developer": "software-dev",
    "full stack engineer":    "software-dev",
    "web3 engineer":          "software-dev",
    "python developer":       "software-dev",
    "backend engineer":       "software-dev",
    "frontend engineer":      "software-dev",
    "software developer":     "software-dev",
}


class RemotiveScraper(BaseScraper):
    name = "remotive"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        logger.info("[Remotive] Fetching job feed…")
        try:
            resp = requests.get(
                API_URL + "&category=software-dev",
                headers={"Accept": "application/json"},
                timeout=25,
            )
            resp.raise_for_status()
            raw = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning(f"[Remotive] API failed: {e}")
            return []

        # Build keyword set from roles
        role_words: set[str] = set()
        for role in roles:
            for word in role.lower().split():
                if len(word) > 3:
                    role_words.add(word)

        jobs: list[dict] = []
        seen: set[str] = set()

        for item in raw:
            url = item.get("url", "")
            if not url or url in seen:
                continue

            title = self._clean(item.get("title", ""))
            tags  = " ".join(item.get("tags", [])).lower()
            searchable = f"{title} {tags}".lower()

            if not any(w in searchable for w in role_words):
                continue

            seen.add(url)
            desc = self._clean(re.sub(r"<[^>]+>", " ",
                                      item.get("description", "")))
            salary = self._clean(item.get("salary", ""))

            jobs.append({
                "title":       title,
                "company":     self._clean(item.get("company_name", "")),
                "location":    item.get("candidate_required_location", "Worldwide"),
                "url":         url,
                "description": desc,
                "salary":      salary,
                "posted_date": item.get("publication_date", ""),
                "source":      self.name,
            })

        logger.info(f"[Remotive] Found {len(jobs)} matching jobs")
        return jobs
