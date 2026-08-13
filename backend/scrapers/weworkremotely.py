import logging
import re
import feedparser
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

RSS_FEEDS = {
    "programming":  "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "devops":       "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "fullstack":    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "design":       "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "management":   "https://weworkremotely.com/categories/remote-management-finance-jobs.rss",
}


class WeWorkRemotelyScraper(BaseScraper):
    name = "weworkremotely"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        # Build a flat set of individual meaningful words AND full multi-word
        # phrases so "full stack" matches "fullstack" or "full-stack".
        role_words: set[str] = set()
        role_phrases: list[str] = []
        for role in roles:
            role_lower = role.lower().strip()
            role_phrases.append(role_lower)
            # normalised version — remove spaces and hyphens — "full stack" → "fullstack"
            role_phrases.append(re.sub(r"[\s\-]+", "", role_lower))
            for word in re.split(r"[\s\-/]+", role_lower):
                if len(word) > 3:
                    role_words.add(word)

        jobs: list[dict] = []
        seen: set[str] = set()

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
                    # WWR titles: "Acme Corp: Senior React Engineer [Anywhere]"
                    if ": " in title:
                        company, job_title = title.split(": ", 1)
                    else:
                        company, job_title = "", title

                    # Strip location tag like "[Anywhere]" from job title
                    job_title_clean = re.sub(r"\s*\[[^\]]+\]$", "", job_title).strip()

                    summary_html = entry.get("summary", "")
                    searchable = f"{job_title_clean} {BeautifulSoup(summary_html, 'lxml').get_text()}".lower()
                    # Normalise searchable text too
                    searchable_nospace = re.sub(r"[\s\-]+", "", searchable)

                    # Match on individual words OR full phrases (handles "full stack" ↔ "fullstack")
                    matched = (
                        any(word in searchable for word in role_words) or
                        any(phrase in searchable for phrase in role_phrases) or
                        any(phrase in searchable_nospace for phrase in role_phrases)
                    )
                    if not matched:
                        continue

                    seen.add(url)
                    description = self._clean(
                        BeautifulSoup(summary_html, "lxml").get_text(separator=" ")
                    )
                    # Fetch full description only if RSS summary is very short
                    if len(description) < 200:
                        full_desc = self._fetch_description(url)
                        if full_desc:
                            description = full_desc

                    # Location from title bracket, e.g. [USA] or [Anywhere]
                    loc_match = re.search(r"\[([^\]]+)\]", title)
                    loc = loc_match.group(1) if loc_match else "Remote"

                    jobs.append({
                        "title":       self._clean(job_title_clean),
                        "company":     self._clean(company),
                        "location":    loc,
                        "url":         url,
                        "description": description,
                        "salary":      "",
                        "posted_date": entry.get("published", ""),
                        "source":      self.name,
                    })
                except Exception as e:
                    logger.debug(f"[WWR] Entry parse error: {e}")

        logger.info(f"[WWR] Found {len(jobs)} matching jobs")
        return jobs

    def _fetch_description(self, url: str) -> str:
        resp = self.get(url)
        if not resp:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        listing = soup.find("div", class_="listing-container") or \
                  soup.find("section", class_="container-listing")
        return self._clean(listing.get_text(separator=" ") if listing else "")
