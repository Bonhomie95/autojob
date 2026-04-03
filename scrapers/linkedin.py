import logging
import re
from urllib.parse import urlencode, quote_plus
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

GUEST_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
GUEST_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


class LinkedInScraper(BaseScraper):
    name = "linkedin"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        jobs = []
        seen_ids = set()

        for role in roles:
            logger.info(f"[LinkedIn] Scraping: {role}")
            params = {
                "keywords": role,
                "location": location,
                "f_WT": "2",          # Remote filter
                "f_TPR": "r86400",    # Past 24 hours
                "start": "0",
            }
            url = f"{GUEST_SEARCH_URL}?{urlencode(params)}"
            resp = self.get(url)
            if not resp:
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.find_all("div", class_="base-card")

            for card in cards:
                try:
                    job_id = card.get("data-entity-urn", "")
                    match = re.search(r":(\d+)$", job_id)
                    if not match:
                        continue
                    lid = match.group(1)
                    if lid in seen_ids:
                        continue
                    seen_ids.add(lid)

                    title_el = card.find("h3", class_="base-search-card__title")
                    company_el = card.find("h4", class_="base-search-card__subtitle")
                    location_el = card.find("span", class_="job-search-card__location")
                    link_el = card.find("a", class_="base-card__full-link")
                    date_el = card.find("time")

                    title = self._clean(title_el.text if title_el else "")
                    company = self._clean(company_el.text if company_el else "")
                    loc = self._clean(location_el.text if location_el else "")
                    url = link_el["href"].split("?")[0] if link_el else ""
                    posted_date = date_el.get("datetime", "") if date_el else ""

                    if not url or not title:
                        continue

                    description = self._fetch_description(lid)

                    jobs.append(
                        {
                            "title": title,
                            "company": company,
                            "location": loc,
                            "url": url,
                            "description": description,
                            "salary": "",
                            "posted_date": posted_date,
                            "source": self.name,
                        }
                    )
                except Exception as e:
                    logger.debug(f"[LinkedIn] Card parse error: {e}")

        logger.info(f"[LinkedIn] Found {len(jobs)} jobs")
        return jobs

    def _fetch_description(self, job_id: str) -> str:
        url = GUEST_DETAIL_URL.format(job_id)
        resp = self.get(url)
        if not resp:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        desc_el = soup.find("div", class_="description__text")
        if not desc_el:
            desc_el = soup.find("div", {"class": re.compile(r"show-more-less-html")})
        return self._clean(desc_el.get_text(separator=" ") if desc_el else "")
