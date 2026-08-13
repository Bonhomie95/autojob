"""
groq_client.py — multi-provider LLM client.

``chat()``/``chat_json()`` dispatch on ``config.AI_PROVIDER`` (default
"groq"): groq | openai | anthropic | gemini | grok | openrouter. Whichever provider
is active, Ollama remains available as a local fallback (see below) — it's
independent of provider choice, not an alternative to it.

Groq gets the most elaborate handling (a rotating pool of keys, since its
free tier's per-key rate limits make pooling genuinely useful):
  1. Try each Groq key in the pool (rotating on rate limits).
  2. If ALL Groq keys are exhausted AND Ollama is reachable,
     fall back to the local Ollama models in priority order.
  3. If Ollama is also unavailable, wait for the soonest Groq key
     to recover and retry (original behaviour).

The other hosted providers (OpenAI, Anthropic, Gemini, Grok, OpenRouter) use
whichever key is configured for them, with no rotation — most users only
have one key per provider. See .env.example for where to get a key for each,
and README.md for a plain-language comparison.

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
# Rebuilt whenever the configured key list actually changes (not just once
# per process) — this is multi-tenant code now: engine_adapter.py shims a
# different user's keys into config.GROQ_API_KEYS on every run, and a
# stale, once-built pool would silently keep using whichever tenant's keys
# happened to be configured when this process first called chat().
_key_pool: list[dict] = []
_pool_keys_signature: tuple[str, ...] = ()


def _init_pool():
    global _key_pool, _pool_keys_signature
    keys = tuple(k for k in config.GROQ_API_KEYS if k)
    if keys == _pool_keys_signature:
        return
    _key_pool = [{"key": k, "client": Groq(api_key=k), "exhausted_until": 0.0} for k in keys]
    _pool_keys_signature = keys
    if keys:
        logger.info(f"[Groq] Loaded {len(_key_pool)} API key(s)")
    else:
        logger.warning("[Groq] No API keys configured — will use Ollama only if enabled")


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


# ── Other hosted providers ───────────────────────────────────
# Each takes the first configured key for that provider and makes one request
# — no pooling/rotation (unlike Groq above), since a user typically has just
# one key per extra provider. All raise on failure; callers below catch and
# log rather than letting an exception propagate out of chat().

def _model_or_default(default: str) -> str:
    return getattr(config, "AI_MODEL", "") or default


def _openai_compatible_chat(base_url: str, key: str, default_model: str,
                             system: str, user: str,
                             temperature: float, max_tokens: int) -> str:
    """
    Shared implementation for every provider that speaks OpenAI's
    chat-completions shape (OpenAI itself, xAI/Grok, OpenRouter — and Groq,
    though Groq gets its own pooled implementation above instead).
    """
    resp = requests.post(
        base_url,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": _model_or_default(default_model),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


def _openai_chat(system: str, user: str, temperature: float, max_tokens: int) -> str:
    return _openai_compatible_chat(
        "https://api.openai.com/v1/chat/completions", config.OPENAI_API_KEYS[0],
        "gpt-4o-mini", system, user, temperature, max_tokens,
    )


def _grok_chat(system: str, user: str, temperature: float, max_tokens: int) -> str:
    return _openai_compatible_chat(
        "https://api.x.ai/v1/chat/completions", config.GROK_API_KEYS[0],
        "grok-3-mini", system, user, temperature, max_tokens,
    )


def _anthropic_chat(system: str, user: str, temperature: float, max_tokens: int) -> str:
    key = config.ANTHROPIC_API_KEYS[0]
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": _model_or_default("claude-3-5-haiku-latest"),
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    resp.raise_for_status()
    parts = resp.json().get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _gemini_chat(system: str, user: str, temperature: float, max_tokens: int) -> str:
    key = config.GEMINI_API_KEYS[0]
    model = _model_or_default("gemini-2.0-flash")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        },
        timeout=60,
    )
    resp.raise_for_status()
    candidates = resp.json().get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _openrouter_chat(system: str, user: str, temperature: float, max_tokens: int) -> str:
    # OpenRouter speaks the same chat-completions shape as OpenAI. Model IDs
    # are provider-prefixed (e.g. "meta-llama/llama-3.3-70b-instruct:free") and
    # which free models exist changes over time — set AI_MODEL explicitly
    # rather than relying on this default for anything but a quick test.
    return _openai_compatible_chat(
        "https://openrouter.ai/api/v1/chat/completions", config.OPENROUTER_API_KEYS[0],
        "meta-llama/llama-3.3-70b-instruct:free", system, user, temperature, max_tokens,
    )


_PROVIDERS = {
    "openai": ("OPENAI_API_KEYS", _openai_chat),
    "anthropic": ("ANTHROPIC_API_KEYS", _anthropic_chat),
    "gemini": ("GEMINI_API_KEYS", _gemini_chat),
    "grok": ("GROK_API_KEYS", _grok_chat),
    "openrouter": ("OPENROUTER_API_KEYS", _openrouter_chat),
}


def _other_provider_chat(provider: str, system: str, user: str,
                          temperature: float, max_tokens: int) -> str:
    """Call a non-Groq hosted provider, falling back to Ollama on any failure."""
    attr, fn = _PROVIDERS[provider]
    keys = [k for k in getattr(config, attr, []) if k]
    if not keys:
        logger.error(f"[LLM] AI_PROVIDER={provider} but no {attr} configured")
    else:
        try:
            result = fn(system, user, temperature, max_tokens)
            if result:
                return result
            logger.warning(f"[{provider}] Empty response")
        except Exception as e:
            logger.error(f"[{provider}] Request failed: {e}")

    if _ollama_enabled():
        logger.warning(f"[{provider}] Falling back to Ollama")
        return _ollama_chat(system, user, temperature, max_tokens)
    return ""


# ── Public API ────────────────────────────────────────────────

def chat(system: str, user: str,
         temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """
    Send a chat request through whichever provider ``config.AI_PROVIDER``
    names (default "groq"). Ollama is a fallback for every provider, not an
    alternative to them — see ``_other_provider_chat`` and pass 2 below.
    """
    provider = (getattr(config, "AI_PROVIDER", "") or "groq").strip().lower()
    if provider in _PROVIDERS:
        return _other_provider_chat(provider, system, user, temperature, max_tokens)
    if provider != "groq":
        logger.warning(f"[LLM] Unknown AI_PROVIDER={provider!r} — defaulting to groq")

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
