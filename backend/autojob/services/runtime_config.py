"""
Per-user runtime configuration.

The legacy engine reads one global ``config`` singleton sourced from a single
``.env``. In a multi-tenant world each run must instead use *that user's*
settings and *that user's* credentials. ``build_runtime_config`` assembles a
plain object from ``UserSettings`` + decrypted ``UserCredential`` rows; Phase 5
threads it into the pipeline so scraping, scoring, and sending all run with the
right tenant's configuration and secrets.

Managed vs. bring-your-own: if a user hasn't supplied a credential (e.g. a Groq
key), we fall back to a platform-managed pool from the app config — this is what
lets a user "just upload a CV" without configuring any API keys themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flask import current_app

from . import repository as repo


@dataclass
class RuntimeConfig:
    user_id: str

    # Targeting
    blacklist_keywords: list[str] = field(default_factory=list)
    min_match_score: int = 60
    min_salary: int = 0
    remote_only: bool = True
    target_countries: list[str] = field(default_factory=lambda: ["Remote"])

    # Sending behaviour
    auto_send: bool = False
    generate_docs_without_hr: bool = True
    follow_up_enabled: bool = True
    follow_up_days: int = 6
    dedup_window_days: int = 30
    email_daily_limit: int = 100
    has_sending_consent: bool = False

    # Scheduling
    schedule_enabled: bool = False
    schedule_cron: str = "0 8 * * 1-5"

    # Which provider tailors/scores for this user, and its key(s) (decrypted,
    # in memory only). One of: groq, openai, anthropic, gemini, openrouter.
    ai_provider: str = "groq"
    ai_keys: list[str] = field(default_factory=list)

    # Credentials (decrypted, in memory only)
    hunter_keys: list[str] = field(default_factory=list)
    prospeo_keys: list[str] = field(default_factory=list)
    reoon_keys: list[str] = field(default_factory=list)
    million_keys: list[str] = field(default_factory=list)
    smtp: dict = field(default_factory=dict)
    imap: dict = field(default_factory=dict)

    # Whether the platform is supplying managed keys the user didn't set.
    managed_llm: bool = False
    managed_enrichment: bool = False


def _split(csv: str) -> list[str]:
    return [x.strip() for x in (csv or "").split(",") if x.strip()]


def _managed_pool(config_key: str) -> list[str]:
    raw = current_app.config.get(config_key, "")
    return _split(raw) if isinstance(raw, str) else list(raw or [])


def build_runtime_config(user_id: str) -> RuntimeConfig:
    s = repo.get_or_create_settings(user_id)

    # Master switch (Settings → Credentials): "use my own AI/enrichment keys".
    # Off means always use the platform-managed pool for these providers, even
    # if the user has a key saved — e.g. to test with the platform default
    # without deleting a key they might want back later.
    use_own_keys = s.use_own_api_keys

    ai_provider = (s.ai_provider or "groq").strip().lower()
    ai_keys = _split(repo.get_credential(user_id, ai_provider)) if use_own_keys else []
    managed_llm = False
    if not ai_keys:
        ai_keys = _managed_pool(f"MANAGED_{ai_provider.upper()}_KEYS")
        managed_llm = bool(ai_keys)

    # Enrichment providers: user's own key first, else the managed pool. This
    # is what lets a user find recruiter contacts without signing up for
    # Hunter/Prospeo/Reoon themselves.
    def _enrichment(provider: str, managed_key: str) -> tuple[list[str], bool]:
        keys = _split(repo.get_credential(user_id, provider)) if use_own_keys else []
        if keys:
            return keys, False
        managed = _managed_pool(managed_key)
        return managed, bool(managed)

    hunter, m1 = _enrichment("hunter", "MANAGED_HUNTER_KEYS")
    prospeo, m2 = _enrichment("prospeo", "MANAGED_PROSPEO_KEYS")
    reoon, m3 = _enrichment("reoon", "MANAGED_REOON_KEYS")
    million, m4 = _enrichment("million", "MANAGED_MILLION_KEYS")
    managed_enrichment = any((m1, m2, m3, m4))

    smtp = {}
    smtp_secret = repo.get_credential(user_id, "smtp")
    if smtp_secret:
        # meta holds host/port/from; secret holds the password
        row_meta = _credential_meta(user_id, "smtp")
        smtp = {**row_meta, "password": smtp_secret}

    imap = {}
    imap_secret = repo.get_credential(user_id, "imap")
    if imap_secret:
        imap = {**_credential_meta(user_id, "imap"), "password": imap_secret}

    return RuntimeConfig(
        user_id=user_id,
        blacklist_keywords=_split(s.blacklist_keywords),
        min_match_score=s.min_match_score,
        min_salary=s.min_salary,
        remote_only=s.remote_only,
        target_countries=_split(s.target_countries) or ["Remote"],
        auto_send=s.auto_send,
        generate_docs_without_hr=s.generate_docs_without_hr,
        follow_up_enabled=s.follow_up_enabled,
        follow_up_days=s.follow_up_days,
        dedup_window_days=s.dedup_window_days,
        email_daily_limit=s.email_daily_limit,
        has_sending_consent=s.sending_consent_at is not None,
        schedule_enabled=s.schedule_enabled,
        schedule_cron=s.schedule_cron,
        ai_provider=ai_provider,
        ai_keys=ai_keys,
        hunter_keys=hunter,
        prospeo_keys=prospeo,
        reoon_keys=reoon,
        million_keys=million,
        smtp=smtp,
        imap=imap,
        managed_llm=managed_llm,
        managed_enrichment=managed_enrichment,
    )


def _credential_meta(user_id: str, provider: str) -> dict:
    return repo.get_credential_meta(user_id, provider)


def preflight_issues(cfg: RuntimeConfig) -> list[str]:
    """
    Hard blockers checked before a run starts — problems that would make the
    run pointless (no AI key anywhere) or silently incomplete (auto-send is on
    but there's nowhere to send from), so the user gets a clear message up
    front instead of a run that quietly does less than they expect.
    """
    issues: list[str] = []

    if not cfg.ai_keys:
        issues.append(
            f"No AI key available for tailoring — add your own {cfg.ai_provider} "
            "key in Settings → Credentials, or ask the operator to configure a "
            "platform default."
        )

    if cfg.auto_send and cfg.has_sending_consent:
        from . import mailer

        if not mailer.smtp_ready(cfg):
            issues.append(
                "Auto-send is on but your SMTP sender isn't configured — add "
                "it in Settings → Credentials, or turn off auto-send in "
                "Settings → General."
            )

    return issues
