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
        """Re-read .env and update all values in place."""
        load_dotenv(override=True)
        self._load()

    def _load(self):
        # ── Groq
        self.GROQ_API_KEYS: list[str] = _load_groq_keys()
        self.GROQ_MODEL: str = "llama-3.3-70b-versatile"

        # ── Optional integrations
        self.HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")

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
        self.MAX_JOBS_PER_BOARD: int     = int(os.getenv("MAX_JOBS_PER_BOARD", 50))
        self.LINKEDIN_EMAIL: str         = os.getenv("LINKEDIN_EMAIL", "")
        self.LINKEDIN_PASSWORD: str      = os.getenv("LINKEDIN_PASSWORD", "")

        # ── Scoring & docs
        self.MIN_MATCH_SCORE: int           = int(os.getenv("MIN_MATCH_SCORE", 60))
        self.GENERATE_DOCS_WITHOUT_HR: bool = os.getenv("GENERATE_DOCS_WITHOUT_HR", "true").lower() == "true"

        # ── Paths
        self.OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")
        self.INPUT_DIR: str  = os.getenv("INPUT_DIR", "input")
        self.DB_PATH: str    = os.getenv("DB_PATH", "jobhunter.db")

        # ── Flask
        self.FLASK_PORT: int  = int(os.getenv("FLASK_PORT", 5000))
        self.FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"
        self.TIMEZONE: str    = os.getenv("TIMEZONE", "UTC")

        # ── Proxy / SOCKS
        self.PROXY_ENABLED: bool = os.getenv("PROXY_ENABLED", "false").lower() == "true"
        # Rotate through each on failure. Format: socks5://user:pass@host:port  or  host:port
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
        self.SMTP_HOST: str      = os.getenv("SMTP_HOST", "bonhomieinc.dev")
        self.SMTP_PORT: int      = int(os.getenv("SMTP_PORT", "465"))
        self.SMTP_USER: str      = os.getenv("SMTP_USER", "bonhomie@bonhomieinc.dev")
        self.SMTP_PASSWORD: str  = os.getenv("SMTP_PASSWORD", "")
        self.SMTP_FROM: str      = os.getenv("SMTP_FROM", "bonhomie@bonhomieinc.dev")
        self.SMTP_TLS: bool      = os.getenv("SMTP_TLS", "false").lower() == "true"
        self.SMTP_AUTO_SEND: bool  = os.getenv("SMTP_AUTO_SEND", "false").lower() == "true"
        self.SMTP_ATTACH_PDF: bool  = os.getenv("SMTP_ATTACH_PDF", "true").lower() == "true"
        self.SMTP_ATTACH_DOCX: bool = os.getenv("SMTP_ATTACH_DOCX", "false").lower() == "true"
        self.SMTP_RETRY_COUNT: int  = int(os.getenv("SMTP_RETRY_COUNT", "1"))


config = Config()
