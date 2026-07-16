import os
from dotenv import load_dotenv


def _load_groq_keys() -> list[str]:
    keys: set[str] = set()
    s = os.getenv("GROQ_API_KEY", "").strip()
    if s:
        keys.add(s)
    for i in range(1, 20):
        k = os.getenv(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            keys.add(k)
    return list(keys)


class Config:
    """
    All configuration. Call config.reload() after .env changes
    to pick up new values without restarting the server.
    """

    def __init__(self):
        load_dotenv()
        self._load()

    def reload(self):
        load_dotenv(override=True)
        self._load()

    def _load(self):
        # ── Groq
        self.GROQ_API_KEYS: list[str] = _load_groq_keys()
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
        self.CONTACT_SEARCH_ENABLED: bool = os.getenv("CONTACT_SEARCH_ENABLED", "true").lower() == "true"
        self.CONTACT_SEARCH_ENGINE: str = os.getenv("CONTACT_SEARCH_ENGINE", "duckduckgo,bing,google")
        self.CONTACT_SEARCH_MAX_RESULTS: int = int(os.getenv("CONTACT_SEARCH_MAX_RESULTS", "8"))
        self.CONTACT_AI_FALLBACK: bool = os.getenv("CONTACT_AI_FALLBACK", "true").lower() == "true"

        # ── GitHub Integration
        # Personal access token (classic or fine-grained, read:user + repo scope).
        # When set, AutoJob fetches your real repo URLs and descriptions to
        # inject accurate, concrete details into every CV it generates.
        # Leave blank to skip GitHub enrichment (URLs fall back to what's in your CV PDF).
        self.GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "").strip()

        # Optional explicit project → URL overrides (JSON object in env).
        # Format: CANDIDATE_PROJECT_URLS={"WordWar":"https://github.com/you/wordwar","VaultDrop":"https://vaultdrop.io"}
        # When present, these URLs win over any auto-fetched GitHub URL for that project name.
        # Useful for projects with live demo URLs or repos not on GitHub.
        raw_urls = os.getenv("CANDIDATE_PROJECT_URLS", "").strip()
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
            for k in os.getenv("BLACKLIST_KEYWORDS", "internship,unpaid").split(",")
            if k.strip()
        ]

        # ── Salary
        # A floor only. Postings below it are filtered out; postings that
        # state no salary at all are kept, since most don't list one.
        self.MIN_SALARY: int = int(os.getenv("MIN_SALARY", 0))

        # ── Location
        self.REMOTE_ONLY: bool = os.getenv("REMOTE_ONLY", "true").lower() == "true"
        self.TARGET_COUNTRIES: list[str] = [
            c.strip()
            for c in os.getenv("TARGET_COUNTRIES", "Remote").split(",")
            if c.strip()
        ]

        # ── Candidate
        self.CANDIDATE_NAME: str     = os.getenv("CANDIDATE_NAME", "Candidate")
        self.CANDIDATE_EMAIL: str    = os.getenv("CANDIDATE_EMAIL", "")
        self.CANDIDATE_PHONE: str    = os.getenv("CANDIDATE_PHONE", "")
        self.CANDIDATE_LOCATION: str = os.getenv("CANDIDATE_LOCATION", "")
        self.CANDIDATE_LINKEDIN: str = os.getenv("CANDIDATE_LINKEDIN", "")
        self.CANDIDATE_GITHUB: str   = os.getenv("CANDIDATE_GITHUB", "")

        # ── Scrapers
        self.SCRAPE_LINKEDIN: bool       = os.getenv("SCRAPE_LINKEDIN", "true").lower() == "true"
        self.SCRAPE_INDEED: bool         = os.getenv("SCRAPE_INDEED", "true").lower() == "true"
        self.SCRAPE_REMOTEOK: bool       = os.getenv("SCRAPE_REMOTEOK", "true").lower() == "true"
        self.SCRAPE_WEWORKREMOTELY: bool = os.getenv("SCRAPE_WEWORKREMOTELY", "true").lower() == "true"
        self.SCRAPE_GOOGLE: bool         = os.getenv("SCRAPE_GOOGLE", "false").lower() == "true"
        self.SCRAPE_JOBICY: bool         = os.getenv("SCRAPE_JOBICY", "true").lower() == "true"
        self.SCRAPE_REMOTIVE: bool       = os.getenv("SCRAPE_REMOTIVE", "true").lower() == "true"
        self.SCRAPE_ARBEITNOW: bool      = os.getenv("SCRAPE_ARBEITNOW", "true").lower() == "true"
        self.SCRAPE_HACKERNEWS: bool     = os.getenv("SCRAPE_HACKERNEWS", "true").lower() == "true"
        self.MAX_JOBS_PER_BOARD: int     = int(os.getenv("MAX_JOBS_PER_BOARD", 50))
        # ── Scoring & docs
        self.MIN_MATCH_SCORE: int           = int(os.getenv("MIN_MATCH_SCORE", 60))
        self.LLM_SCORING: bool              = os.getenv("LLM_SCORING", "false").lower() == "true"
        # Documents are assembled offline from the parsed CV. Only jobs
        # scoring at or above this get an LLM polish pass (1 call, not 3).
        # Set to 101 to disable polishing and run with zero API calls.
        self.LLM_POLISH_MIN_SCORE: int      = int(os.getenv("LLM_POLISH_MIN_SCORE", 85))
        self.GENERATE_DOCS_WITHOUT_HR: bool = os.getenv("GENERATE_DOCS_WITHOUT_HR", "true").lower() == "true"

        # ── Paths
        self.OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")
        self.INPUT_DIR: str  = os.getenv("INPUT_DIR", "input")
        self.DB_PATH: str    = os.getenv("DB_PATH", "jobhunter.db")

        # ── Flask
        self.FLASK_PORT: int   = int(os.getenv("FLASK_PORT", 5000))
        self.FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"
        self.TIMEZONE: str     = os.getenv("TIMEZONE", "UTC")

        # ── Proxy / SOCKS
        self.PROXY_ENABLED: bool = os.getenv("PROXY_ENABLED", "false").lower() == "true"
        raw_proxies = os.getenv("PROXY_LIST", "")
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
        self.SMTP_HOST: str      = os.getenv("SMTP_HOST", "")
        self.SMTP_PORT: int      = int(os.getenv("SMTP_PORT", "587"))
        self.SMTP_USER: str      = os.getenv("SMTP_USER", "")
        self.SMTP_PASSWORD: str  = os.getenv("SMTP_PASSWORD", "")
        self.SMTP_FROM: str      = os.getenv("SMTP_FROM", "")
        self.SMTP_TLS: bool      = os.getenv("SMTP_TLS", "true").lower() == "true"
        self.SMTP_AUTO_SEND: bool   = os.getenv("SMTP_AUTO_SEND", "false").lower() == "true"
        self.SMTP_ATTACH_PDF: bool  = os.getenv("SMTP_ATTACH_PDF", "true").lower() == "true"
        self.SMTP_ATTACH_DOCX: bool = os.getenv("SMTP_ATTACH_DOCX", "false").lower() == "true"
        self.SMTP_RETRY_COUNT: int  = int(os.getenv("SMTP_RETRY_COUNT", "1"))
        self.SMTP_FORMAT: str       = os.getenv("SMTP_FORMAT", "plain").lower()
        self.SMTP_THROTTLE_SECONDS: int = int(os.getenv("SMTP_THROTTLE_SECONDS", "8"))
        self.SMTP_REPLY_TO: str     = os.getenv("SMTP_REPLY_TO", self.CANDIDATE_EMAIL or self.SMTP_FROM)
        self.EMAIL_DAILY_LIMIT: int = int(os.getenv("EMAIL_DAILY_LIMIT", "100"))
        self.BREVO_API_KEY: str     = os.getenv("BREVO_API_KEY", "")
        self.BREVO_FROM: str        = os.getenv("BREVO_FROM", self.SMTP_FROM or self.CANDIDATE_EMAIL)
        self.BREVO_FROM_NAME: str   = os.getenv("BREVO_FROM_NAME", self.CANDIDATE_NAME)
        self.SMTP2GO_API_KEY: str   = os.getenv("SMTP2GO_API_KEY", "")
        self.SMTP2GO_FROM: str      = os.getenv("SMTP2GO_FROM", self.SMTP_FROM or self.CANDIDATE_EMAIL)
        self.SMTP2GO_FROM_NAME: str = os.getenv("SMTP2GO_FROM_NAME", self.CANDIDATE_NAME)
        self.EMAIL_PROVIDERS: list[dict] = self._load_email_providers()
        self.SMTP_PROVIDERS: list[dict] = self.EMAIL_PROVIDERS

        # ── Deduplication
        # Days to remember a sent email address before allowing re-send
        self.DEDUP_WINDOW_DAYS: int = int(os.getenv("DEDUP_WINDOW_DAYS", "30"))

        # ── Follow-up
        self.FOLLOW_UP_ENABLED: bool = os.getenv("FOLLOW_UP_ENABLED", "true").lower() == "true"
        # Days after initial send before follow-up fires (default 6 = ~1 working week)
        self.FOLLOW_UP_DAYS: int = int(os.getenv("FOLLOW_UP_DAYS", "6"))

        # ── IMAP (for reply detection)
        # Optional IMAP reply detection. Leave blank to disable.
        self.IMAP_HOST: str     = os.getenv("IMAP_HOST", "")
        self.IMAP_PORT: int     = int(os.getenv("IMAP_PORT", "993"))
        self.IMAP_USER: str     = os.getenv("IMAP_USER", self.SMTP_USER)
        self.IMAP_PASSWORD: str = os.getenv("IMAP_PASSWORD", self.SMTP_PASSWORD)

        # ── Ollama fallback (used when all Groq keys are exhausted)
        # Models are tried in the order listed — first available wins.
        # Recommended order for this workload (you have all of these):
        #   qwen2.5-coder:32b  — best for doc generation (slow but high quality)
        #   gemma3:12b         — good for scoring + research
        #   qwen3:6b           — fast, reliable JSON extraction
        #   mistral            — quick fallback for any task
        self.OLLAMA_ENABLED: bool  = os.getenv("OLLAMA_ENABLED", "false").lower() == "true"
        self.OLLAMA_BASE_URL: str  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.OLLAMA_MODELS: str    = os.getenv(
            "OLLAMA_MODELS",
            "qwen2.5-coder:32b,gemma3:12b,mistral:latest"
        )

        # ── Scheduler (auto-run pipeline on a cron schedule)
        # SCHEDULE_CRON uses standard 5-field cron syntax. Examples:
        #   "0 8 * * 1-5"   = Mon-Fri at 08:00 local time
        #   "0 9 * * *"     = Every day at 09:00
        self.SCHEDULE_ENABLED: bool  = os.getenv("SCHEDULE_ENABLED", "false").lower() == "true"
        self.SCHEDULE_CRON: str      = os.getenv("SCHEDULE_CRON", "0 8 * * 1-5")
        self.SCHEDULE_FOLLOWUP: bool = os.getenv("SCHEDULE_FOLLOWUP", "true").lower() == "true"

        # ── Notifications (Telegram)
        # Create a bot at t.me/BotFather, get your chat_id via t.me/userinfobot
        self.TELEGRAM_ENABLED: bool  = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
        self.TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID: str   = os.getenv("TELEGRAM_CHAT_ID", "")

        # ── Portal auto-fill (Playwright)
        self.PORTAL_ENABLED: bool    = os.getenv("PORTAL_ENABLED", "false").lower() == "true"
        self.PORTAL_HEADLESS: bool   = os.getenv("PORTAL_HEADLESS", "true").lower() == "true"
        self.PORTAL_SUBMIT: bool     = os.getenv("PORTAL_SUBMIT", "true").lower() == "true"
        self.PORTAL_TIMEOUT_MS: int  = int(os.getenv("PORTAL_TIMEOUT_MS", "30000"))

        # ── CV Version Management
        # Leave blank = auto-pick first file found in input/ (original behaviour)
        # Set to a filename to pin a specific CV (e.g. CV_web3.docx)
        self.ACTIVE_CV: str = os.getenv("ACTIVE_CV", "")


    def _load_hunter_keys(self) -> list[str]:
        return self._load_key_pool("HUNTER_API_KEY")

    def _load_key_pool(self, base_name: str) -> list[str]:
        keys: list[str] = []
        k = os.getenv(base_name, "").strip()
        if k:
            keys.append(k)
        for i in range(1, 20):
            k = os.getenv(f"{base_name}_{i}", "").strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    def _load_email_providers(self) -> list[dict]:
        providers: list[dict] = []

        def add(name: str, api_key: str, from_addr: str, from_name: str):
            if api_key and from_addr:
                providers.append({
                    "name": name,
                    "api_key": api_key,
                    "from": from_addr,
                    "from_name": from_name or self.CANDIDATE_NAME,
                })

        add("brevo", self.BREVO_API_KEY, self.BREVO_FROM, self.BREVO_FROM_NAME)
        add("smtp2go", self.SMTP2GO_API_KEY, self.SMTP2GO_FROM, self.SMTP2GO_FROM_NAME)
        return providers


config = Config()
