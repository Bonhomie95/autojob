import logging
import re
import feedparser
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

RSS_FEEDS = {
    "programming": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "devops": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "fullstack": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
}


class WeWorkRemotelyScraper(BaseScraper):
    name = "weworkremotely"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        role_words = set()
        for role in roles:
            for word in role.lower().split():
                if len(word) > 3:
                    role_words.add(word)

        jobs = []
        seen = set()

        for feed_name, feed_url in RSS_FEEDS.items():
            logger.info(f"[WWR] Fetching feed: {feed_name}")
            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                logger.warning(f"[WWR] Feed error {feed_name}: {e}")
                continue

            for entry in feed.entries:
                try:
                    url = entry.get("link", "")
                    if not url or url in seen:
                        continue

                    title = self._clean(entry.get("title", ""))
                    # WWR titles are like "Company: Job Title"
                    if ": " in title:
                        company, job_title = title.split(": ", 1)
                    else:
                        company, job_title = "", title

                    # Relevance filter
                    searchable = f"{job_title} {entry.get('summary', '')}".lower()
                    if not any(word in searchable for word in role_words):
                        continue

                    seen.add(url)

                    # Use RSS summary as the primary description — always available
                    summary_html = entry.get("summary", "")
                    description = self._clean(
                        BeautifulSoup(summary_html, "lxml").get_text(separator=" ")
                    )

                    # Only attempt full-page fetch if RSS summary is very short
                    if len(description) < 200:
                        full_desc = self._fetch_description(url)
                        if full_desc:
                            description = full_desc

                    # Parse location from title suffix
                    loc_match = re.search(r"\[([^\]]+)\]", title)
                    loc = loc_match.group(1) if loc_match else "Remote"

                    jobs.append(
                        {
                            "title": self._clean(job_title),
                            "company": self._clean(company),
                            "location": loc,
                            "url": url,
                            "description": description,
                            "salary": "",
                            "posted_date": entry.get("published", ""),
                            "source": self.name,
                        }
                    )
                except Exception as e:
                    logger.debug(f"[WWR] Entry parse error: {e}")

        logger.info(f"[WWR] Found {len(jobs)} matching jobs")
        return jobs

    def _fetch_description(self, url: str) -> str:
        """Attempt to fetch full description — silently skip if blocked."""
        resp = self.get(url)
        if not resp:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        listing = soup.find("div", class_="listing-container")
        if not listing:
            listing = soup.find("section", class_="container-listing")
        return self._clean(listing.get_text(separator=" ") if listing else "")
