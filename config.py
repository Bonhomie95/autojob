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

        # ── Target roles & keywords
        self.TARGET_ROLES: list[str] = [
            r.strip()
            for r in os.getenv("TARGET_ROLES",
                                "Software Developer,Full Stack Engineer,Backend Engineer").split(",")
            if r.strip()
        ]
        self.KEYWORDS: list[str] = [
            k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()
        ]
        self.BLACKLIST_KEYWORDS: list[str] = [
            k.strip().lower()
            for k in os.getenv("BLACKLIST_KEYWORDS", "internship,unpaid").split(",")
            if k.strip()
        ]
        self.EXPERIENCE_LEVEL: list[str] = [
            e.strip()
            for e in os.getenv("EXPERIENCE_LEVEL", "mid,senior").split(",")
            if e.strip()
        ]

        # ── Salary
        self.MIN_SALARY: int = int(os.getenv("MIN_SALARY", 0))
        self.MAX_SALARY: int = int(os.getenv("MAX_SALARY", 999999))
        self.SALARY_CURRENCY: str = os.getenv("SALARY_CURRENCY", "USD")

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
        self.CANDIDATE_PROJECTS: list[str] = [
            p.strip() for p in os.getenv("CANDIDATE_PROJECTS", "").split(",") if p.strip()
        ]

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
        self.LINKEDIN_EMAIL: str         = os.getenv("LINKEDIN_EMAIL", "")
        self.LINKEDIN_PASSWORD: str      = os.getenv("LINKEDIN_PASSWORD", "")

        # ── Scoring & docs
        self.MIN_MATCH_SCORE: int           = int(os.getenv("MIN_MATCH_SCORE", 60))
        self.GENERATE_DOCS_WITHOUT_HR: bool = os.getenv("GENERATE_DOCS_WITHOUT_HR", "true").lower() == "true"
        # Fetch a brief company summary before scoring for richer personalisation
        self.ENRICH_COMPANY_DATA: bool      = os.getenv("ENRICH_COMPANY_DATA", "true").lower() == "true"

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

        # ── SMTP
        self.SMTP_HOST: str      = os.getenv("SMTP_HOST", "smtp.gmail.com")
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

        # ── Deduplication
        # Days to remember a sent email address before allowing re-send
        self.DEDUP_WINDOW_DAYS: int = int(os.getenv("DEDUP_WINDOW_DAYS", "30"))

        # ── Follow-up
        self.FOLLOW_UP_ENABLED: bool = os.getenv("FOLLOW_UP_ENABLED", "true").lower() == "true"
        # Days after initial send before follow-up fires (default 6 = ~1 working week)
        self.FOLLOW_UP_DAYS: int = int(os.getenv("FOLLOW_UP_DAYS", "6"))

        # ── IMAP (for reply detection)
        # Defaults to Gmail IMAP; works with any IMAP server
        self.IMAP_HOST: str     = os.getenv("IMAP_HOST", "imap.gmail.com")
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
        keys: list[str] = []
        # Single key
        k = os.getenv("HUNTER_API_KEY", "").strip()
        if k:
            keys.append(k)
        # Numbered pool
        for i in range(1, 20):
            k = os.getenv(f"HUNTER_API_KEY_{i}", "").strip()
            if k and k not in keys:
                keys.append(k)
        return keys


config = Config()
