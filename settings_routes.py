"""
settings.py — Flask blueprint for the Settings UI.

Renders an editable form and saves changes to the database (the UI-editable
source of truth). API keys are never shown or modified through this interface.
"""

import os
import re
from flask import Blueprint, render_template, request, redirect, url_for, jsonify

settings_bp = Blueprint("settings", __name__)


def _active_profile() -> dict:
    """
    The parsed profile for whichever CV is currently active. Cached by file
    hash, so calling this from a request handler is cheap.
    """
    try:
        from pipeline import _find_cv
        from core.cv_profile import get_profile

        cv_path = _find_cv()
        return get_profile(cv_path) if cv_path else {}
    except Exception:  # a broken CV must not take the settings page down
        return {}

# ── Fields shown in the UI (ordered by section) ───────────────────────────────
# Format: (env_key, label, field_type, section, hint)
# field_type: "text" | "bool" | "number" | "textarea" | "select"
# For "select", add options as extra tuple element

SETTINGS_FIELDS = [
    # ── Candidate
    (
        "CANDIDATE_NAME",
        "Full Name",
        "text",
        "Candidate Info",
        "Your full name as it appears on documents",
    ),
    (
        "CANDIDATE_EMAIL",
        "Email",
        "text",
        "Candidate Info",
        "Contact email on CV and cover letters",
    ),
    (
        "CANDIDATE_PHONE",
        "Phone",
        "text",
        "Candidate Info",
        "Phone number including country code",
    ),
    (
        "CANDIDATE_LOCATION",
        "Location",
        "text",
        "Candidate Info",
        "e.g. Lagos, Nigeria (Open to Remote)",
    ),
    (
        "CANDIDATE_LINKEDIN",
        "LinkedIn URL",
        "text",
        "Candidate Info",
        "e.g. linkedin.com/in/yourname",
    ),
    (
        "CANDIDATE_GITHUB",
        "GitHub URL",
        "text",
        "Candidate Info",
        "e.g. github.com/yourname",
    ),
    (
        "GITHUB_TOKEN",
        "GitHub Token",
        "password",
        "Candidate Info",
        "Personal access token (classic: read:user + repo scope). When set, AutoJob fetches your real repo "
        "URLs, descriptions, and tech stacks to write accurate CV bullets automatically. "
        "Create one at github.com/settings/tokens",
    ),
    (
        "CANDIDATE_PROJECT_URLS",
        "Project URLs (JSON)",
        "textarea",
        "Candidate Info",
        'Optional: explicit project → URL map as JSON. These override auto-fetched GitHub URLs. '
        'Format: {"ProjectName":"https://github.com/you/repo","AnotherProject":"https://live-demo.com"} '
        "Leave blank to use auto-fetched GitHub URLs.",
    ),
    # ── Automation (the operational controls: how many, and when)
    (
        "MAX_APPLICATIONS_PER_RUN",
        "Applications per run",
        "number",
        "Automation",
        "How many jobs to apply to each run — the top matches by score. 0 = no limit.",
    ),
    (
        "EMAIL_DAILY_LIMIT",
        "Daily email limit",
        "number",
        "Automation",
        "Hard cap on emails sent per day across all runs. 0 = no limit.",
    ),
    (
        "SCHEDULE_ENABLED",
        "Run automatically",
        "bool",
        "Automation",
        "When on, AutoJob runs itself on the schedule below (test your email first).",
    ),
    (
        "SCHEDULE_TIME",
        "Run at (24h, HH:MM)",
        "text",
        "Automation",
        "Local time to run, e.g. 08:00 or 17:30 (uses the Timezone set below).",
    ),
    (
        "SCHEDULE_FREQUENCY",
        "How often",
        "select",
        "Automation",
        "How often the automatic run happens.",
        ["weekdays", "daily"],
    ),
    (
        "SCHEDULE_FOLLOWUP",
        "Auto follow-ups",
        "bool",
        "Automation",
        "After each scheduled run, send follow-ups for applications with no reply.",
    ),
    # ── Target Roles
    (
        "BLACKLIST_KEYWORDS",
        "Blacklist Keywords",
        "textarea",
        "Job Targeting",
        "Jobs containing these keywords are auto-skipped (comma-separated)",
    ),
    # ── Salary
    (
        "MIN_SALARY",
        "Min Salary",
        "number",
        "Salary",
        "Minimum acceptable salary (where listed)",
    ),
    # ── Location
    ("REMOTE_ONLY", "Remote Only", "bool", "Location", "Only show remote jobs"),
    (
        "TARGET_COUNTRIES",
        "Target Countries",
        "textarea",
        "Location",
        "Comma-separated list of countries (used when Remote Only is off)",
    ),
    # ── Scraper Toggles
    (
        "SCRAPE_LINKEDIN",
        "LinkedIn",
        "bool",
        "Job Sources",
        "Guest API — no login needed",
    ),
    (
        "SCRAPE_WEWORKREMOTELY",
        "WeWorkRemotely",
        "bool",
        "Job Sources",
        "RSS feed — very reliable",
    ),
    ("SCRAPE_JOBICY", "Jobicy", "bool", "Job Sources", "Public JSON API — reliable"),
    (
        "SCRAPE_REMOTIVE",
        "Remotive",
        "bool",
        "Job Sources",
        "Public JSON API — reliable",
    ),
    (
        "SCRAPE_ARBEITNOW",
        "Arbeitnow",
        "bool",
        "Job Sources",
        "Public JSON API — good for international remote",
    ),
    (
        "SCRAPE_REMOTEOK",
        "RemoteOK",
        "bool",
        "Job Sources",
        "JSON API — works, occasionally rate-limits",
    ),
    (
        "SCRAPE_INDEED",
        "Indeed (RSS)",
        "bool",
        "Job Sources",
        "RSS feed — less reliable than API boards",
    ),
    (
        "SCRAPE_GOOGLE",
        "Google Jobs",
        "bool",
        "Job Sources",
        "Experimental — results vary by region/IP. Off by default.",
    ),
    (
        "MAX_JOBS_PER_BOARD",
        "Max Jobs per Board",
        "number",
        "Job Sources",
        "Max listings fetched from each source per run",
    ),
    # ── Scoring & Documents
    (
        "MIN_MATCH_SCORE",
        "Min Match Score",
        "number",
        "Scoring & Documents",
        "Groq score threshold (0–100) — jobs below this are skipped",
    ),
    (
        "LLM_SCORING",
        "Use AI Scoring",
        "bool",
        "Scoring & Documents",
        "Off by default to save tokens. When off, AutoJob uses keyword scoring and reserves AI for document generation/fallbacks.",
    ),
    (
        "CONTACT_SEARCH_ENABLED",
        "Search Leads",
        "bool",
        "Scoring & Documents",
        "Use headless Chromium/search engines to discover recruiter or careers emails when APIs do not return a lead.",
    ),
    (
        "CONTACT_AI_FALLBACK",
        "AI Contact Fallback",
        "bool",
        "Scoring & Documents",
        "Use AI only as a last resort to parse contact info explicitly present in the posting.",
    ),
    (
        "GENERATE_DOCS_WITHOUT_HR",
        "Generate Docs Without HR Contact",
        "bool",
        "Scoring & Documents",
        "ON (recommended) — generate docs for every qualified job regardless of contact info. "
        "OFF — only generate docs when at least one contact signal exists (HR email, application email, or apply URL). "
        "Auto-send always requires a real email address regardless of this setting.",
    ),
    # ── Proxy / SOCKS
    (
        "PROXY_ENABLED",
        "Enable Proxy Rotation",
        "bool",
        "Proxy / SOCKS",
        "Route scraper requests through SOCKS/HTTP proxies to avoid IP blocks",
    ),
    (
        "PROXY_LIST",
        "Proxy List",
        "textarea",
        "Proxy / SOCKS",
        "One proxy per line (or comma-separated). Formats: "
        "socks5://user:pass@host:port  ·  socks5://host:port  ·  host:port (assumed socks5)  ·  http://host:port. "
        "Failed proxies are automatically skipped for that run and the next one tried.",
    ),
    # ── App
    (
        "TIMEZONE",
        "Timezone",
        "text",
        "App",
        "e.g. Africa/Lagos, America/New_York, Europe/London",
    ),
    (
        "FLASK_PORT",
        "Port",
        "number",
        "App",
        "Web UI port (restart required to take effect)",
    ),
    # ── Email / SMTP
    (
        "SMTP_HOST",
        "SMTP Host",
        "text",
        "Email (SMTP)",
        "Namecheap cPanel: bonhomieinc.dev  (SSL port 465)",
    ),
    (
        "SMTP_PORT",
        "SMTP Port",
        "number",
        "Email (SMTP)",
        "465 = SSL (recommended for Namecheap) · 587 = STARTTLS · 25 = plain",
    ),
    (
        "SMTP_USER",
        "SMTP Username",
        "text",
        "Email (SMTP)",
        "SMTP username for a generic fallback sender. Leave blank when using MailerSend/SMTP2GO.",
    ),
    (
        "SMTP_FROM",
        "From Address",
        "text",
        "Email (SMTP)",
        "The email address shown as sender — usually same as username",
    ),
    (
        "SMTP_TLS",
        "Use STARTTLS",
        "bool",
        "Email (SMTP)",
        "Port 587 only. Keep OFF for port 465 (SSL handles encryption automatically)",
    ),
    (
        "SMTP_AUTO_SEND",
        "Auto-Send After Pipeline",
        "bool",
        "Email (SMTP)",
        "Automatically email each application right after docs are generated. "
        "Only fires when an HR or application email was found for that job.",
    ),
    (
        "SMTP_ATTACH_PDF",
        "Attach PDF",
        "bool",
        "Email (SMTP)",
        "Attach CV.pdf + CoverLetter.pdf",
    ),
    (
        "SMTP_ATTACH_DOCX",
        "Attach DOCX",
        "bool",
        "Email (SMTP)",
        "Attach CV.docx + CoverLetter.docx",
    ),
    (
        "SMTP_RETRY_COUNT",
        "Retry Count",
        "number",
        "Email (SMTP)",
        "How many times to retry a failed send before giving up (0 = no retries)",
    ),
    (
        "SMTP_REPLY_TO",
        "Reply-To",
        "text",
        "Email (SMTP)",
        "Replies go here even when sending through MailerSend, SMTP2GO, or another sender.",
    ),
    (
        "EMAIL_DAILY_LIMIT",
        "Daily Send Limit",
        "number",
        "Email (SMTP)",
        "Hard daily cap across all senders. Set 0 to disable.",
    ),
]

# Password is handled separately — never shown, only updated if non-blank is submitted
SMTP_PASSWORD_KEY = "SMTP_PASSWORD"

# Keys that should never be shown or edited through the UI
HIDDEN_KEYS = {
    "GROQ_API_KEY",
    "HUNTER_API_KEY",
    "PROSPEO_API_KEY",
    "REOON_API_KEY",
    "MILLION_VERIFIER_API_KEY",
    "MAILERSEND_API_KEY",
    "MAILERSEND_SMTP_PASSWORD",
    "SMTP2GO_API_KEY",
    "SMTP2GO_SMTP_PASSWORD",
    "GITHUB_TOKEN",
    *{f"GROQ_API_KEY_{i}" for i in range(1, 20)},
    *{f"HUNTER_API_KEY_{i}" for i in range(1, 20)},
    *{f"PROSPEO_API_KEY_{i}" for i in range(1, 20)},
    *{f"REOON_API_KEY_{i}" for i in range(1, 20)},
    *{f"MILLION_VERIFIER_API_KEY_{i}" for i in range(1, 20)},
}


def _read_settings() -> dict[str, str]:
    """UI-editable settings stored in the database (the source of truth)."""
    from database import get_settings_map

    return {k: v for k, v in get_settings_map().items() if k not in HIDDEN_KEYS}


def _write_env(updates: dict[str, str]):
    """
    Persist settings to the database and refresh the running config.

    Named for backwards compatibility — nothing is written to .env anymore.
    The database is what a deployed user (with no shell access) can edit from
    the dashboard, and it is shared by every worker/process.
    """
    from database import set_settings
    from config import config

    set_settings(updates)
    config.reload()

    # Apply schedule changes to the live scheduler without a restart.
    try:
        from scheduler import reschedule

        reschedule()
    except Exception:
        pass


def _get_current_values() -> dict[str, str]:
    """Current value for each settings field: DB override, else env, else ''."""
    saved = _read_settings()
    result: dict[str, str] = {}
    for key, *_ in SETTINGS_FIELDS:
        result[key] = saved.get(key) if key in saved else os.getenv(key, "")
    return result


@settings_bp.route("/settings", methods=["GET"])
def settings_page():
    current = _get_current_values()
    # Group fields by section
    sections: dict[str, list[tuple]] = {}
    for field in SETTINGS_FIELDS:
        key, label, ftype, section, hint = field[:5]
        options = field[5] if len(field) > 5 else None
        sections.setdefault(section, []).append(
            (key, label, ftype, hint, current.get(key, ""), options)
        )
    return render_template("settings.html", sections=sections)


@settings_bp.route("/settings", methods=["POST"])
def settings_save():
    updates: dict[str, str] = {}
    for field in SETTINGS_FIELDS:
        key, _, ftype, *_ = field
        if ftype == "bool":
            updates[key] = "true" if request.form.get(key) else "false"
        elif ftype == "password":
            # Only write password/token fields when the user actually typed a value
            val = request.form.get(key, "").strip()
            if val:
                updates[key] = val
            # else: leave existing .env value untouched
        else:
            val = request.form.get(key, "").strip()
            if ftype == "textarea":
                val = ", ".join(
                    p.strip() for p in re.split(r"[\n,]+", val) if p.strip()
                )
            updates[key] = val

    # SMTP Password: only write if non-blank (blank = keep existing)
    pwd = request.form.get("SMTP_PASSWORD", "").strip()
    if pwd:
        updates["SMTP_PASSWORD"] = pwd

    _write_env(updates)
    return redirect(url_for("settings.settings_page") + "?saved=1")


@settings_bp.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(_get_current_values())


@settings_bp.route("/api/settings", methods=["POST"])
def api_settings_post():
    data = request.json or {}
    safe = {k: str(v) for k, v in data.items() if k not in HIDDEN_KEYS}
    # Password is allowed via this route (it's masked in UI)
    if "SMTP_PASSWORD" in data:
        safe["SMTP_PASSWORD"] = str(data["SMTP_PASSWORD"])
    _write_env(safe)
    return jsonify({"status": "ok"})


@settings_bp.route("/api/github/repos", methods=["GET"])
def api_github_repos():
    """
    Fetch the candidate's GitHub repos and return a summary + suggested
    CANDIDATE_PROJECT_URLS JSON so the user can review and save it.

    Returns JSON:
      {
        "repos": [{name, html_url, description, language, topics}, ...],
        "suggested_urls": {"RepoName": "https://github.com/..."},
        "error": null | "message"
      }
    """
    from core.github_client import GitHubClient
    from config import config

    token    = os.getenv("GITHUB_TOKEN", "").strip()
    username = config.CANDIDATE_GITHUB

    if not token and not username:
        return jsonify({"repos": [], "suggested_urls": {}, "error": "No GITHUB_TOKEN or CANDIDATE_GITHUB set."})

    gh    = GitHubClient(token=token, username=username)
    repos = gh.all_repos_summary()

    if not repos:
        return jsonify({
            "repos": [],
            "suggested_urls": {},
            "error": "No repos found. Check your token scope (needs read:user + repo) or GitHub URL."
        })

    # Project names come from the parsed CV rather than a hand-typed list.
    proj_names = [p.get("name", "") for p in _active_profile().get("projects", [])
                  if p.get("name")]
    url_map    = gh.project_url_map(proj_names) if proj_names else {}
    # Explicit overrides already saved win over anything fetched.
    url_map.update(config.CANDIDATE_PROJECT_URLS)

    return jsonify({
        "repos":          repos,
        "suggested_urls": url_map,
        "error":          None,
    })
