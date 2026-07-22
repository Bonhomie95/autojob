import html
import re
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


# Telltales of UTF-8 bytes decoded as a single-byte charset upstream:
# "Estágio" arrives as "EstÃ¡gio". Some boards (RemoteOK) serve this straight
# from their own database, already escaped in the JSON, so it can't be fixed
# by setting a response encoding — it has to be repaired after parsing.
#
# The C1 range is the load-bearing half of this pattern. A Latin-1 decode
# turns UTF-8 continuation bytes into raw C1 controls, so "We’re" arrives as
# "Weâ\x80\x99re" — with no literal "â€" to match on. C1 characters carry no
# meaning in real text, so their presence alone is proof of mangling.
_MOJIBAKE_RE = re.compile(r"[-]|Ã[-¿]|â€|Â[ -¿]")


def _looks_mangled(text: str) -> bool:
    return bool(_MOJIBAKE_RE.search(text))


def _byte_to_char(b: int) -> str:
    """The character a single byte becomes when decoded as cp1252."""
    try:
        return bytes([b]).decode("cp1252")
    except UnicodeDecodeError:
        return chr(b)  # undefined cp1252 slot — survives as a raw C1 control


# A mangled UTF-8 sequence always looks the same: one character standing in
# for a lead byte (0xC0-0xFF), then one to three standing in for continuation
# bytes (0x80-0xBF). Matching that shape lets each damaged run be repaired on
# its own.
# Each byte has two possible stand-ins, because the damage happens through
# either charset: byte 0x80 shows up as "€" if cp1252 did the decoding, or as
# a raw U+0080 control if Latin-1 did. Both forms have to match.
_LEAD_CHARS = "".join(sorted(
    {_byte_to_char(b) for b in range(0xC0, 0x100)}
    | {chr(b) for b in range(0xC0, 0x100)}
))
_CONT_CHARS = "".join(sorted(
    {_byte_to_char(b) for b in range(0x80, 0xC0)}
    | {chr(b) for b in range(0x80, 0xC0)}
))
_MANGLED_RUN_RE = re.compile(
    f"[{re.escape(_LEAD_CHARS)}][{re.escape(_CONT_CHARS)}]{{1,3}}"
)


def _fix_mojibake(text: str) -> str:
    """
    Repair double-encoded text, one damaged run at a time.

    Repairing the whole string at once is tempting but wrong: a description
    holding both a mangled bullet ("â\\x80¢") and one genuine non-Latin-1
    character elsewhere — an emoji, a CJK name — cannot round-trip as a unit,
    so a whole-string repair bails and leaves every mangled run in place.
    Fixing each run independently repairs what is broken and leaves the rest
    alone.

    A run that doesn't decode as UTF-8 was never mangled, and is kept as-is —
    which is what protects legitimately accented text like "Développeur".
    """
    if not _looks_mangled(text):
        return text

    def repair(match: re.Match) -> str:
        run = match.group(0)
        try:
            decoded = _undo_byte_mangling(run).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return run
        return run if "�" in decoded else decoded

    return _MANGLED_RUN_RE.sub(repair, text)


def _undo_byte_mangling(text: str) -> bytes:
    """
    Recover the original bytes from text mangled by a single-byte decode.

    Neither cp1252 nor Latin-1 covers this alone. cp1252 is what mangles real
    web text (it owns "€" at 0x80, which Latin-1 lacks), but cp1252 leaves a
    few byte values undefined — 0x9D among them — and those survive as raw C1
    control characters, where Latin-1's 1:1 mapping is what's needed. Smart
    quotes hit both cases in a single string: "“" mangles through cp1252, "”"
    through the 0x9D hole.

    So: cp1252 per character, falling back to the raw code point for anything
    it can't express. Raises ValueError on a character above U+00FF that
    cp1252 rejects, which means the text was never mangled this way.
    """
    out = bytearray()
    for ch in text:
        try:
            out.extend(ch.encode("cp1252"))
        except UnicodeEncodeError:
            if ord(ch) > 0xFF:
                raise ValueError("not single-byte mangled text")
            out.append(ord(ch))
    return bytes(out)


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
        """
        Normalise scraped text: repair encoding damage, decode HTML entities,
        collapse whitespace.

        Entity decoding matters beyond display. This text is what the scorer
        tokenises and what the document generator quotes into a CV, so a title
        left as "Growth &amp; Success" ends up in an employer's inbox exactly
        like that.
        """
        if not text:
            return ""
        cleaned = _fix_mojibake(str(text))
        # Twice: some boards double-escape, leaving "&amp;amp;".
        cleaned = html.unescape(html.unescape(cleaned))
        # Non-breaking spaces survive unescape and break tokenisation.
        cleaned = cleaned.replace("\xa0", " ")
        return " ".join(cleaned.split())
