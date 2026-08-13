import re
import logging
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.google.com/search"


class GoogleJobsScraper(BaseScraper):
    name = "google"

    def __init__(self):
        super().__init__()
        # Google requires more browser-like headers
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
        })

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        jobs = []
        seen = set()
        loc = "Remote" if "remote" in location.lower() else location

        for role in roles:
            logger.info(f"[Google] Scraping: {role}")
            params = {
                "q": f"{role} jobs {loc}",
                "ibp": "htl;jobs",
                "hl": "en",
                "gl": "us",
            }
            url = f"{SEARCH_URL}?{urlencode(params)}"
            resp = self.get(url)
            if not resp:
                logger.warning(f"[Google] No response for {role}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # Google Jobs results live in a specific div structure
            job_cards = soup.find_all("div", {"class": re.compile(r"PwjeAc|iFjolb|gws-plugins-horizon-jobs")})

            # Fallback: look for any structured job-like divs
            if not job_cards:
                job_cards = soup.find_all("li", {"class": re.compile(r"LL")})

            if not job_cards:
                # Try extracting from script tags (Google sometimes renders jobs in JSON-LD)
                json_jobs = self._extract_json_ld(soup)
                for j in json_jobs:
                    if j["url"] not in seen:
                        seen.add(j["url"])
                        jobs.append(j)
                continue

            for card in job_cards:
                try:
                    title_el = card.find(["h2", "h3", "div"], {"class": re.compile(r"BjJfJf|sH3znd|title")})
                    company_el = card.find("div", {"class": re.compile(r"vNEEBe|companyName|company")})
                    location_el = card.find("div", {"class": re.compile(r"Qk80Jf|location")})
                    date_el = card.find("span", {"class": re.compile(r"LL4CDc|date")})

                    title = self._clean(title_el.get_text() if title_el else "")
                    company = self._clean(company_el.get_text() if company_el else "")
                    loc_str = self._clean(location_el.get_text() if location_el else loc)
                    posted_date = self._clean(date_el.get_text() if date_el else "")

                    if not title:
                        continue

                    # Try to get the apply URL
                    link_el = card.find("a", href=True)
                    job_url = ""
                    if link_el:
                        href = link_el["href"]
                        if href.startswith("/url?q="):
                            job_url = href.split("/url?q=")[1].split("&")[0]
                        elif href.startswith("http"):
                            job_url = href
                    if not job_url:
                        job_url = f"https://www.google.com/search?{urlencode({'q': f'{title} {company} job apply'})}"

                    if job_url in seen:
                        continue
                    seen.add(job_url)

                    # Description from card
                    desc_el = card.find("div", {"class": re.compile(r"HBvzbc|job-snippet|description")})
                    description = self._clean(desc_el.get_text(separator=" ") if desc_el else "")

                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": loc_str,
                        "url": job_url,
                        "description": description,
                        "salary": "",
                        "posted_date": posted_date,
                        "source": self.name,
                    })
                except Exception as e:
                    logger.debug(f"[Google] Card parse error: {e}")

        logger.info(f"[Google] Found {len(jobs)} jobs")
        return jobs

    def _extract_json_ld(self, soup: BeautifulSoup) -> list[dict]:
        """Extract jobs from JSON-LD structured data in page."""
        import json
        jobs = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = [data]
                else:
                    continue
                for item in items:
                    if item.get("@type") == "JobPosting":
                        url = item.get("url", "") or item.get("sameAs", "")
                        if not url:
                            continue
                        org = item.get("hiringOrganization", {})
                        jobs.append({
                            "title": self._clean(item.get("title", "")),
                            "company": self._clean(org.get("name", "") if isinstance(org, dict) else ""),
                            "location": self._clean(str(item.get("jobLocation", {}))),
                            "url": url,
                            "description": self._clean(
                                re.sub(r"<[^>]+>", " ", item.get("description", ""))
                            )[:1000],
                            "salary": self._clean(str(item.get("baseSalary", ""))),
                            "posted_date": item.get("datePosted", ""),
                            "source": self.name,
                        })
            except Exception:
                pass
        return jobs
