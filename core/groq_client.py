"""
groq_client.py — LLM client with Groq primary + Ollama fallback.

Execution order:
  1. Try each Groq key in the pool (rotating on rate limits).
  2. If ALL Groq keys are exhausted AND Ollama is reachable,
     fall back to the local Ollama models in priority order.
  3. If Ollama is also unavailable, wait for the soonest Groq key
     to recover and retry (original behaviour).

Ollama config (.env):
  OLLAMA_ENABLED=true
  OLLAMA_BASE_URL=http://localhost:11434   (default)
  OLLAMA_MODELS=qwen2.5-coder:32b,gemma3:12b,qwen3:6b,mistral
                ^ priority order — first reachable model is used

The Ollama fallback is transparent — callers (scorer, contact_extractor,
document_generator) don't need any changes.
"""

import re
import json
import time
import logging
import requests
from typing import Optional
from groq import Groq, RateLimitError
from config import config

logger = logging.getLogger(__name__)

# ── Groq key pool ─────────────────────────────────────────────
_key_pool: list[dict] = []
_pool_ready = False


def _init_pool():
    global _key_pool, _pool_ready
    if _pool_ready:
        return
    keys = [k for k in config.GROQ_API_KEYS if k]
    if not keys:
        logger.warning("[Groq] No API keys configured — will use Ollama only if enabled")
        _pool_ready = True
        return
    _key_pool = [{"key": k, "client": Groq(api_key=k), "exhausted_until": 0.0} for k in keys]
    logger.info(f"[Groq] Loaded {len(_key_pool)} API key(s)")
    _pool_ready = True


def _get_available_client() -> tuple[dict, int] | tuple[None, int]:
    now = time.time()
    for i, entry in enumerate(_key_pool):
        if entry["exhausted_until"] <= now:
            return entry, i
    return None, -1


def _all_exhausted() -> bool:
    now = time.time()
    return all(e["exhausted_until"] > now for e in _key_pool)


def _mark_exhausted(index: int, wait_secs: float):
    _key_pool[index]["exhausted_until"] = time.time() + wait_secs
    remaining = sum(1 for e in _key_pool if e["exhausted_until"] <= time.time())
    logger.warning(
        f"[Groq] Key #{index + 1} rate-limited for {wait_secs:.0f}s. "
        f"{remaining}/{len(_key_pool)} key(s) still available."
    )


def _parse_wait_seconds(error_message: str) -> float:
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)s?", str(error_message))
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2) or 0) + 2
    return 65.0


# ── Ollama fallback ───────────────────────────────────────────

def _ollama_enabled() -> bool:
    return str(getattr(config, "OLLAMA_ENABLED", "false")).lower() == "true"


def _ollama_base_url() -> str:
    return getattr(config, "OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _ollama_models() -> list[str]:
    raw = getattr(config, "OLLAMA_MODELS",
                  "qwen2.5-coder:32b,gemma3:12b,mistral:latest")
    return [m.strip() for m in raw.split(",") if m.strip()]


def _ollama_running_models() -> set[str]:
    """Return the set of models currently available in Ollama."""
    try:
        resp = requests.get(f"{_ollama_base_url()}/api/tags", timeout=5)
        if resp.ok:
            return {m["name"] for m in resp.json().get("models", [])}
    except Exception:
        pass
    return set()


def _ollama_chat(system: str, user: str,
                 temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """
    Try each configured Ollama model in priority order.
    Returns the first successful response, or empty string if all fail.
    """
    available = _ollama_running_models()
    if not available:
        logger.warning("[Ollama] No models available or Ollama not running")
        return ""

    for model in _ollama_models():
        # Accept prefix matches so "qwen3:6b" matches "qwen3:6b-instruct" etc.
        matched = next((a for a in available if a.startswith(model.split(":")[0])), None)
        if not matched:
            logger.debug(f"[Ollama] Model '{model}' not available — skipping")
            continue

        try:
            logger.info(f"[Ollama] Using fallback model: {matched}")
            resp = requests.post(
                f"{_ollama_base_url()}/api/chat",
                json={
                    "model":  matched,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                },
                timeout=120,   # local models can be slow
            )
            if resp.ok:
                content = resp.json().get("message", {}).get("content", "")
                if content:
                    logger.info(f"[Ollama] ✅ Response from {matched} ({len(content)} chars)")
                    return content
        except requests.exceptions.Timeout:
            logger.warning(f"[Ollama] Timeout on {matched} — trying next model")
        except Exception as e:
            logger.warning(f"[Ollama] Error on {matched}: {e}")

    logger.error("[Ollama] All configured models failed")
    return ""


# ── Public API ────────────────────────────────────────────────

def chat(system: str, user: str,
         temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """
    Send a chat request.

    Execution order:
      1. Try EVERY available Groq key in sequence, rotating on rate limits.
         A key is only skipped if it is actively rate-limited (exhausted_until > now).
      2. Only after ALL keys have been tried and failed do we fall back to Ollama.
      3. If Ollama is also unavailable, wait for the soonest Groq key to recover
         and retry once more before giving up.
    """
    _init_pool()

    if not _key_pool:
        # No Groq keys at all — go straight to Ollama
        if _ollama_enabled():
            return _ollama_chat(system, user, temperature, max_tokens)
        logger.error("[LLM] No Groq keys configured and Ollama disabled — cannot proceed")
        return ""

    # ── Pass 1: try every non-exhausted key ──────────────────────────────────
    # We iterate the full pool rather than using _get_available_client() so that
    # each key is tried exactly once per pass regardless of insertion order.
    keys_tried = 0
    for idx, entry in enumerate(_key_pool):
        if entry["exhausted_until"] > time.time():
            logger.debug(f"[Groq] Key #{idx + 1} is rate-limited — skipping")
            continue

        keys_tried += 1
        try:
            response = entry["client"].chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""

        except RateLimitError as e:
            err_str   = str(e)
            wait_secs = _parse_wait_seconds(err_str)
            if "tokens per minute" in err_str or "TPM" in err_str:
                wait_secs = min(wait_secs, 65.0)
            _mark_exhausted(idx, wait_secs)
            # Continue to next key — do NOT fall back to Ollama yet

        except Exception as e:
            logger.error(f"[Groq] Unexpected error on key #{idx + 1}: {e}")
            # Mark this key as briefly cooling off so we don't hammer it
            _mark_exhausted(idx, 10.0)
            # Continue to next key

    logger.warning(
        f"[Groq] All {len(_key_pool)} key(s) exhausted after pass 1 "
        f"({keys_tried} tried, {len(_key_pool) - keys_tried} already rate-limited)."
    )

    # ── Pass 2: Ollama fallback ───────────────────────────────────────────────
    if _ollama_enabled():
        logger.warning("[Groq] All keys exhausted — falling back to Ollama")
        result = _ollama_chat(system, user, temperature, max_tokens)
        if result:
            return result
        logger.warning("[Ollama] Fallback failed — will wait for a Groq key to recover")

    # ── Pass 3: wait for soonest Groq key, then one final attempt ────────────
    soonest = min(_key_pool, key=lambda e: e["exhausted_until"])
    wait    = max(0.0, soonest["exhausted_until"] - time.time())
    if wait > 0:
        logger.warning(f"[Groq] Waiting {wait:.0f}s for next key to recover…")
        time.sleep(wait + 1)

    for idx, entry in enumerate(_key_pool):
        if entry["exhausted_until"] > time.time():
            continue
        try:
            response = entry["client"].chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"[Groq] Recovery attempt failed on key #{idx + 1}: {e}")

    logger.error("[LLM] All Groq keys and Ollama exhausted — giving up.")
    return ""


def chat_json(system: str, user: str,
              temperature: float = 0.3, max_tokens: int = 2000) -> Optional[dict]:
    """Send a request and parse the JSON response. Returns dict or None."""
    raw = chat(system, user, temperature, max_tokens)
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines   = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", cleaned)
        if m:
            try:
                result = json.loads(m.group(0))
                return result if isinstance(result, dict) else None
            except Exception:
                pass
        logger.warning(f"[LLM] Could not parse JSON: {cleaned[:200]}")
        return None
