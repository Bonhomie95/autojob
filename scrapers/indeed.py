import logging
import re
import feedparser
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Indeed exposes public RSS feeds — no auth, no bot detection
RSS_URL = "https://www.indeed.com/rss"


class IndeedScraper(BaseScraper):
    name = "indeed"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        jobs = []
        seen = set()
        loc = "Remote" if "remote" in location.lower() else location

        for role in roles:
            logger.info(f"[Indeed] RSS scraping: {role}")
            feed_url = f"{RSS_URL}?q={role.replace(' ', '+')}&l={loc.replace(' ', '+')}&sort=date"
            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                logger.warning(f"[Indeed] Feed parse error for {role}: {e}")
                continue

            for entry in feed.entries:
                try:
                    url = entry.get("link", "")
                    if not url or url in seen:
                        continue
                    seen.add(url)

                    title = self._clean(entry.get("title", ""))
                    # Indeed RSS titles are "Job Title - Company - Location"
                    company, loc_str = "", ""
                    parts = title.split(" - ")
                    if len(parts) >= 3:
                        title = parts[0].strip()
                        company = parts[1].strip()
                        loc_str = parts[2].strip()
                    elif len(parts) == 2:
                        title = parts[0].strip()
                        company = parts[1].strip()

                    # Description from RSS summary (HTML)
                    summary_html = entry.get("summary", "")
                    description = self._clean(
                        BeautifulSoup(summary_html, "lxml").get_text(separator=" ")
                    )

                    # Try to extract salary from description
                    salary = ""
                    sal_match = re.search(
                        r"\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?(?:\s*(?:per\s+)?(?:year|yr|hour|hr|month))?",
                        description, re.IGNORECASE,
                    )
                    if sal_match:
                        salary = sal_match.group(0).strip()

                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": loc_str or loc,
                        "url": url,
                        "description": description,
                        "salary": salary,
                        "posted_date": entry.get("published", ""),
                        "source": self.name,
                    })
                except Exception as e:
                    logger.debug(f"[Indeed] Entry parse error: {e}")

        logger.info(f"[Indeed] Found {len(jobs)} jobs")
        return jobs
