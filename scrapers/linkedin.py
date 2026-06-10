"""
LinkedIn guest-API scraper.

Uses uk.linkedin.com when available (benefits from UK exit IPs like
Cloudflare WARP), falls back to www.linkedin.com. Does NOT fetch
individual job detail pages — card text is enough for Groq scoring
and avoids the 200+ request rate-limit trip.
"""
import logging
import re
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# UK endpoint works better with WARP / UK IP addresses
GUEST_SEARCH_URLS = [
    "https://uk.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
]


class LinkedInScraper(BaseScraper):
    name = "linkedin"

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        jobs: list[dict] = []
        seen_ids: set[str] = set()
        consecutive_blocks = 0

        for role in roles:
            if consecutive_blocks >= 2:
                logger.warning(
                    "[LinkedIn] Blocked twice in a row — stopping early. "
                    "Enable proxies in .env or try again later."
                )
                break

            logger.info(f"[LinkedIn] Scraping: {role}")
            params = {
                "keywords": role,
                "location": location,
                "f_WT":     "2",        # Remote
                "f_TPR":    "r604800",  # Past week
                "start":    "0",
            }

            resp = None
            used_url = None
            for base_url in GUEST_SEARCH_URLS:
                url  = f"{base_url}?{urlencode(params)}"
                resp = self.get(url)
                if resp and not self._is_blocked(resp.text or ""):
                    used_url = base_url
                    break
                resp = None

            if not resp:
                consecutive_blocks += 1
                continue

            html = resp.text or ""
            if self._is_blocked(html):
                logger.warning(f"[LinkedIn] Bot-block for '{role}'")
                consecutive_blocks += 1
                continue

            consecutive_blocks = 0
            logger.debug(f"[LinkedIn] Using endpoint: {used_url}")
            soup = BeautifulSoup(html, "lxml")
            cards = soup.find_all("div", class_="base-card") or soup.find_all("li")

            role_count = 0
            for card in cards:
                try:
                    job = self._parse_card(card)
                    if not job or job["_lid"] in seen_ids:
                        continue
                    seen_ids.add(job["_lid"])
                    job.pop("_lid", None)
                    jobs.append(job)
                    role_count += 1
                except Exception as e:
                    logger.debug(f"[LinkedIn] Card parse error: {e}")

            logger.info(f"[LinkedIn]   {role}: {role_count} jobs")

        logger.info(f"[LinkedIn] Found {len(jobs)} jobs total")
        return jobs

    def _is_blocked(self, html: str) -> bool:
        if len(html) < 200:
            return True
        first = html[:1024].lower()
        return any(s in first for s in (
            "authwall", "challenge", "captcha",
            "unusual activity", "<title>linkedin login",
        ))

    def _parse_card(self, card) -> dict | None:
        job_urn = card.get("data-entity-urn", "") if hasattr(card, "get") else ""
        m = re.search(r":(\d+)$", job_urn or "")
        lid = m.group(1) if m else ""
        if not lid:
            a = card.find("a", href=True)
            if a:
                m2 = re.search(r"/jobs/view/(\d+)", a["href"])
                lid = m2.group(1) if m2 else ""
        if not lid:
            return None

        title_el    = card.find(["h3", "h4"], class_=re.compile(r"title"))
        company_el  = card.find(["h4", "h3", "a"], class_=re.compile(r"subtitle"))
        location_el = card.find(["span"], class_=re.compile(r"location"))
        link_el     = card.find("a", class_=re.compile(r"full-link")) or card.find("a", href=True)
        date_el     = card.find("time")

        title   = self._clean(title_el.get_text() if title_el else "")
        company = self._clean(company_el.get_text() if company_el else "")
        loc     = self._clean(location_el.get_text() if location_el else "")
        url     = link_el["href"].split("?")[0] if link_el and link_el.get("href") else ""
        posted  = date_el.get("datetime", "") if date_el else ""

        if not (title and url):
            return None

        snippet_el = (
            card.find(class_=re.compile(r"snippet")) or
            card.find(class_=re.compile(r"show-more-less-html"))
        )
        description = self._clean(snippet_el.get_text(separator=" ")) if snippet_el else ""

        return {
            "_lid":        lid,
            "title":       title,
            "company":     company,
            "location":    loc,
            "url":         url,
            "description": description,
            "salary":      "",
            "posted_date": posted,
            "source":      self.name,
        }
