"""
Contact extractor.

Priority:
  1. Deterministic extraction from the posting text and URL.
  2. Hunter / Prospeo enrichment with key rotation.
  3. Search-engine lead discovery with headless Chromium when available.
  4. Optional AI fallback only if normal extraction found nothing useful.
  5. Reoon / MillionVerifier validation with key rotation.

Apollo is intentionally not used.
"""

from __future__ import annotations

import logging
import re
import threading
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

from config import config

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
URL_RE = re.compile(r"https?://[^\s<>()\"']+")

_BOARD_DOMAINS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "remoteok.com",
    "weworkremotely.com", "remotive.com", "jobicy.com", "arbeitnow.com",
    "wellfound.com", "greenhouse.io", "lever.co", "workable.com",
    "smartrecruiters.com", "ashbyhq.com", "jobvite.com", "myworkdayjobs.com",
    "icims.com", "bamboohr.com", "breezy.hr", "recruitee.com",
}
_HR_TERMS = ("hr", "human resources", "talent", "recruit", "people", "hiring", "careers", "jobs")
_BAD_EMAIL_PREFIXES = ("noreply", "no-reply", "donotreply", "privacy", "abuse", "support")
_KEY_STATE: dict[str, dict] = {}
_KEY_LOCK = threading.Lock()


def _key_pool(base_name: str, keys: list[str]) -> str | None:
    live_keys = [k for k in keys if k]
    if not live_keys:
        return None
    with _KEY_LOCK:
        state = _KEY_STATE.setdefault(base_name, {"idx": 0, "dead": set()})
        usable = [k for k in live_keys if k not in state["dead"]]
        if not usable:
            return None
        state["idx"] %= len(usable)
        key = usable[state["idx"]]
        state["idx"] = (state["idx"] + 1) % len(usable)
        return key


def _mark_dead(base_name: str, key: str):
    with _KEY_LOCK:
        _KEY_STATE.setdefault(base_name, {"idx": 0, "dead": set()})["dead"].add(key)


def _extract_domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.netloc or parsed.path).split(":")[0].lower()
        host = re.sub(r"^www\.", "", host).strip()
        return host if "." in host and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host) else ""
    except Exception:
        return ""


def _guess_company_domain(company: str, job_url: str, provided_domain: str = "") -> str:
    if provided_domain:
        return _extract_domain_from_url(provided_domain)
    domain = _extract_domain_from_url(job_url)
    if domain and not any(b in domain for b in _BOARD_DOMAINS):
        return domain
    return ""


def _valid_email(email: str) -> bool:
    if not EMAIL_RE.fullmatch(email or ""):
        return False
    local, domain = email.lower().split("@", 1)
    if local.startswith(_BAD_EMAIL_PREFIXES):
        return False
    return "." in domain


def _email_score(email: str, company_domain: str = "") -> int:
    local, domain = email.lower().split("@", 1)
    score = 20
    if company_domain and domain.endswith(company_domain.lower()):
        score += 35
    if any(term in local for term in _HR_TERMS):
        score += 30
    if local in ("jobs", "careers", "recruiting", "talent", "hr", "people"):
        score += 20
    return score


def _best_email(emails: list[str], company_domain: str = "") -> str:
    candidates = sorted(
        {e.lower().strip(".,;:()[]<>") for e in emails if _valid_email(e)},
        key=lambda e: _email_score(e, company_domain),
        reverse=True,
    )
    return candidates[0] if candidates else ""


def _explicit_contact(job: dict, domain: str) -> dict:
    # Rows loaded from SQLite carry None for unset columns, not "" — a job
    # dict from the scraper and a job dict from the database are not the same
    # shape, and joining None raises.
    text = " ".join([
        str(job.get(field) or "")
        for field in ("description", "url", "application_url",
                      "application_email", "hr_email")
    ])
    email = _best_email(EMAIL_RE.findall(text), domain)
    urls = URL_RE.findall(text)
    application_url = job.get("application_url") or job.get("url") or ""
    for u in urls:
        lu = u.lower()
        if any(word in lu for word in ("apply", "careers", "jobs", "greenhouse", "lever", "ashby")):
            application_url = u
            break
    return {
        "hr_name": "",
        "hr_title": "",
        "hr_email": email,
        "application_email": email if email and not any(t in email for t in ("hr@", "talent@", "recruit")) else "",
        "application_url": application_url,
        "company_domain": domain,
        "contact_notes": "Email from job posting" if email else "",
    }


def _hunter_lookup(domain: str) -> dict | None:
    for _ in range(len(config.HUNTER_API_KEYS) or 1):
        key = _key_pool("HUNTER_API_KEY", config.HUNTER_API_KEYS)
        if not key:
            return None
        try:
            resp = requests.get(
                "https://api.hunter.io/v2/domain-search",
                params={"domain": domain, "api_key": key, "type": "personal", "limit": 10},
                timeout=12,
            )
            if resp.status_code in (401, 402, 403, 429):
                _mark_dead("HUNTER_API_KEY", key)
                continue
            if not resp.ok:
                return None
            emails = resp.json().get("data", {}).get("emails", [])
            ranked = []
            for item in emails:
                email = item.get("value", "")
                if not _valid_email(email):
                    continue
                pos = " ".join([item.get("department", ""), item.get("position", "")]).lower()
                ranked.append((
                    1 if any(t in pos for t in _HR_TERMS) else 0,
                    item.get("confidence") or 0,
                    email,
                    item,
                ))
            if not ranked:
                return None
            item = sorted(ranked, reverse=True)[0][3]
            return {
                "email": item.get("value", ""),
                "name": f"{item.get('first_name','')} {item.get('last_name','')}".strip(),
                "position": item.get("position", ""),
                "source": "Hunter.io",
            }
        except Exception as ex:
            logger.debug(f"[Hunter] Failed for {domain}: {ex}")
            return None
    return None


def _prospeo_lookup(domain: str) -> dict | None:
    for _ in range(len(config.PROSPEO_API_KEYS) or 1):
        key = _key_pool("PROSPEO_API_KEY", config.PROSPEO_API_KEYS)
        if not key:
            return None
        try:
            resp = requests.post(
                "https://api.prospeo.io/domain-search",
                headers={"Content-Type": "application/json", "X-KEY": key},
                json={"company": domain, "limit": 10},
                timeout=12,
            )
            if resp.status_code in (401, 402, 403, 429):
                _mark_dead("PROSPEO_API_KEY", key)
                continue
            if not resp.ok:
                return None
            emails = resp.json().get("response", {}).get("email_list", [])
            ranked = []
            for item in emails:
                email = item.get("email", "")
                if _valid_email(email):
                    ranked.append((
                        1 if any(t in (item.get("job_title") or "").lower() for t in _HR_TERMS) else 0,
                        email,
                        item,
                    ))
            if not ranked:
                return None
            item = sorted(ranked, reverse=True)[0][2]
            return {
                "email": item.get("email", ""),
                "name": f"{item.get('first_name','')} {item.get('last_name','')}".strip(),
                "position": item.get("job_title", ""),
                "source": "Prospeo",
            }
        except Exception as ex:
            logger.debug(f"[Prospeo] Failed for {domain}: {ex}")
            return None
    return None


def _fetch_text(url: str) -> str:
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if resp.ok and "text/html" in resp.headers.get("content-type", ""):
            return BeautifulSoup(resp.text, "lxml").get_text(" ", strip=True)[:20000]
    except Exception:
        pass
    return ""


def _search_urls_requests(query: str, engine: str) -> list[str]:
    if engine == "duckduckgo":
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        selector = "a.result__a"
    elif engine == "bing":
        url = f"https://www.bing.com/search?q={quote_plus(query)}"
        selector = "li.b_algo h2 a"
    else:
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        selector = "a"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if not resp.ok:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        for a in soup.select(selector):
            href = a.get("href", "")
            if href.startswith("http") and "google.com/search" not in href:
                urls.append(href)
        return urls[: config.CONTACT_SEARCH_MAX_RESULTS]
    except Exception:
        return []


def _search_urls_chromium(query: str, engine: str) -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []
    urls: list[str] = []
    target = {
        "duckduckgo": f"https://duckduckgo.com/?q={quote_plus(query)}",
        "bing": f"https://www.bing.com/search?q={quote_plus(query)}",
        "google": f"https://www.google.com/search?q={quote_plus(query)}",
    }.get(engine, f"https://duckduckgo.com/?q={quote_plus(query)}")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            for href in page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)"):
                if href.startswith("http") and not any(skip in href for skip in ("google.com/search", "duckduckgo.com/y.js")):
                    urls.append(href)
            browser.close()
    except Exception as ex:
        logger.debug(f"[Search] Chromium failed: {ex}")
    return urls[: config.CONTACT_SEARCH_MAX_RESULTS]


def _search_leads(company: str, domain: str) -> dict | None:
    if not config.CONTACT_SEARCH_ENABLED:
        return None
    engines = [e.strip().lower() for e in config.CONTACT_SEARCH_ENGINE.split(",") if e.strip()]
    queries = [
        f'site:{domain} (recruiter OR "talent acquisition" OR careers OR jobs) email' if domain else "",
        f'"{company}" recruiter email',
        f'"{company}" "talent acquisition" email',
        f'"{company}" careers email',
    ]
    emails: list[str] = []
    application_url = ""
    for query in [q for q in queries if q]:
        for engine in engines:
            urls = _search_urls_chromium(query, engine) or _search_urls_requests(query, engine)
            for url in urls:
                if not application_url and any(w in url.lower() for w in ("careers", "jobs", "apply")):
                    application_url = url
                text = _fetch_text(url)
                emails.extend(EMAIL_RE.findall(text + " " + url))
                best = _best_email(emails, domain)
                if best:
                    return {"email": best, "source": f"Search:{engine}", "application_url": application_url}
    return {"email": "", "source": "Search", "application_url": application_url} if application_url else None


def _verify_reoon(email: str) -> str:
    for _ in range(len(config.REOON_API_KEYS) or 1):
        key = _key_pool("REOON_API_KEY", config.REOON_API_KEYS)
        if not key:
            return "unknown"
        try:
            resp = requests.get("https://emailverifier.reoon.com/api/v1/verify",
                                params={"email": email, "key": key, "mode": "quick"}, timeout=12)
            if resp.status_code in (401, 402, 403, 429):
                _mark_dead("REOON_API_KEY", key)
                continue
            if not resp.ok:
                return "unknown"
            data = resp.json()
            status = str(data.get("status") or data.get("result") or "").lower()
            if status in ("safe", "valid"):
                return "valid"
            if status in ("invalid", "disabled", "disposable"):
                return "invalid"
            return "unknown"
        except Exception:
            return "unknown"
    return "unknown"


def _verify_million(email: str) -> str:
    for _ in range(len(config.MILLION_VERIFIER_API_KEYS) or 1):
        key = _key_pool("MILLION_VERIFIER_API_KEY", config.MILLION_VERIFIER_API_KEYS)
        if not key:
            return "unknown"
        try:
            resp = requests.get("https://api.millionverifier.com/api/v3/",
                                params={"api": key, "email": email, "timeout": 10}, timeout=15)
            if resp.status_code in (401, 402, 403, 429):
                _mark_dead("MILLION_VERIFIER_API_KEY", key)
                continue
            if not resp.ok:
                return "unknown"
            result = str(resp.json().get("result", "")).lower()
            if result in ("ok", "valid"):
                return "valid"
            if result in ("invalid", "disposable"):
                return "invalid"
            return "unknown"
        except Exception:
            return "unknown"
    return "unknown"


def _verify_email(email: str) -> tuple[bool, str]:
    if not email:
        return False, "missing"
    for name, fn in [("Reoon", _verify_reoon), ("MillionVerifier", _verify_million)]:
        result = fn(email)
        if result == "valid":
            return True, f"verified by {name}"
        if result == "invalid":
            return False, f"invalid by {name}"
    return True, "format-valid, verification unknown"


def _ai_fallback(job: dict) -> dict:
    if not config.CONTACT_AI_FALLBACK:
        return {}
    try:
        from core.groq_client import chat_json
        system = """Extract contact info explicitly present in this job posting.
Return only JSON with hr_name, hr_title, hr_email, application_email, application_url, company_domain, contact_notes.
Do not guess emails."""
        user = (
            f"TITLE: {job.get('title','')}\nCOMPANY: {job.get('company','')}\n"
            f"URL: {job.get('url','')}\nDESCRIPTION:\n{job.get('description','')[:2000]}"
        )
        result = chat_json(system, user, temperature=0.1, max_tokens=800)
        return result if isinstance(result, dict) else {}
    except Exception as ex:
        logger.debug(f"[Contact] AI fallback failed: {ex}")
        return {}


def _apply_contact(result: dict, contact: dict, label: str):
    if contact.get("email") and not result.get("hr_email"):
        result["hr_email"] = contact["email"]
        result["contact_notes"] = f"Email from {contact.get('source', label)}"
    if contact.get("name") and not result.get("hr_name"):
        result["hr_name"] = contact["name"]
    if contact.get("position") and not result.get("hr_title"):
        result["hr_title"] = contact["position"]
    if contact.get("application_url") and not result.get("application_url"):
        result["application_url"] = contact["application_url"]


def extract_contacts(job: dict) -> dict:
    company = str(job.get("company") or "")
    url = str(job.get("url") or "")
    domain = _guess_company_domain(company, url, str(job.get("company_domain") or ""))
    result = _explicit_contact(job, domain)

    if not result["hr_email"] and domain:
        for lookup_fn, label in [
            (_hunter_lookup, "Hunter.io"),
            (_prospeo_lookup, "Prospeo"),
        ]:
            contact = lookup_fn(domain)
            if contact:
                _apply_contact(result, contact, label)
                break

    if not result["hr_email"]:
        search_contact = _search_leads(company, domain)
        if search_contact:
            _apply_contact(result, search_contact, "Search")

    if not result["hr_email"]:
        ai = _ai_fallback(job)
        ai_domain = _guess_company_domain(company, url, ai.get("company_domain", ""))
        if ai_domain and not result["company_domain"]:
            result["company_domain"] = ai_domain
            domain = ai_domain
        email = _best_email([ai.get("hr_email", ""), ai.get("application_email", "")], domain)
        if email:
            result["hr_email"] = email
            result["contact_notes"] = "Email explicitly extracted by AI fallback"
        if ai.get("application_url") and not result.get("application_url"):
            result["application_url"] = ai["application_url"]

    email = result.get("hr_email") or result.get("application_email")
    if email:
        ok, note = _verify_email(email)
        if ok:
            result["contact_notes"] = "; ".join([n for n in [result.get("contact_notes"), note] if n])
        else:
            result["hr_email"] = ""
            result["application_email"] = ""
            result["contact_notes"] = note

    if not (result.get("hr_email") or result.get("application_email")):
        result["contact_notes"] = result.get("contact_notes") or "No verified email - apply via URL"
    result["application_url"] = result.get("application_url") or url
    result["company_domain"] = result.get("company_domain") or domain
    return result
