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
    # MongoDB everywhere (local dev via a free Atlas cluster, or a local
    # mongod) — one MONGODB_URI for every environment. No schema, no
    # migrations: autojob/db_bootstrap.py just ensures indexes exist.
    MONGODB_URI = os.getenv(
        "MONGODB_URI", "mongodb://localhost:27017/autojob_dev"
    )
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "autojob")

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
    # Anchored at BASE_DIR (not left cwd-relative) — a relative
    # STORAGE_LOCAL_ROOT would otherwise land wherever the process happened to
    # be launched from (e.g. repo root vs backend/), scattering files outside
    # backend/ depending on which launcher started the server.
    _storage_root_env = os.getenv("STORAGE_LOCAL_ROOT", "storage")
    STORAGE_LOCAL_ROOT = (
        _storage_root_env
        if Path(_storage_root_env).is_absolute()
        else str(BASE_DIR / _storage_root_env)
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
    MANAGED_OPENAI_KEYS = os.getenv("MANAGED_OPENAI_KEYS", "")
    MANAGED_ANTHROPIC_KEYS = os.getenv("MANAGED_ANTHROPIC_KEYS", "")
    MANAGED_GEMINI_KEYS = os.getenv("MANAGED_GEMINI_KEYS", "")
    MANAGED_GROK_KEYS = os.getenv("MANAGED_GROK_KEYS", "")
    MANAGED_OPENROUTER_KEYS = os.getenv("MANAGED_OPENROUTER_KEYS", "")
    MANAGED_HUNTER_KEYS = os.getenv("MANAGED_HUNTER_KEYS", "")
    MANAGED_PROSPEO_KEYS = os.getenv("MANAGED_PROSPEO_KEYS", "")
    MANAGED_REOON_KEYS = os.getenv("MANAGED_REOON_KEYS", "")
    MANAGED_MILLION_KEYS = os.getenv("MANAGED_MILLION_KEYS", "")

    # ── Security ──────────────────────────────────────────────────
    # The frontend (Vercel) and this API (Render) are deliberately different
    # origins, so the session cookie must be sent cross-site: SameSite=None
    # (browsers additionally require Secure with that, hence ProductionConfig
    # forces SESSION_COOKIE_SECURE). Local dev stays "Lax" since Vite's /api
    # proxy makes everything same-origin there.
    SESSION_COOKIE_HTTPONLY = True
    # `or` (not getenv's default arg) so a present-but-blank .env line falls
    # through to "Lax" too, instead of setting an invalid empty cookie attribute.
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE") or "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = int(os.getenv("SESSION_LIFETIME_SECONDS", str(60 * 60 * 24 * 7)))
    WTF_CSRF_TIME_LIMIT = None  # tie CSRF token lifetime to the session
    # Flask-WTF's CSRFProtect also checks that the Referer header's host
    # matches this server's host on HTTPS requests — a same-origin assumption
    # that breaks a legitimate cross-origin frontend. The X-CSRFToken value
    # itself (validated against the session-bound token) is still checked;
    # this only turns off the extra same-origin check.
    WTF_CSRF_SSL_STRICT = False

    # Comma-separated list of origins allowed to call this API with
    # credentials (e.g. "https://autojob.vercel.app"). Empty means same-origin
    # only — no CORS headers are added, matching local dev via the Vite proxy.
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "")

    # Flask-Limiter storage. In-memory is fine for a single dev process but
    # useless across workers — prod points this at Redis.
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200 per hour")

    # ── Observability (Phase 6) ───────────────────────────────────
    SENTRY_DSN = os.getenv("SENTRY_DSN", "")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_JSON = _bool("LOG_JSON", False)
    # /metrics is unauthenticated by default (fine on an internal network).
    # Set this to require a matching X-Metrics-Token header on a public deploy.
    METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")

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
    # mongomock:// is a signal to Mongo.init_app (extensions.py) to use an
    # in-memory mongomock client instead of a real pymongo connection.
    MONGODB_URI = os.getenv("TEST_MONGODB_URI", "mongomock://localhost/autojob_test")
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SECRET_KEY = "testing-secret-key"
    STORAGE_LOCAL_ROOT = _tempfile.mkdtemp(prefix="autojob-test-storage-")
    CELERY_TASK_ALWAYS_EAGER = True


class ProductionConfig(BaseConfig):
    APP_ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    # Defaults to None (cross-site) rather than BaseConfig's "Lax" — the
    # deployed frontend and this API are different origins. Override to
    # "Lax" via env if the frontend ends up served from the same origin
    # (e.g. behind a shared reverse proxy) instead.
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE") or "None"

    def __init__(self) -> None:
        # Fail fast: production must not run with insecure or missing secrets.
        self.SECRET_KEY = _require("SECRET_KEY")
        self.MONGODB_URI = _require("MONGODB_URI")
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
