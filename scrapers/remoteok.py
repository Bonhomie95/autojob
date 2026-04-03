import logging
import re
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://remoteok.com/api"

ROLE_TAGS = {
    "react native developer": ["react-native", "react", "mobile"],
    "full stack engineer": ["fullstack", "full-stack", "javascript", "node"],
    "web3 engineer": ["web3", "blockchain", "solana", "ethereum"],
    "python developer": ["python", "django", "fastapi"],
    "backend engineer": ["backend", "node", "python", "go", "ruby"],
    "frontend engineer": ["frontend", "react", "vue", "angular"],
    "software developer": ["javascript", "typescript", "software"],
}


class RemoteOKScraper(BaseScraper):
    name = "remoteok"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        logger.info("[RemoteOK] Fetching job feed…")
        self.session.headers["Accept"] = "application/json"
        resp = self.get(API_URL)
        if not resp:
            return []

        try:
            data = resp.json()
        except Exception:
            return []

        # First entry is metadata, skip it
        raw_jobs = data[1:] if data else []

        # Build relevant tags from roles
        relevant_tags = set()
        for role in roles:
            for key, tags in ROLE_TAGS.items():
                if any(word in role.lower() for word in key.split()):
                    relevant_tags.update(tags)
        # Always include the raw role words too
        for role in roles:
            for word in role.lower().split():
                if len(word) > 3:
                    relevant_tags.add(word)

        jobs = []
        seen = set()

        for item in raw_jobs:
            try:
                if not isinstance(item, dict):
                    continue

                job_id = str(item.get("id", ""))
                if job_id in seen:
                    continue

                item_tags = [t.lower() for t in item.get("tags", [])]
                item_pos = item.get("position", "").lower()

                # Check relevance
                match = any(tag in item_tags for tag in relevant_tags) or any(
                    word in item_pos for word in relevant_tags
                )
                if not match:
                    continue

                seen.add(job_id)
                description = self._clean(
                    re.sub(r"<[^>]+>", " ", item.get("description", ""))
                )

                jobs.append(
                    {
                        "title": self._clean(item.get("position", "")),
                        "company": self._clean(item.get("company", "")),
                        "location": item.get("location", "Remote"),
                        "url": item.get("url", f"https://remoteok.com/remote-jobs/{job_id}"),
                        "description": description,
                        "salary": self._parse_salary(item),
                        "posted_date": item.get("date", ""),
                        "source": self.name,
                    }
                )
            except Exception as e:
                logger.debug(f"[RemoteOK] Item parse error: {e}")

        logger.info(f"[RemoteOK] Found {len(jobs)} matching jobs")
        return jobs

    def _parse_salary(self, item: dict) -> str:
        lo = item.get("salary_min")
        hi = item.get("salary_max")
        if lo and hi:
            return f"${int(lo):,} – ${int(hi):,}"
        if lo:
            return f"From ${int(lo):,}"
        return ""
