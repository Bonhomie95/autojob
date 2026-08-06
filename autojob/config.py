"""
Environment-based configuration for the multi-tenant SaaS.

Unlike the legacy top-level ``config.py`` (a single global singleton that reads
one user's ``.env``), this module defines *application* configuration only —
things that are the same for every tenant: the database URL, the secret key,
the Redis broker, security toggles. Per-user settings (their CV, their API
keys, their sending identity) live in the database and are loaded per request,
not from environment variables. See Phase 4.

Selection is by the ``APP_ENV`` environment variable: ``development`` (default),
``testing``, or ``production``.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            f"Refusing to start with an insecure default."
        )
    return val


class BaseConfig:
    """Settings shared by every environment."""

    # ── Core Flask ────────────────────────────────────────────────
    # SECRET_KEY MUST be stable across restarts (sessions, CSRF tokens depend
    # on it) and MUST be secret. Never fall back to os.urandom() like the
    # legacy app did — that silently invalidated every session on restart.
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")

    # ── Database ──────────────────────────────────────────────────
    # SQLite for local dev, Postgres in prod. Both via a single DATABASE_URL.
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'autojob_saas.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,  # drop dead connections instead of erroring
        "pool_recycle": 1800,
    }

    # ── Background jobs ───────────────────────────────────────────
    # AutoJob runs as a single web service by default: discovery runs execute
    # in a background thread in-process, and progress streams over an in-process
    # bus (see services/progress.py) — no Redis, no separate worker required.
    # Set RUN_VIA_CELERY=true (with a reachable broker + a running worker) to
    # enqueue runs onto Celery instead, for horizontal scaling.
    RUN_VIA_CELERY = _bool("RUN_VIA_CELERY", False)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
    # Default to running any dispatched Celery task inline, so nothing depends on
    # a broker being present unless the operator explicitly opts into Celery.
    CELERY_TASK_ALWAYS_EAGER = _bool("CELERY_TASK_ALWAYS_EAGER", True)

    # ── Credential encryption (Phase 4) ───────────────────────────
    # Fernet key used to encrypt per-user secrets (their SMTP password, API
    # keys) at rest in the database. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    CREDENTIAL_ENCRYPTION_KEY = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")

    # ── Object storage for uploaded CVs (Phase 4) ─────────────────
    # 'local' keeps files on disk (dev); 's3' targets any S3-compatible store.
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
    STORAGE_LOCAL_ROOT = os.getenv(
        "STORAGE_LOCAL_ROOT", str(BASE_DIR / "storage")
    )
    S3_BUCKET = os.getenv("S3_BUCKET", "")
    S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
    S3_REGION = os.getenv("S3_REGION", "")

    # ── Uploads ───────────────────────────────────────────────────
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    ALLOWED_CV_EXTENSIONS = {".pdf", ".docx", ".txt"}

    # ── Managed credential pools ──────────────────────────────────
    # Server-side keys used when a user hasn't supplied their own, so they can
    # "just upload a CV". Comma-separated. Leave empty to require BYO keys.
    MANAGED_GROQ_KEYS = os.getenv("MANAGED_GROQ_KEYS", "")
    MANAGED_HUNTER_KEYS = os.getenv("MANAGED_HUNTER_KEYS", "")
    MANAGED_PROSPEO_KEYS = os.getenv("MANAGED_PROSPEO_KEYS", "")
    MANAGED_REOON_KEYS = os.getenv("MANAGED_REOON_KEYS", "")
    MANAGED_MILLION_KEYS = os.getenv("MANAGED_MILLION_KEYS", "")

    # ── Security ──────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = int(os.getenv("SESSION_LIFETIME_SECONDS", str(60 * 60 * 24 * 7)))
    WTF_CSRF_TIME_LIMIT = None  # tie CSRF token lifetime to the session

    # Flask-Limiter storage. In-memory is fine for a single dev process but
    # useless across workers — prod points this at Redis.
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200 per hour")

    # ── Observability (Phase 6) ───────────────────────────────────
    SENTRY_DSN = os.getenv("SENTRY_DSN", "")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_JSON = _bool("LOG_JSON", False)

    # ── Misc ──────────────────────────────────────────────────────
    APP_ENV = "base"
    TESTING = False
    DEBUG = False


class DevelopmentConfig(BaseConfig):
    APP_ENV = "development"
    DEBUG = True
    # Run tasks inline by default so the app is fully functional without a
    # Redis broker + worker. Set CELERY_TASK_ALWAYS_EAGER=false and run a real
    # worker to exercise the production path locally.
    CELERY_TASK_ALWAYS_EAGER = _bool("CELERY_TASK_ALWAYS_EAGER", True)


class TestingConfig(BaseConfig):
    import tempfile as _tempfile

    APP_ENV = "testing"
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SECRET_KEY = "testing-secret-key"
    STORAGE_LOCAL_ROOT = _tempfile.mkdtemp(prefix="autojob-test-storage-")
    CELERY_TASK_ALWAYS_EAGER = True


class ProductionConfig(BaseConfig):
    APP_ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True

    def __init__(self) -> None:
        # Fail fast: production must not run with insecure or missing secrets.
        self.SECRET_KEY = _require("SECRET_KEY")
        self.SQLALCHEMY_DATABASE_URI = _require("DATABASE_URL")
        self.CREDENTIAL_ENCRYPTION_KEY = _require("CREDENTIAL_ENCRYPTION_KEY")


_CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    """Return the config object for ``name`` (or ``$APP_ENV``, default dev)."""
    env = (name or os.getenv("APP_ENV", "development")).strip().lower()
    cfg = _CONFIG_MAP.get(env, DevelopmentConfig)
    # ProductionConfig validates in __init__; others are used as classes.
    return cfg() if isinstance(cfg, type) and env == "production" else cfg
