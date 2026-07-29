import os
from dotenv import load_dotenv

# Where user-editable settings (email sender, candidate details, toggles) are
# written. Defaults to the repo-local .env for local dev. On Render — where the
# code directory is wiped on every deploy — point this at the mounted disk
# (APP_CONFIG_FILE=/var/data/autojob.env) so settings saved in the dashboard
# survive redeploys.
CONFIG_FILE = os.getenv("APP_CONFIG_FILE", ".env")


def _load_dotenvs(override: bool):
    """Load the repo .env first, then the persistent config file on top."""
    load_dotenv(override=override)
    if CONFIG_FILE and CONFIG_FILE != ".env" and os.path.exists(CONFIG_FILE):
        load_dotenv(CONFIG_FILE, override=True)


def _read_db_settings() -> dict:
    """
    The UI-editable settings from the database — the source of truth for a
    deployed instance where nobody can touch .env. Returns {} on any error
    (table missing, DB down, or a circular import during initial construction)
    so config always has env/defaults to fall back on.
    """
    try:
        from database import get_settings_map

        return get_settings_map()
    except Exception:
        return {}


class Config:
    """
    All configuration, resolved in this precedence for every key:

        1. app_settings row in the database  (edited from the dashboard UI)
        2. environment variable / .env        (deploy-time bootstrap + secrets)
        3. code default

    Call config.reload() after settings change to pick up new values without
    restarting the server.
    """

    def __init__(self):
        self._db_settings: dict = {}
        # Load .env once at startup with override=False, so real OS environment
        # variables (DATABASE_URL, DB_PATH, PORT injected by Render) always win
        # over the .env file. Runtime config changes go through the database.
        _load_dotenvs(override=False)
        self._load()

    def reload(self):
        # Deliberately does NOT re-read .env: settings now live in the database,
        # and re-running load_dotenv(override=True) would let a stale .env value
        # clobber a real environment variable (e.g. flip DB_PATH/DATABASE_URL).
        self._db_settings = _read_db_settings()
        self._load()

    def _get(self, key: str, default: str = "") -> str:
        """DB setting first, then env var, then default."""
        if key in self._db_settings and self._db_settings[key] != "":
            return self._db_settings[key]
        return os.getenv(key, default)

    def _load_groq_keys(self) -> list[str]:
        keys: list[str] = []
        for candidate in [self._get("GROQ_API_KEY")] + [
            self._get(f"GROQ_API_KEY_{i}") for i in range(1, 20)
        ]:
            k = (candidate or "").strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    def _derive_cron(self) -> str:
        """Turn the friendly SCHEDULE_TIME / SCHEDULE_FREQUENCY into a cron."""
        time_str = self._get("SCHEDULE_TIME", "08:00").strip()
        freq = self._get("SCHEDULE_FREQUENCY", "weekdays").strip().lower()
        try:
            hh, mm = (int(x) for x in time_str.split(":", 1))
            hh = max(0, min(23, hh))
            mm = max(0, min(59, mm))
        except Exception:
            hh, mm = 8, 0
        dow = "1-5" if freq == "weekdays" else "*"
        return f"{mm} {hh} * * {dow}"

    def _load(self):
        # ── Groq
        self.GROQ_API_KEYS: list[str] = self._load_groq_keys()
        self.GROQ_MODEL: str = "llama-3.3-70b-versatile"

        # ── Optional integrations
        # Hunter.io — supports a pool of keys just like Groq.
        # Single key:  HUNTER_API_KEY=abc123
        # Multiple:    HUNTER_API_KEY_1=abc  HUNTER_API_KEY_2=def ...
        self.HUNTER_API_KEYS: list[str] = self._load_hunter_keys()

        # Keep single-key attr for any code that still reads it directly
        self.HUNTER_API_KEY: str = self.HUNTER_API_KEYS[0] if self.HUNTER_API_KEYS else ""
        self.PROSPEO_API_KEYS: list[str] = self._load_key_pool("PROSPEO_API_KEY")
        self.PROSPEO_API_KEY: str = self.PROSPEO_API_KEYS[0] if self.PROSPEO_API_KEYS else ""
        self.REOON_API_KEYS: list[str] = self._load_key_pool("REOON_API_KEY")
        self.REOON_API_KEY: str = self.REOON_API_KEYS[0] if self.REOON_API_KEYS else ""
        self.MILLION_VERIFIER_API_KEYS: list[str] = self._load_key_pool("MILLION_VERIFIER_API_KEY")
        self.MILLION_VERIFIER_API_KEY: str = (
            self.MILLION_VERIFIER_API_KEYS[0] if self.MILLION_VERIFIER_API_KEYS else ""
        )
        self.CONTACT_SEARCH_ENABLED: bool = self._get("CONTACT_SEARCH_ENABLED", "true").lower() == "true"
        self.CONTACT_SEARCH_ENGINE: str = self._get("CONTACT_SEARCH_ENGINE", "duckduckgo,bing,google")
        self.CONTACT_SEARCH_MAX_RESULTS: int = int(self._get("CONTACT_SEARCH_MAX_RESULTS", "8"))
        self.CONTACT_AI_FALLBACK: bool = self._get("CONTACT_AI_FALLBACK", "true").lower() == "true"

        # ── GitHub Integration
        # Personal access token (classic or fine-grained, read:user + repo scope).
        # When set, AutoJob fetches your real repo URLs and descriptions to
        # inject accurate, concrete details into every CV it generates.
        # Leave blank to skip GitHub enrichment (URLs fall back to what's in your CV PDF).
        self.GITHUB_TOKEN: str = self._get("GITHUB_TOKEN", "").strip()

        # Optional explicit project → URL overrides (JSON object in env).
        # Format: CANDIDATE_PROJECT_URLS={"WordWar":"https://github.com/you/wordwar","VaultDrop":"https://vaultdrop.io"}
        # When present, these URLs win over any auto-fetched GitHub URL for that project name.
        # Useful for projects with live demo URLs or repos not on GitHub.
        raw_urls = self._get("CANDIDATE_PROJECT_URLS", "").strip()
        self.CANDIDATE_PROJECT_URLS: dict[str, str] = {}
        if raw_urls:
            try:
                import json as _json
                self.CANDIDATE_PROJECT_URLS = _json.loads(raw_urls)
            except Exception:
                pass  # silently ignore malformed JSON

        # ── Job targeting
        # What to search for, which seniority, and which skills matter are all
        # derived from the CV — see core/discovery.py and core/cv_profile.py.
        # Only the blacklist stays manual: it encodes a preference the CV
        # cannot express ("never show me unpaid work").
        self.BLACKLIST_KEYWORDS: list[str] = [
            k.strip().lower()
            for k in self._get("BLACKLIST_KEYWORDS", "internship,unpaid").split(",")
            if k.strip()
        ]

        # ── Salary
        # A floor only. Postings below it are filtered out; postings that
        # state no salary at all are kept, since most don't list one.
        self.MIN_SALARY: int = int(self._get("MIN_SALARY", 0))

        # ── Location
        self.REMOTE_ONLY: bool = self._get("REMOTE_ONLY", "true").lower() == "true"
        self.TARGET_COUNTRIES: list[str] = [
            c.strip()
            for c in self._get("TARGET_COUNTRIES", "Remote").split(",")
            if c.strip()
        ]

        # ── Candidate
        self.CANDIDATE_NAME: str     = self._get("CANDIDATE_NAME", "Candidate")
        self.CANDIDATE_EMAIL: str    = self._get("CANDIDATE_EMAIL", "")
        self.CANDIDATE_PHONE: str    = self._get("CANDIDATE_PHONE", "")
        self.CANDIDATE_LOCATION: str = self._get("CANDIDATE_LOCATION", "")
        self.CANDIDATE_LINKEDIN: str = self._get("CANDIDATE_LINKEDIN", "")
        self.CANDIDATE_GITHUB: str   = self._get("CANDIDATE_GITHUB", "")

        # ── Scrapers
        self.SCRAPE_LINKEDIN: bool       = self._get("SCRAPE_LINKEDIN", "true").lower() == "true"
        self.SCRAPE_INDEED: bool         = self._get("SCRAPE_INDEED", "true").lower() == "true"
        self.SCRAPE_REMOTEOK: bool       = self._get("SCRAPE_REMOTEOK", "true").lower() == "true"
        self.SCRAPE_WEWORKREMOTELY: bool = self._get("SCRAPE_WEWORKREMOTELY", "true").lower() == "true"
        self.SCRAPE_GOOGLE: bool         = self._get("SCRAPE_GOOGLE", "false").lower() == "true"
        self.SCRAPE_JOBICY: bool         = self._get("SCRAPE_JOBICY", "true").lower() == "true"
        self.SCRAPE_REMOTIVE: bool       = self._get("SCRAPE_REMOTIVE", "true").lower() == "true"
        self.SCRAPE_ARBEITNOW: bool      = self._get("SCRAPE_ARBEITNOW", "true").lower() == "true"
        self.SCRAPE_HACKERNEWS: bool     = self._get("SCRAPE_HACKERNEWS", "true").lower() == "true"
        self.MAX_JOBS_PER_BOARD: int     = int(self._get("MAX_JOBS_PER_BOARD", 50))
        # ── Scoring & docs
        self.MIN_MATCH_SCORE: int           = int(self._get("MIN_MATCH_SCORE", 60))
        self.LLM_SCORING: bool              = self._get("LLM_SCORING", "false").lower() == "true"
        # Documents are assembled offline from the parsed CV. Only jobs
        # scoring at or above this get an LLM polish pass (1 call, not 3).
        # Set to 101 to disable polishing and run with zero API calls.
        self.LLM_POLISH_MIN_SCORE: int      = int(self._get("LLM_POLISH_MIN_SCORE", 85))
        self.GENERATE_DOCS_WITHOUT_HR: bool = self._get("GENERATE_DOCS_WITHOUT_HR", "true").lower() == "true"

        # ── How many jobs to apply to per run (top matches by score).
        # 0 = no cap. This is the main "how many should it apply to" control,
        # set from the dashboard.
        self.MAX_APPLICATIONS_PER_RUN: int = int(self._get("MAX_APPLICATIONS_PER_RUN", 10))

        # ── Paths
        self.OUTPUT_DIR: str = self._get("OUTPUT_DIR", "output")
        self.INPUT_DIR: str  = self._get("INPUT_DIR", "input")
        self.DB_PATH: str    = self._get("DB_PATH", "jobhunter.db")

        # ── Flask
        # PORT is what Render (and most PaaS) inject; fall back to FLASK_PORT
        # for local dev, then 5000.
        self.FLASK_PORT: int   = int(self._get("PORT", self._get("FLASK_PORT", "5000")))
        # Public URL of the deployment, used for social share cards. Leave blank
        # to auto-detect from the incoming request.
        self.APP_BASE_URL: str = self._get("APP_BASE_URL", "").rstrip("/")
        self.FLASK_DEBUG: bool = self._get("FLASK_DEBUG", "false").lower() == "true"
        self.TIMEZONE: str     = self._get("TIMEZONE", "UTC")

        # ── Proxy / SOCKS
        self.PROXY_ENABLED: bool = self._get("PROXY_ENABLED", "false").lower() == "true"
        raw_proxies = self._get("PROXY_LIST", "")
        self.PROXY_LIST: list[str] = []
        if raw_proxies:
            for p in raw_proxies.split(","):
                p = p.strip()
                if not p:
                    continue
                if "://" not in p:
                    p = f"socks5://{p}"
                self.PROXY_LIST.append(p)

        # ── Email API sending
        self.SMTP_HOST: str      = self._get("SMTP_HOST", "")
        self.SMTP_PORT: int      = int(self._get("SMTP_PORT", "587"))
        self.SMTP_USER: str      = self._get("SMTP_USER", "")
        self.SMTP_PASSWORD: str  = self._get("SMTP_PASSWORD", "")
        self.SMTP_FROM: str      = self._get("SMTP_FROM", "")
        self.SMTP_TLS: bool      = self._get("SMTP_TLS", "true").lower() == "true"
        # Explicit SSL choice. "" = decide by port (465 = SSL). "true"/"false"
        # let the user force it from the dashboard regardless of port.
        self.SMTP_SSL: str       = self._get("SMTP_SSL", "").lower()

        # ── Gmail (app password) — the simplest option for most people.
        # Turn on 2-Step Verification, then create an App Password at
        # https://myaccount.google.com/apppasswords and paste the 16-char code.
        self.GMAIL_ADDRESS: str      = self._get("GMAIL_ADDRESS", "").strip()
        self.GMAIL_APP_PASSWORD: str = self._get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
        # Which sender the onboarding flow selected: "gmail" | "smtp" | "" (none/API only)
        self.EMAIL_MODE: str = self._get("EMAIL_MODE", "").strip().lower()
        self.SMTP_AUTO_SEND: bool   = self._get("SMTP_AUTO_SEND", "false").lower() == "true"
        self.SMTP_ATTACH_PDF: bool  = self._get("SMTP_ATTACH_PDF", "true").lower() == "true"
        self.SMTP_ATTACH_DOCX: bool = self._get("SMTP_ATTACH_DOCX", "false").lower() == "true"
        self.SMTP_RETRY_COUNT: int  = int(self._get("SMTP_RETRY_COUNT", "1"))
        self.SMTP_FORMAT: str       = self._get("SMTP_FORMAT", "plain").lower()
        self.SMTP_THROTTLE_SECONDS: int = int(self._get("SMTP_THROTTLE_SECONDS", "8"))
        self.SMTP_REPLY_TO: str     = self._get("SMTP_REPLY_TO", self.CANDIDATE_EMAIL or self.SMTP_FROM)
        self.EMAIL_DAILY_LIMIT: int = int(self._get("EMAIL_DAILY_LIMIT", "100"))
        self.BREVO_API_KEY: str     = self._get("BREVO_API_KEY", "")
        self.BREVO_FROM: str        = self._get("BREVO_FROM", self.SMTP_FROM or self.CANDIDATE_EMAIL)
        self.BREVO_FROM_NAME: str   = self._get("BREVO_FROM_NAME", self.CANDIDATE_NAME)
        self.SMTP2GO_API_KEY: str   = self._get("SMTP2GO_API_KEY", "")
        self.SMTP2GO_FROM: str      = self._get("SMTP2GO_FROM", self.SMTP_FROM or self.CANDIDATE_EMAIL)
        self.SMTP2GO_FROM_NAME: str = self._get("SMTP2GO_FROM_NAME", self.CANDIDATE_NAME)
        self.EMAIL_PROVIDERS: list[dict] = self._load_email_providers()
        self.SMTP_PROVIDERS: list[dict] = self.EMAIL_PROVIDERS

        # ── Deduplication
        # Days to remember a sent email address before allowing re-send
        self.DEDUP_WINDOW_DAYS: int = int(self._get("DEDUP_WINDOW_DAYS", "30"))

        # ── Follow-up
        self.FOLLOW_UP_ENABLED: bool = self._get("FOLLOW_UP_ENABLED", "true").lower() == "true"
        # Days after initial send before follow-up fires (default 6 = ~1 working week)
        self.FOLLOW_UP_DAYS: int = int(self._get("FOLLOW_UP_DAYS", "6"))

        # ── IMAP (for reply detection)
        # Optional IMAP reply detection. Leave blank to disable.
        self.IMAP_HOST: str     = self._get("IMAP_HOST", "")
        self.IMAP_PORT: int     = int(self._get("IMAP_PORT", "993"))
        self.IMAP_USER: str     = self._get("IMAP_USER", self.SMTP_USER)
        self.IMAP_PASSWORD: str = self._get("IMAP_PASSWORD", self.SMTP_PASSWORD)

        # ── Ollama fallback (used when all Groq keys are exhausted)
        # Models are tried in the order listed — first available wins.
        # Recommended order for this workload (you have all of these):
        #   qwen2.5-coder:32b  — best for doc generation (slow but high quality)
        #   gemma3:12b         — good for scoring + research
        #   qwen3:6b           — fast, reliable JSON extraction
        #   mistral            — quick fallback for any task
        self.OLLAMA_ENABLED: bool  = self._get("OLLAMA_ENABLED", "false").lower() == "true"
        self.OLLAMA_BASE_URL: str  = self._get("OLLAMA_BASE_URL", "http://localhost:11434")
        self.OLLAMA_MODELS: str    = self._get(
            "OLLAMA_MODELS",
            "qwen2.5-coder:32b,gemma3:12b,mistral:latest"
        )

        # ── Scheduler (auto-run pipeline automatically)
        # The dashboard sets a friendly time + frequency; a raw cron is derived
        # from them. SCHEDULE_TIME is "HH:MM" (24h). SCHEDULE_FREQUENCY is
        # "daily" or "weekdays". A raw SCHEDULE_CRON still wins if set directly.
        self.SCHEDULE_ENABLED: bool  = self._get("SCHEDULE_ENABLED", "false").lower() == "true"
        self.SCHEDULE_TIME: str      = self._get("SCHEDULE_TIME", "08:00")
        self.SCHEDULE_FREQUENCY: str = self._get("SCHEDULE_FREQUENCY", "weekdays").lower()
        # The friendly time/frequency fields are the UI interface and win once
        # the user has set either of them. A raw SCHEDULE_CRON is only an
        # advanced escape hatch used when the friendly fields aren't in the DB.
        friendly_in_db = (
            "SCHEDULE_TIME" in self._db_settings
            or "SCHEDULE_FREQUENCY" in self._db_settings
        )
        raw_cron = self._get("SCHEDULE_CRON", "")
        self.SCHEDULE_CRON: str = (
            raw_cron if (raw_cron and not friendly_in_db) else self._derive_cron()
        )
        self.SCHEDULE_FOLLOWUP: bool = self._get("SCHEDULE_FOLLOWUP", "true").lower() == "true"

        # ── Notifications (Telegram)
        # Create a bot at t.me/BotFather, get your chat_id via t.me/userinfobot
        self.TELEGRAM_ENABLED: bool  = self._get("TELEGRAM_ENABLED", "false").lower() == "true"
        self.TELEGRAM_BOT_TOKEN: str = self._get("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID: str   = self._get("TELEGRAM_CHAT_ID", "")

        # ── Portal auto-fill (Playwright)
        self.PORTAL_ENABLED: bool    = self._get("PORTAL_ENABLED", "false").lower() == "true"
        self.PORTAL_HEADLESS: bool   = self._get("PORTAL_HEADLESS", "true").lower() == "true"
        self.PORTAL_SUBMIT: bool     = self._get("PORTAL_SUBMIT", "true").lower() == "true"
        self.PORTAL_TIMEOUT_MS: int  = int(self._get("PORTAL_TIMEOUT_MS", "30000"))

        # ── CV Version Management
        # Leave blank = auto-pick first file found in input/ (original behaviour)
        # Set to a filename to pin a specific CV (e.g. CV_web3.docx)
        self.ACTIVE_CV: str = self._get("ACTIVE_CV", "")


    def _load_hunter_keys(self) -> list[str]:
        return self._load_key_pool("HUNTER_API_KEY")

    def _load_key_pool(self, base_name: str) -> list[str]:
        keys: list[str] = []
        k = self._get(base_name, "").strip()
        if k:
            keys.append(k)
        for i in range(1, 20):
            k = self._get(f"{base_name}_{i}", "").strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    def _load_email_providers(self) -> list[dict]:
        """
        Build the ordered list of email senders.

        Real SMTP (Gmail app password or a generic host) comes first because
        it needs no third-party account — it sends straight from the user's own
        mailbox, so replies land where they expect. Brevo/SMTP2GO stay as
        optional API fallbacks for anyone who has them.
        """
        providers: list[dict] = []

        # ── Gmail (app password) ──────────────────────────────
        if self.GMAIL_ADDRESS and self.GMAIL_APP_PASSWORD:
            providers.append({
                "name": "smtp",
                "label": "Gmail",
                "host": "smtp.gmail.com",
                "port": 587,
                "user": self.GMAIL_ADDRESS,
                "password": self.GMAIL_APP_PASSWORD,
                "from": self.GMAIL_ADDRESS,
                "from_name": self.CANDIDATE_NAME,
                "tls": True,
                "ssl": False,
            })

        # ── Generic SMTP (any provider / company mailbox) ─────
        elif self.SMTP_HOST and self.SMTP_USER and self.SMTP_PASSWORD:
            # Explicit SSL wins; otherwise fall back to the port convention.
            if self.SMTP_SSL in ("true", "false"):
                use_ssl = self.SMTP_SSL == "true"
            else:
                use_ssl = self.SMTP_PORT == 465
            providers.append({
                "name": "smtp",
                "label": "SMTP",
                "host": self.SMTP_HOST,
                "port": self.SMTP_PORT,
                "user": self.SMTP_USER,
                "password": self.SMTP_PASSWORD,
                "from": self.SMTP_FROM or self.SMTP_USER,
                "from_name": self.CANDIDATE_NAME,
                "tls": self.SMTP_TLS and not use_ssl,
                "ssl": use_ssl,
            })

        def add(name: str, api_key: str, from_addr: str, from_name: str):
            if api_key and from_addr:
                providers.append({
                    "name": name,
                    "label": name.title(),
                    "api_key": api_key,
                    "from": from_addr,
                    "from_name": from_name or self.CANDIDATE_NAME,
                })

        add("brevo", self.BREVO_API_KEY, self.BREVO_FROM, self.BREVO_FROM_NAME)
        add("smtp2go", self.SMTP2GO_API_KEY, self.SMTP2GO_FROM, self.SMTP2GO_FROM_NAME)
        return providers


config = Config()
