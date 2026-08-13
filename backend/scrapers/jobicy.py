"""
Jobicy — has a public JSON API, no auth needed, no scraping.
https://jobicy.com/api/v2/remote-jobs
"""
import re
import logging
import requests
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)
API_URL = "https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}"


class JobicyScraper(BaseScraper):
    name = "jobicy"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        # Map roles → Jobicy tag keywords
        tag_words: set[str] = set()
        for role in roles:
            for word in role.lower().split():
                if len(word) > 3:
                    tag_words.add(word)

        jobs: list[dict] = []
        seen: set[str] = set()
        attempted_tags: set[str] = set()

        # Try most relevant tags (API supports one tag per request)
        priority = ["javascript", "python", "react", "node", "typescript",
                    "blockchain", "mobile", "backend", "frontend", "software"]
        tags_to_try = [t for t in priority if t in tag_words] or list(tag_words)[:5]

        for tag in tags_to_try:
            if tag in attempted_tags:
                continue
            attempted_tags.add(tag)
            try:
                resp = requests.get(API_URL.format(tag=tag),
                                    headers={"Accept": "application/json"}, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(f"[Jobicy] Tag '{tag}' failed: {e}")
                continue

            for item in data.get("jobs", []):
                url = item.get("url") or item.get("jobUrl", "")
                if not url or url in seen:
                    continue
                seen.add(url)

                desc = self._clean(re.sub(r"<[^>]+>", " ",
                                          item.get("jobDescription", "")))
                salary = ""
                lo = item.get("annualSalaryMin")
                hi = item.get("annualSalaryMax")
                if lo and hi:
                    salary = f"${int(lo):,} – ${int(hi):,}"
                elif lo:
                    salary = f"From ${int(lo):,}"

                jobs.append({
                    "title":       self._clean(item.get("jobTitle", "")),
                    "company":     self._clean(item.get("companyName", "")),
                    "location":    item.get("jobGeo", "Remote"),
                    "url":         url,
                    "description": desc,
                    "salary":      salary,
                    "posted_date": item.get("pubDate", ""),
                    "source":      self.name,
                })

        logger.info(f"[Jobicy] Found {len(jobs)} jobs")
        return jobs
