import time
import random
import logging
import requests
from abc import ABC, abstractmethod
from config import config

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Dead proxies are skipped for the rest of this Python process session
_dead_proxies: set[str] = set()


def _normalise_proxy(raw: str) -> str:
    """Ensure proxy string has a scheme. Bare host:port assumed socks5."""
    s = raw.strip()
    if s and "://" not in s:
        s = f"socks5://{s}"
    return s


def _proxy_dict(proxy_url: str) -> dict:
    return {"http": proxy_url, "https": proxy_url}


def _pick_proxy() -> dict:
    """
    Return a random live proxy dict, or {} if proxies are disabled / pool empty.
    """
    if not config.PROXY_ENABLED or not config.PROXY_LIST:
        return {}
    live = [p for p in config.PROXY_LIST if p not in _dead_proxies]
    if not live:
        logger.warning("[Proxy] All proxies exhausted — using direct connection")
        return {}
    return _proxy_dict(random.choice(live))


def _mark_dead(pdict: dict):
    url = pdict.get("http", "")
    if url:
        _dead_proxies.add(url)
        host = url.split("@")[-1] if "@" in url else url.split("://")[-1]
        logger.warning(f"[Proxy] Marked dead: {host}")


class BaseScraper(ABC):
    name: str = "base"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    def _session_for(self, proxy: dict) -> requests.Session:
        sess = requests.Session()
        sess.headers.update(HEADERS)
        if proxy:
            sess.proxies.update(proxy)
        return sess

    def get(self, url: str, **kwargs) -> requests.Response | None:
        """
        GET with automatic proxy rotation:
          - Each attempt picks a fresh proxy from the pool
          - Failed proxies are marked dead and skipped for the session
          - Falls back to direct connection when pool is exhausted
        """
        proxy = _pick_proxy()

        for attempt in range(3):
            try:
                time.sleep(random.uniform(1.5, 3.5))
                sess = self._session_for(proxy) if proxy else self.session
                resp = sess.get(url, timeout=25, **kwargs)
                resp.raise_for_status()
                if proxy:
                    host = list(proxy.values())[0].split("@")[-1]
                    logger.debug(f"[{self.name}] OK via {host[:40]}")
                return resp

            except requests.RequestException as e:
                logger.warning(
                    f"[{self.name}] Attempt {attempt + 1} failed "
                    f"{'(proxy)' if proxy else '(direct)'}: {e}"
                )
                if proxy:
                    _mark_dead(proxy)
                    proxy = _pick_proxy()
                time.sleep(2 ** attempt)

        return None

    # ------------------------------------------------------------------
    @abstractmethod
    def scrape(self, roles: list[str], location: str) -> list[dict]:
        ...

    def _clean(self, text: str | None) -> str:
        if not text:
            return ""
        return " ".join(str(text).split())
