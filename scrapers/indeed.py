"""
Indeed scraper — uses RSS feeds (still functional as of 2026) with
indeed.com and uk.indeed.com as sources. The HTML scraper is kept as
a last-resort fallback but rarely works without residential proxies.

With Cloudflare WARP (UK exit) the RSS feeds work reliably.
"""
import logging
import re
import json
import feedparser
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# RSS endpoints — uk.indeed.com works well with UK IPs (e.g. WARP)
RSS_URLS = [
    "https://uk.indeed.com/rss",
    "https://www.indeed.com/rss",
]
HTML_URL = "https://www.indeed.com/jobs"
JOB_URL_TPL = "https://www.indeed.com/viewjob?jk={jk}"


class IndeedScraper(BaseScraper):
    name = "indeed"

    def __init__(self):
        super().__init__()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
            "Referer": "https://uk.indeed.com/",
        })

    def scrape(self, roles: list[str], location: str = "Remote") -> list[dict]:
        jobs: list[dict] = []
        seen: set[str] = set()
        loc = "Remote" if "remote" in location.lower() else location

        for role in roles:
            logger.info(f"[Indeed] Fetching RSS: {role}")
            found = self._scrape_rss(role, loc, seen)

            # If RSS gave nothing, try HTML as last resort
            if not found:
                logger.debug(f"[Indeed] RSS empty for '{role}' — trying HTML fallback")
                found = self._scrape_html(role, loc, seen)

            jobs.extend(found)

        logger.info(f"[Indeed] Found {len(jobs)} jobs")
        return jobs

    # ─────────────────────────────────────────────────────────
    # RSS (primary) — works from UK IPs without proxies
    # ─────────────────────────────────────────────────────────
    def _scrape_rss(self, role: str, loc: str, seen: set) -> list[dict]:
        params = {
            "q":       role,
            "l":       loc,
            "sort":    "date",
            "fromage": "7",
            "limit":   "50",
        }
        jobs = []
        for base_url in RSS_URLS:
            url = f"{base_url}?{urlencode(params)}"
            try:
                feed = feedparser.parse(url)
                entries = feed.get("entries", [])
                if not entries:
                    continue

                for entry in entries:
                    try:
                        job_url = entry.get("link", "")
                        if not job_url or job_url in seen:
                            continue

                        title   = self._clean(entry.get("title", ""))
                        company = self._clean(
                            entry.get("source", {}).get("title", "") or
                            self._extract_company_from_summary(entry.get("summary", ""))
                        )
                        summary_html = entry.get("summary", "")
                        description  = self._clean(
                            BeautifulSoup(summary_html, "lxml").get_text(separator=" ")
                        ) if summary_html else ""

                        # Extract location from description or title
                        location_str = self._extract_location(entry, loc)

                        # Extract salary if mentioned
                        salary = self._extract_salary_text(description)

                        if not title:
                            continue

                        seen.add(job_url)
                        jobs.append({
                            "title":       title,
                            "company":     company,
                            "location":    location_str,
                            "url":         job_url,
                            "description": description,
                            "salary":      salary,
                            "posted_date": entry.get("published", ""),
                            "source":      self.name,
                        })
                    except Exception as e:
                        logger.debug(f"[Indeed] RSS entry parse error: {e}")

                if jobs:
                    logger.debug(f"[Indeed] RSS source '{base_url.split('/')[2]}' gave {len(jobs)} results for '{role}'")
                    break  # got results from this source — don't try the next

            except Exception as e:
                logger.warning(f"[Indeed] RSS fetch error ({base_url}): {e}")
                continue

        return jobs

    def _extract_company_from_summary(self, html: str) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "lxml")
        # Indeed sometimes puts "Company: X" in the summary
        text = soup.get_text()
        m = re.search(r"Company:\s*(.+?)(?:\n|$)", text)
        return m.group(1).strip() if m else ""

    def _extract_location(self, entry: dict, fallback: str) -> str:
        # Try tags first
        for tag in entry.get("tags", []):
            term = tag.get("term", "")
            if term and len(term) < 60:
                return self._clean(term)
        # Try title suffix like "(Remote)" or "- London"
        title = entry.get("title", "")
        m = re.search(r"[(\-–]\s*([^)]+)\s*\)?$", title)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) < 40:
                return candidate
        return fallback

    def _extract_salary_text(self, text: str) -> str:
        m = re.search(
            r"(\$[\d,]+\s*[-–]\s*\$[\d,]+|\£[\d,]+\s*[-–]\s*\£[\d,]+"
            r"|\d+k?\s*[-–]\s*\d+k?\s*(?:USD|GBP|EUR|per year|/yr)?)",
            text, re.IGNORECASE
        )
        return m.group(0) if m else ""

    # ─────────────────────────────────────────────────────────
    # HTML fallback (unreliable without proxies)
    # ─────────────────────────────────────────────────────────
    def _scrape_html(self, role: str, loc: str, seen: set) -> list[dict]:
        params = {"q": role, "l": loc, "sort": "date", "fromage": "7"}
        url    = f"{HTML_URL}?{urlencode(params)}"
        resp   = self.get(url)
        if not resp:
            return []
        html = resp.text or ""
        if self._is_blocked(html):
            logger.warning(
                f"[Indeed] HTML bot-block for '{role}'. "
                "Enable proxies in .env or rely on RSS."
            )
            return []
        page_jobs = self._parse_serp_json(html, loc) or self._parse_serp_html(html, loc)
        result = []
        for j in page_jobs:
            if j["url"] not in seen:
                seen.add(j["url"])
                result.append(j)
        return result

    def _is_blocked(self, html: str) -> bool:
        markers = (
            "just a moment", "cf-browser-verification",
            "needs to review the security", "pardon our interruption",
            "verify you are a human",
        )
        first_2k = html[:2048].lower()
        return any(m in first_2k for m in markers)

    def _parse_serp_json(self, html: str, loc: str) -> list[dict]:
        m = re.search(
            r"window\.mosaic\.providerData\[[^\]]+\]\s*=\s*({.+?});\s*</script>",
            html, re.DOTALL
        )
        if not m:
            m = re.search(
                r'"jobmap"\s*:\s*({.+?})\s*,\s*"jobkeysWithTwoPaneEligibleJobs"',
                html, re.DOTALL
            )
        if not m:
            return []
        try:
            blob = json.loads(m.group(1))
        except Exception:
            return []
        results = self._walk_for_jobs(blob)
        return [r for r in (self._row_from_json(j, loc) for j in results) if r]

    def _walk_for_jobs(self, node, found=None) -> list[dict]:
        if found is None:
            found = []
        if isinstance(node, dict):
            jk = node.get("jobkey") or node.get("jk")
            if jk and (node.get("title") or node.get("displayTitle")) and node.get("company"):
                found.append(node)
            for v in node.values():
                self._walk_for_jobs(v, found)
        elif isinstance(node, list):
            for v in node:
                self._walk_for_jobs(v, found)
        return found

    def _row_from_json(self, r: dict, loc: str) -> dict | None:
        jk = r.get("jobkey") or r.get("jk")
        if not jk:
            return None
        title    = self._clean(r.get("title") or r.get("displayTitle", ""))
        company  = self._clean(r.get("company") or r.get("companyName", ""))
        location = self._clean(r.get("formattedLocation") or r.get("jobLocationCity") or loc)
        snippet  = self._clean(re.sub(r"<[^>]+>", " ", r.get("snippet") or r.get("jobDescription") or ""))
        sal      = r.get("salarySnippet") or r.get("estimatedSalary") or {}
        salary   = self._clean(str(sal.get("text") or sal.get("formattedRange") or "")) if isinstance(sal, dict) else ""
        return {
            "title":       title,
            "company":     company,
            "location":    location,
            "url":         JOB_URL_TPL.format(jk=jk),
            "description": snippet,
            "salary":      salary,
            "posted_date": r.get("formattedRelativeTime", ""),
            "source":      self.name,
        }

    def _parse_serp_html(self, html: str, loc: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        rows = []
        for a in soup.select("a[data-jk], a[href*='/viewjob']"):
            jk = a.get("data-jk")
            if not jk:
                m = re.search(r"[?&]jk=([a-z0-9]+)", a.get("href", ""))
                jk = m.group(1) if m else None
            if not jk:
                continue
            title_node = a.find(["h2", "span"]) or a
            title = self._clean(title_node.get_text(separator=" "))
            if not title:
                continue
            container = a.find_parent(["div", "li", "td"]) or a
            company  = self._clean(self._first_text(container, ["[data-testid='company-name']", ".companyName", "[class*='company']"]))
            location = self._clean(self._first_text(container, ["[data-testid='text-location']", ".companyLocation", "[class*='location']"])) or loc
            snippet  = self._clean(self._first_text(container, [".job-snippet", "[class*='snippet']"]))
            rows.append({
                "title": title, "company": company, "location": location,
                "url": JOB_URL_TPL.format(jk=jk), "description": snippet,
                "salary": "", "posted_date": "", "source": self.name,
            })
        return rows

    def _first_text(self, root, selectors: list[str]) -> str:
        for sel in selectors:
            el = root.select_one(sel)
            if el and el.get_text(strip=True):
                return el.get_text(separator=" ", strip=True)
        return ""
