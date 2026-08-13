"""engine_adapter shims a tenant's AI provider + key(s) into the legacy config."""

from __future__ import annotations

from autojob.services.engine_adapter import use_runtime
from autojob.services.runtime_config import RuntimeConfig


def test_use_runtime_shims_ai_provider_and_clears_others(app_context):
    from config import config as legacy_config

    legacy_config.AI_PROVIDER = "groq"
    legacy_config.GROQ_API_KEYS = ["stale-groq-key"]
    legacy_config.OPENAI_API_KEYS = []

    cfg = RuntimeConfig(user_id="u1", ai_provider="openai", ai_keys=["user-openai-key"])
    with use_runtime(cfg, profile={}):
        assert legacy_config.AI_PROVIDER == "openai"
        assert legacy_config.OPENAI_API_KEYS == ["user-openai-key"]
        # The previously-configured Groq keys must not leak into this run.
        assert legacy_config.GROQ_API_KEYS == []

    # Restored to whatever was there before the run.
    assert legacy_config.AI_PROVIDER == "groq"
    assert legacy_config.GROQ_API_KEYS == ["stale-groq-key"]
