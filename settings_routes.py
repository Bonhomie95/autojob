"""
settings.py — Flask blueprint for the Settings UI.
Reads the .env file, renders an editable form, and writes changes back.
API keys are never shown or modified through this interface.
"""

import os
import re
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv

settings_bp = Blueprint("settings", __name__)

ENV_PATH = Path(".env")

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
        "CANDIDATE_PROJECTS",
        "Project Names",
        "textarea",
        "Candidate Info",
        "Comma-separated list of your exact project names — overrides auto-detection from CV. "
        "Leave blank to auto-detect. Example: PulseQuiz,Property Wey International,AI Trader",
    ),
    # ── Education (up to 5 entries)
    (
        "CANDIDATE_EDUCATION_1",
        "Education 1",
        "text",
        "Education",
        "Format: Degree | Institution | Year(s)  e.g. BSc, Computer Science | Lagos State University | 2018–2023",
    ),
    (
        "CANDIDATE_EDUCATION_2",
        "Education 2",
        "text",
        "Education",
        "Same format — leave blank if not needed",
    ),
    (
        "CANDIDATE_EDUCATION_3",
        "Education 3",
        "text",
        "Education",
        "Same format — leave blank if not needed",
    ),
    (
        "CANDIDATE_EDUCATION_4",
        "Education 4",
        "text",
        "Education",
        "Same format — leave blank if not needed",
    ),
    (
        "CANDIDATE_EDUCATION_5",
        "Education 5",
        "text",
        "Education",
        "Same format — leave blank if not needed",
    ),
    # ── Target Roles
    (
        "TARGET_ROLES",
        "Target Roles",
        "textarea",
        "Job Targeting",
        "Comma-separated list of job titles to search for",
    ),
    (
        "KEYWORDS",
        "Match Keywords",
        "textarea",
        "Job Targeting",
        "Comma-separated keywords that should match in job descriptions",
    ),
    (
        "BLACKLIST_KEYWORDS",
        "Blacklist Keywords",
        "textarea",
        "Job Targeting",
        "Jobs containing these keywords are auto-skipped (comma-separated)",
    ),
    (
        "EXPERIENCE_LEVEL",
        "Experience Level",
        "text",
        "Job Targeting",
        "Comma-separated: junior, mid, senior",
    ),
    # ── Salary
    (
        "MIN_SALARY",
        "Min Salary",
        "number",
        "Salary",
        "Minimum acceptable salary (where listed)",
    ),
    ("MAX_SALARY", "Max Salary", "number", "Salary", "Maximum salary range"),
    ("SALARY_CURRENCY", "Currency", "text", "Salary", "e.g. USD, GBP, EUR"),
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
        "Your full email address: bonhomie@bonhomieinc.dev",
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
]

# Password is handled separately — never shown, only updated if non-blank is submitted
SMTP_PASSWORD_KEY = "SMTP_PASSWORD"

# Keys that should never be shown or edited through the UI
HIDDEN_KEYS = {
    "GROQ_API_KEY",
    "HUNTER_API_KEY",
    "LINKEDIN_EMAIL",
    "LINKEDIN_PASSWORD",
    *{f"GROQ_API_KEY_{i}" for i in range(1, 20)},
}


def _read_env() -> dict[str, str]:
    """Read all key=value pairs from .env, preserving values as-is."""
    if not ENV_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key in HIDDEN_KEYS:
            continue
        # Strip inline comments and quotes
        val = val.split(" #")[0].strip().strip('"').strip("'")
        values[key] = val
    return values


def _write_env(updates: dict[str, str]):
    """
    Write updated values back to .env, preserving comments, structure,
    and any keys not managed by the UI (including API keys).
    """
    if not ENV_PATH.exists():
        # Create fresh from updates
        lines = [f"{k}={v}" for k, v in updates.items()]
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        load_dotenv(override=True)
        return

    original = ENV_PATH.read_text(encoding="utf-8")
    result_lines: list[str] = []
    written_keys: set[str] = set()

    for line in original.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            result_lines.append(line)
            continue
        key = stripped.split("=")[0].strip()
        if key in HIDDEN_KEYS:
            result_lines.append(line)
            continue
        if key in updates:
            result_lines.append(f"{key}={updates[key]}")
            written_keys.add(key)
        else:
            result_lines.append(line)

    # Append any new keys not previously in .env
    for key, val in updates.items():
        if key not in written_keys and key not in HIDDEN_KEYS:
            result_lines.append(f"{key}={val}")

    ENV_PATH.write_text("\n".join(result_lines) + "\n", encoding="utf-8")

    # Reload .env into os.environ and update the config singleton in place
    load_dotenv(override=True)
    from config import config

    config.reload()


def _get_current_values() -> dict[str, str]:
    """Get current values for all settings fields from .env + os.environ."""
    env_file_vals = _read_env()
    result: dict[str, str] = {}
    for key, *_ in SETTINGS_FIELDS:
        # Prefer .env file value, fallback to os.environ
        if key in env_file_vals:
            result[key] = env_file_vals[key]
        else:
            result[key] = os.getenv(key, "")
    return result


@settings_bp.route("/settings", methods=["GET"])
def settings_page():
    current = _get_current_values()
    # Group fields by section
    sections: dict[str, list[tuple]] = {}
    for field in SETTINGS_FIELDS:
        key, label, ftype, section, hint = field
        sections.setdefault(section, []).append(
            (key, label, ftype, hint, current.get(key, ""))
        )
    return render_template("settings.html", sections=sections)


@settings_bp.route("/settings", methods=["POST"])
def settings_save():
    updates: dict[str, str] = {}
    for field in SETTINGS_FIELDS:
        key, _, ftype, *_ = field
        if ftype == "bool":
            updates[key] = "true" if request.form.get(key) else "false"
        else:
            val = request.form.get(key, "").strip()
            if ftype == "textarea":
                val = ", ".join(
                    p.strip() for p in re.split(r"[\n,]+", val) if p.strip()
                )
            updates[key] = val

    # Password: only write if non-blank (blank = keep existing)
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
