"""
Adapter that lets the legacy engine run for one tenant.

The engine modules (``core.contact_extractor``, ``core.document_generator``,
``core.scorer``) read the legacy global ``config`` singleton and the legacy
``database`` module. Rather than rewrite thousands of lines, this context
manager temporarily points those globals at the current user's values for the
duration of one pipeline step, then restores them.

Two things happen inside ``use_runtime``:

1. The user's credentials and preferences (Hunter keys, salary/location filters,
   candidate identity from the parsed CV) are written onto the legacy config.
2. The scorer's IDF corpus functions are redirected to the SaaS database via the
   tenant repository, so no corpus data ever lands in the legacy ``jobhunter.db``
   (fix for the cross-database corpus leak).

A process-wide lock serialises this: mutating a shared global is only safe one
run at a time. Celery prefork already runs one task per worker process; the lock
additionally protects the dev eager path where requests share a process.
"""

from __future__ import annotations

import contextlib
import threading

from . import repository as repo
from .runtime_config import RuntimeConfig

_engine_lock = threading.RLock()

# Config attributes we override per run, so we can restore exactly these.
_MANAGED_ATTRS = (
    "HUNTER_API_KEYS", "PROSPEO_API_KEYS", "REOON_API_KEYS",
    "MILLION_VERIFIER_API_KEYS", "CONTACT_SEARCH_ENABLED",
    "MIN_SALARY", "REMOTE_ONLY", "TARGET_COUNTRIES",
    "GENERATE_DOCS_WITHOUT_HR", "BLACKLIST_KEYWORDS",
    "CANDIDATE_NAME", "CANDIDATE_EMAIL", "CANDIDATE_PHONE",
    "CANDIDATE_LOCATION", "CANDIDATE_LINKEDIN", "CANDIDATE_GITHUB",
)


def _candidate_from_profile(profile: dict) -> dict:
    cc = profile.get("contact_choices", {})

    def pick(field):
        return (cc.get(field, {}) or {}).get("value", "") or profile.get(field, "")

    return {
        "CANDIDATE_NAME": profile.get("name", ""),
        "CANDIDATE_EMAIL": pick("email"),
        "CANDIDATE_PHONE": pick("phone"),
        "CANDIDATE_LOCATION": pick("location"),
        "CANDIDATE_LINKEDIN": pick("linkedin"),
        "CANDIDATE_GITHUB": pick("github"),
    }


@contextlib.contextmanager
def use_runtime(cfg: RuntimeConfig, profile: dict):
    """Run the enclosed engine calls as this tenant."""
    import core.scorer as scorer_mod  # noqa: F401  (ensures module import)
    from config import config as legacy_config

    with _engine_lock:
        saved = {a: getattr(legacy_config, a, None) for a in _MANAGED_ATTRS}
        saved_df = _swap_corpus()
        try:
            legacy_config.HUNTER_API_KEYS = list(cfg.hunter_keys)
            legacy_config.PROSPEO_API_KEYS = []
            legacy_config.REOON_API_KEYS = []
            legacy_config.MILLION_VERIFIER_API_KEYS = []
            legacy_config.CONTACT_SEARCH_ENABLED = True
            legacy_config.MIN_SALARY = cfg.min_salary
            legacy_config.REMOTE_ONLY = cfg.remote_only
            legacy_config.TARGET_COUNTRIES = list(cfg.target_countries)
            legacy_config.GENERATE_DOCS_WITHOUT_HR = cfg.generate_docs_without_hr
            legacy_config.BLACKLIST_KEYWORDS = list(cfg.blacklist_keywords)
            for k, v in _candidate_from_profile(profile).items():
                setattr(legacy_config, k, v)
            yield
        finally:
            for a, v in saved.items():
                with contextlib.suppress(Exception):
                    setattr(legacy_config, a, v)
            _restore_corpus(saved_df)


def _swap_corpus():
    """Redirect the scorer's IDF corpus to the SaaS DB; return originals."""
    import database as legacy_db

    original = (legacy_db.load_token_df, legacy_db.bump_token_df)
    legacy_db.load_token_df = repo.load_token_df
    legacy_db.bump_token_df = repo.bump_token_df
    return original


def _restore_corpus(original) -> None:
    import database as legacy_db

    legacy_db.load_token_df, legacy_db.bump_token_df = original
