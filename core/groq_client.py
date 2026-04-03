import re
import json
import time
import logging
from typing import Optional
from groq import Groq, RateLimitError
from config import config

logger = logging.getLogger(__name__)

# ── Key pool management ────────────────────────────────────
# Tracks which keys are exhausted and when they reset
_key_pool: list[dict] = []   # [{key, client, exhausted_until}]
_pool_ready = False


def _init_pool():
    global _key_pool, _pool_ready
    if _pool_ready:
        return
    keys = [k for k in config.GROQ_API_KEYS if k]
    if not keys:
        raise RuntimeError("No Groq API keys configured. Set GROQ_API_KEY or GROQ_API_KEY_1..N in .env")
    _key_pool = [{"key": k, "client": Groq(api_key=k), "exhausted_until": 0.0} for k in keys]
    logger.info(f"[Groq] Loaded {len(_key_pool)} API key(s)")
    _pool_ready = True


def _get_available_client() -> tuple[dict, int] | tuple[None, -1]:
    """Return the first available (non-exhausted) key entry and its index."""
    now = time.time()
    for i, entry in enumerate(_key_pool):
        if entry["exhausted_until"] <= now:
            return entry, i
    return None, -1


def _mark_exhausted(index: int, wait_secs: float):
    _key_pool[index]["exhausted_until"] = time.time() + wait_secs
    remaining = sum(1 for e in _key_pool if e["exhausted_until"] <= time.time())
    logger.warning(
        f"[Groq] Key #{index + 1} exhausted for {wait_secs:.0f}s. "
        f"{remaining}/{len(_key_pool)} key(s) still available."
    )


def _parse_wait_seconds(error_message: str) -> float:
    match = re.search(r"try again in\s+(?:(\d+)m)?(?:([\d.]+)s)?", str(error_message))
    if match:
        minutes = float(match.group(1) or 0)
        seconds = float(match.group(2) or 0)
        return minutes * 60 + seconds + 2
    return 65.0


# ── Public API ─────────────────────────────────────────────

def chat(system: str, user: str, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """
    Send a chat request using the key pool.
    - Rotates to the next available key when one is rate-limited.
    - If ALL keys are exhausted, waits for the soonest one to recover and retries.
    """
    _init_pool()

    for attempt in range(len(_key_pool) * 2 + 1):
        entry, idx = _get_available_client()

        if entry is None:
            # All keys exhausted — wait for the soonest to recover
            soonest = min(_key_pool, key=lambda e: e["exhausted_until"])
            wait = max(0.0, soonest["exhausted_until"] - time.time())
            logger.warning(f"[Groq] All keys exhausted. Waiting {wait:.0f}s for next key to recover…")
            time.sleep(wait + 1)
            entry, idx = _get_available_client()
            if entry is None:
                continue

        try:
            response = entry["client"].chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""

        except RateLimitError as e:
            err_str = str(e)
            wait_secs = _parse_wait_seconds(err_str)
            # Cap TPM waits; TPD waits must be honoured in full
            if "tokens per minute" in err_str or "TPM" in err_str:
                wait_secs = min(wait_secs, 65.0)
            _mark_exhausted(idx, wait_secs)
            # Immediately try next available key — don't sleep yet

        except Exception as e:
            logger.error(f"[Groq] Unexpected error on key #{idx + 1}: {e}")
            return ""

    logger.error("[Groq] All retries and keys exhausted.")
    return ""


def chat_json(system: str, user: str, temperature: float = 0.3, max_tokens: int = 2000) -> Optional[dict]:
    """Send a request and parse the JSON response. Always returns dict or None."""
    raw = chat(system, user, temperature, max_tokens)
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            try:
                result = json.loads(match.group(0))
                return result if isinstance(result, dict) else None
            except Exception:
                pass
        logger.warning(f"[Groq] Could not parse JSON: {cleaned[:200]}")
        return None

