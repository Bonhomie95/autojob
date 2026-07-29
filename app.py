import os
import json
import queue
import threading
import logging
from pathlib import Path
from datetime import datetime
from flask import (
    Flask,
    render_template,
    jsonify,
    request,
    redirect,
    url_for,
    send_from_directory,
    Response,
    stream_with_context,
)
from werkzeug.utils import secure_filename
from config import config
from database import (
    init_db,
    get_conn,
    get_all_jobs,
    get_job,
    get_recent_runs,
    get_stats,
    update_job,
    get_jobs_needing_follow_up,
)
from settings_routes import settings_bp
from scheduler import start_scheduler, scheduler_status
from notifier import test_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Optional: set SECRET_KEY to keep sessions stable across restarts. This is a
# single-user, no-login tool, so a random per-process key is fine by default.
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)
app.register_blueprint(settings_bp)


@app.route("/healthz")
def healthz():
    """Lightweight liveness probe for Docker / Render health checks."""
    return jsonify({"status": "ok"}), 200


@app.context_processor
def inject_share_meta():
    """Open Graph / Twitter meta for social shares (LinkedIn link previews)."""
    base = config.APP_BASE_URL or request.url_root.rstrip("/")
    return {
        "share": {
            "base_url": base,
            "url": base + "/",
            "image": base + "/static/og.png",
            "title": "AutoJob — your CV applies to jobs for you",
            "description": (
                "Upload your CV once. AutoJob finds real roles, tailors each "
                "application, sends it, and follows up — no sign-up required."
            ),
        }
    }

# ── Global run state ──────────────────────────────────────
_run_lock = threading.Lock()
_run_active = False
_progress_queue: queue.Queue = queue.Queue()

# ── Follow-up run state ───────────────────────────────────
_followup_lock = threading.Lock()
_followup_active = False
_followup_queue: queue.Queue = queue.Queue()


# ─────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────


@app.route("/")
def index():
    init_db()
    stats = get_stats()
    runs = get_recent_runs(5)
    jobs = get_all_jobs(20)
    cv_files = _list_cv_files()
    return render_template(
        "index.html",
        stats=stats,
        runs=runs,
        jobs=jobs,
        cv_files=cv_files,
        config=config,
        active_run=_run_active,
    )


# ─────────────────────────────────────────────────────────
# API — CV profile
# ─────────────────────────────────────────────────────────


@app.route("/api/profile")
def api_profile():
    """
    What the pipeline understands about the candidate, parsed from the CV.

    This is the closest thing to a "why did it do that" endpoint: the roles
    it will search for, the seniority it will filter by, and the skills it
    scores against all come from here.
    """
    from pipeline import _find_cv
    from core.cv_profile import get_profile, profile_is_sendable
    from core.discovery import derive_queries
    from core.tailor import years_phrase

    cv_path = _find_cv()
    if not cv_path:
        return jsonify(
            {
                "ok": False,
                "error": "No CV found in input/ — upload one to get started.",
            }
        )

    try:
        profile = get_profile(cv_path)
    except Exception as e:
        logger.exception("Profile parse failed")
        return jsonify({"ok": False, "error": f"Could not parse CV: {e}"})

    sendable, blockers = profile_is_sendable(profile)
    issues = profile.get("issues", [])

    return jsonify(
        {
            "ok": sendable,
            "source_file": Path(cv_path).name,
            "name": profile.get("name", ""),
            "email": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            "location": profile.get("location", ""),
            "seniority": profile.get("seniority", ""),
            "years_experience": profile.get("years_experience", 0),
            "years_label": years_phrase(profile.get("years_experience", 0)),
            "titles": profile.get("titles", []),
            "queries": derive_queries(profile),
            "skills_by_category": profile.get("skills_by_category", {}),
            "skill_count": len(profile.get("skills", [])),
            "experience": [
                {
                    "title": e.get("title", ""),
                    "company": e.get("company", ""),
                    "period": e.get("period", ""),
                    "bullet_count": len(e.get("bullets", [])),
                }
                for e in profile.get("experience", [])
            ],
            "projects": [p.get("name", "") for p in profile.get("projects", [])],
            "certifications": profile.get("certifications", []),
            "education": profile.get("education", []),
            "blockers": [b[6:].strip() for b in blockers],
            "warnings": [i[5:].strip() for i in issues if i.startswith("WARN:")],
            "notes": [i[5:].strip() for i in issues if i.startswith("INFO:")],
            # Fields the CV states more than once, awaiting a decision.
            "ambiguous": [
                {
                    "field": field,
                    "options": profile["contact_choices"][field]["options"],
                    "current": profile["contact_choices"][field]["value"],
                }
                for field in profile.get("ambiguous_fields", [])
            ],
            "contact_choices": profile.get("contact_choices", {}),
        }
    )


@app.route("/api/profile/choose", methods=["POST"])
def api_profile_choose():
    """
    Record which value to use for a contact field the CV states twice.

    Saved against this CV's hash, so it survives restarts and re-parses but
    does not leak onto a different CV — or onto an edited version of this
    one, where the old answer may no longer be right.
    """
    from pipeline import _find_cv
    from core.cv_profile import cv_hash, get_profile, CONTACT_FIELDS
    from database import save_cv_choice, clear_cv_choice

    data = request.json or {}
    field = (data.get("field") or "").strip()
    value = (data.get("value") or "").strip()

    if field not in CONTACT_FIELDS:
        return jsonify({"error": f"Unknown field: {field}"}), 400

    cv_path = _find_cv()
    if not cv_path:
        return jsonify({"error": "No CV found"}), 404
    digest = cv_hash(cv_path)

    if not value:
        clear_cv_choice(digest, field)
        return jsonify({"status": "cleared", "field": field})

    # Only accept a value the CV (or .env) actually offers — this endpoint
    # resolves an ambiguity, it does not set arbitrary contact details.
    profile = get_profile(cv_path)
    allowed = profile.get("contact_choices", {}).get(field, {}).get("options", [])
    if value not in allowed:
        return jsonify(
            {
                "error": f"{value!r} is not one of the values found for {field}",
                "options": allowed,
            }
        ), 400

    save_cv_choice(digest, field, value)
    logger.info(f"[Profile] {field} set to {value!r} for {Path(cv_path).name}")
    return jsonify({"status": "ok", "field": field, "value": value})


@app.route("/api/profile/reparse", methods=["POST"])
def api_profile_reparse():
    """Drop the cached parse and read the CV again."""
    from pipeline import _find_cv
    from core.cv_profile import cv_hash

    cv_path = _find_cv()
    if not cv_path:
        return jsonify({"error": "No CV found"}), 404
    with get_conn() as conn:
        conn.execute("DELETE FROM cv_profiles WHERE cv_hash = ?", (cv_hash(cv_path),))
    return jsonify({"status": "ok"})


@app.route("/jobs")
def jobs_page():
    status = request.args.get("status", "all")
    source = request.args.get("source", "all")
    all_jobs = get_all_jobs(500)

    if status != "all":
        all_jobs = [j for j in all_jobs if j["status"] == status]
    if source != "all":
        all_jobs = [j for j in all_jobs if j["source"] == source]

    return render_template("jobs.html", jobs=all_jobs, status=status, source=source)


@app.route("/job/<job_id>")
def job_detail(job_id):
    job = get_job(job_id)
    if not job:
        return "Job not found", 404
    output_files = []
    if job.get("output_dir") and Path(job["output_dir"]).exists():
        output_files = [
            f.name for f in Path(job["output_dir"]).iterdir() if f.is_file()
        ]
    return render_template("job_detail.html", job=job, output_files=output_files)


@app.route("/run", methods=["POST"])
def trigger_run():
    global _run_active
    from scheduler import is_pipeline_running, mark_pipeline_running

    # Guardrail: don't start a run — which can auto-send real applications —
    # until the email sender has been tested and confirmed working. This is
    # the "test it successfully before you start" gate.
    status = _email_status()
    if not status["configured"] or not status["verified"]:
        return jsonify(
            {
                "error": "Connect and test your email first — set up Gmail or SMTP "
                "and run the connection test until it passes.",
                "needs_email": True,
            }
        ), 412

    with _run_lock:
        if _run_active or is_pipeline_running():
            return jsonify({"error": "A run is already in progress"}), 409
        _run_active = True
        mark_pipeline_running(True)

    while not _progress_queue.empty():
        try:
            _progress_queue.get_nowait()
        except queue.Empty:
            break

    data = request.json or {}
    cv_filename = data.get("cv_filename", "")

    def run_in_thread():
        global _run_active
        try:
            from pipeline import run_pipeline

            def progress_cb(msg):
                _progress_queue.put(msg)

            run_pipeline(progress_cb=progress_cb, cv_filename=cv_filename)
        except Exception as e:
            _progress_queue.put(f"❌ Fatal error: {e}")
            logger.exception("Pipeline error")
        finally:
            _progress_queue.put("__DONE__")
            with _run_lock:
                _run_active = False
            mark_pipeline_running(False)

    threading.Thread(target=run_in_thread, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/stream")
def stream():
    def generate():
        while True:
            try:
                msg = _progress_queue.get(timeout=60)
                if msg == "__DONE__":
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps({'message': msg})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'ping': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/upload-cv", methods=["POST"])
def upload_cv():
    if "cv" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["cv"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    allowed = {".docx", ".pdf", ".txt"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        return jsonify({"error": f"Only {', '.join(allowed)} files allowed"}), 400
    filename = secure_filename(file.filename)
    dest = Path(config.INPUT_DIR) / filename
    dest.parent.mkdir(exist_ok=True)
    file.save(str(dest))
    return jsonify({"status": "ok", "filename": filename})


@app.route("/output/<path:filepath>")
def serve_output(filepath):
    output_dir = Path(config.OUTPUT_DIR).resolve()
    return send_from_directory(str(output_dir), filepath)


# ─────────────────────────────────────────────────────────
# API — stats & jobs
# ─────────────────────────────────────────────────────────


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/jobs")
def api_jobs():
    return jsonify(get_all_jobs(200))


@app.route("/api/runs")
def api_runs():
    return jsonify(get_recent_runs(10))


@app.route("/api/status")
def api_status():
    return jsonify({"active": _run_active, "followup_active": _followup_active})


@app.route("/api/job/<job_id>/status", methods=["PATCH"])
def update_job_status(job_id):
    data = request.json or {}
    new_status = data.get("status")
    if new_status not in ("pending", "done", "skipped", "applied"):
        return jsonify({"error": "Invalid status"}), 400
    update_job(job_id, status=new_status)
    return jsonify({"status": "ok"})


@app.route("/api/job/<job_id>/send", methods=["POST"])
def send_job_email(job_id):
    from mailer import send_application, smtp_configured

    if not smtp_configured():
        return jsonify({"error": "SMTP not configured"}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    output_dir = job.get("output_dir", "")
    if not output_dir:
        return jsonify({"error": "No output folder — run pipeline first"}), 400

    data = request.json or {}
    to_email = (
        data.get("to_email")
        or job.get("hr_email")
        or job.get("application_email")
        or ""
    )
    subject = data.get("subject")
    body = data.get("body")

    if not to_email:
        return jsonify({"error": "No recipient email address found"}), 400

    # Allow override of dedup check when manually sending
    skip_dedup = data.get("force", False)
    success, message = send_application(
        to_email, output_dir, subject, body, skip_dedup_check=skip_dedup
    )
    if success:
        update_job(
            job_id,
            email_status="sent",
            email_sent_at=datetime.utcnow().isoformat(),
            email_error="",
            status="applied",
        )
    else:
        update_job(job_id, email_status="failed", email_error=message)

    return jsonify({"success": success, "message": message})


# ─────────────────────────────────────────────────────────
# API — SMTP
# ─────────────────────────────────────────────────────────


@app.route("/api/smtp/test", methods=["POST"])
def test_smtp():
    from mailer import test_smtp as _test

    ok, msg = _test()
    # Only ever UPGRADE to verified here. A flaky re-test (e.g. a transient
    # SES timeout) must not re-lock a sender that already connected once —
    # the initial save flow is what strictly gates verification.
    if ok:
        _update_env_key("EMAIL_VERIFIED", "true")
        config.reload()
    return jsonify({"success": ok, "message": msg})


# ─────────────────────────────────────────────────────────
# API — Guided email setup (Gmail app password / generic SMTP)
# ─────────────────────────────────────────────────────────


def _email_status() -> dict:
    """A single source of truth for the onboarding gate."""
    config.reload()
    providers = getattr(config, "EMAIL_PROVIDERS", [])
    smtp = next((p for p in providers if p.get("name") == "smtp"), None)
    mode = ""
    from_addr = ""
    label = ""
    if smtp:
        label = smtp.get("label", "SMTP")
        mode = "gmail" if label == "Gmail" else "smtp"
        from_addr = smtp.get("from", "")
    elif providers:
        mode = "api"
        label = providers[0].get("label", providers[0].get("name", ""))
        from_addr = providers[0].get("from", "")

    # EMAIL_VERIFIED is stored in the database (set after a passing test), so
    # read it from there — not os.environ, which never sees the saved value.
    from database import get_setting

    verified = (get_setting("EMAIL_VERIFIED", "false") or "false").lower() == "true"
    return {
        "configured": bool(providers),
        "mode": mode,
        "label": label,
        "from": from_addr,
        "verified": verified and bool(providers),
        "auto_send": getattr(config, "SMTP_AUTO_SEND", False),
    }


@app.route("/api/email/status")
def api_email_status():
    return jsonify(_email_status())


@app.route("/api/email/save", methods=["POST"])
def api_email_save():
    """
    Save the user's chosen sender (Gmail app password or generic SMTP) and
    immediately verify it with a live connection test. Nothing is trusted
    until this test passes — that is what unlocks the Run button.
    """
    from mailer import test_smtp as _test

    data = request.json or {}
    mode = (data.get("mode") or "").strip().lower()
    updates: dict[str, str] = {"EMAIL_MODE": mode}

    if data.get("from_name"):
        updates["CANDIDATE_NAME"] = data["from_name"].strip()

    if mode == "gmail":
        addr = (data.get("gmail_address") or "").strip()
        pwd = (data.get("gmail_app_password") or "").replace(" ", "").strip()
        if not addr or not pwd:
            return jsonify({"success": False, "message": "Gmail address and App Password are both required."}), 400
        updates["GMAIL_ADDRESS"] = addr
        updates["GMAIL_APP_PASSWORD"] = pwd
        # Clear generic SMTP so only one real sender is active.
        updates["SMTP_HOST"] = ""
        updates["SMTP_USER"] = ""
        updates["SMTP_PASSWORD"] = ""
        updates["SMTP_REPLY_TO"] = addr
    elif mode == "smtp":
        host = (data.get("smtp_host") or "").strip()
        user = (data.get("smtp_user") or "").strip()
        pwd = (data.get("smtp_password") or "").strip()
        port = str(data.get("smtp_port") or "587").strip()
        from_addr = (data.get("smtp_from") or user).strip()
        # Encryption chosen by the user: "ssl" (465), "starttls" (587), "none".
        # Falls back to a port-based guess if not provided.
        security = (data.get("smtp_security") or "").strip().lower()
        if security not in ("ssl", "starttls", "none"):
            security = "ssl" if port == "465" else "starttls"
        if not host or not user or not pwd:
            return jsonify({"success": False, "message": "SMTP host, username and password are all required."}), 400
        updates["SMTP_HOST"] = host
        updates["SMTP_PORT"] = port
        updates["SMTP_USER"] = user
        updates["SMTP_PASSWORD"] = pwd
        updates["SMTP_FROM"] = from_addr
        updates["SMTP_SSL"] = "true" if security == "ssl" else "false"
        updates["SMTP_TLS"] = "true" if security == "starttls" else "false"
        updates["SMTP_REPLY_TO"] = from_addr
        # Clear Gmail so only one real sender is active.
        updates["GMAIL_ADDRESS"] = ""
        updates["GMAIL_APP_PASSWORD"] = ""
    else:
        return jsonify({"success": False, "message": "Choose Gmail or SMTP."}), 400

    _update_env_multi(updates)
    config.reload()

    ok, msg = _test()
    _update_env_key("EMAIL_VERIFIED", "true" if ok else "false")
    config.reload()
    return jsonify({"success": ok, "message": msg, "status": _email_status()})


@app.route("/api/smtp/send-test", methods=["POST"])
def send_test_email():
    from mailer import send_application, smtp_configured

    data = request.json or {}
    to_email = (data.get("to_email") or "").strip()

    if not to_email:
        return jsonify(
            {"success": False, "message": "No recipient email provided"}
        ), 400
    if not smtp_configured():
        return jsonify({"success": False, "message": "SMTP not configured"}), 400

    output_root = Path(config.OUTPUT_DIR)
    best_folder = None
    if output_root.exists():
        folders = sorted(
            [
                d
                for d in output_root.iterdir()
                if d.is_dir()
                and (d / "CV.pdf").exists()
                and (d / "EMAIL_DRAFT.txt").exists()
            ],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if folders:
            best_folder = str(folders[0])

    if not best_folder and output_root.exists():
        folders = sorted(
            [
                d
                for d in output_root.iterdir()
                if d.is_dir() and (d / "CV.pdf").exists()
            ],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if folders:
            best_folder = str(folders[0])

    if not best_folder:
        return jsonify(
            {
                "success": False,
                "message": "No generated documents found. Run the pipeline first.",
            }
        ), 400

    ok, msg = send_application(to_email, best_folder, skip_dedup_check=True)
    return jsonify(
        {"success": ok, "message": msg, "folder_used": Path(best_folder).name}
    )


# ─────────────────────────────────────────────────────────
# API — Follow-ups
# ─────────────────────────────────────────────────────────


@app.route("/api/followup/run", methods=["POST"])
def trigger_followup():
    """Manually trigger the follow-up cycle (runs in background thread)."""
    global _followup_active

    with _followup_lock:
        if _followup_active:
            return jsonify({"error": "Follow-up cycle already running"}), 409
        _followup_active = True

    while not _followup_queue.empty():
        try:
            _followup_queue.get_nowait()
        except queue.Empty:
            break

    def run_followup():
        global _followup_active
        try:
            from follow_up_scheduler import run_follow_up_cycle

            def cb(msg):
                _followup_queue.put(msg)

            run_follow_up_cycle(emit=cb)
        except Exception as e:
            _followup_queue.put(f"❌ Follow-up error: {e}")
            logger.exception("Follow-up error")
        finally:
            _followup_queue.put("__DONE__")
            with _followup_lock:
                _followup_active = False

    threading.Thread(target=run_followup, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/followup/stream")
def followup_stream():
    def generate():
        while True:
            try:
                msg = _followup_queue.get(timeout=60)
                if msg == "__DONE__":
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps({'message': msg})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'ping': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/followup/eligible")
def followup_eligible():
    """List jobs that are currently eligible for follow-up."""
    days = int(request.args.get("days", config.FOLLOW_UP_DAYS))
    jobs = get_jobs_needing_follow_up(follow_up_days=days)
    return jsonify({"count": len(jobs), "jobs": jobs})


@app.route("/api/job/<job_id>/followup", methods=["POST"])
def send_single_followup(job_id):
    """Manually send a follow-up for a specific job."""
    from mailer import send_follow_up

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    sent = send_follow_up(job)
    return jsonify({"success": sent})


# ─────────────────────────────────────────────────────────
# API — Analytics
# ─────────────────────────────────────────────────────────


@app.route("/api/analytics")
def api_analytics():
    """Rich analytics for the dashboard charts."""
    stats = get_stats()
    return jsonify(stats)


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────


def _list_cv_files() -> list[str]:
    input_dir = Path(config.INPUT_DIR)
    input_dir.mkdir(exist_ok=True)
    return [
        f.name
        for f in input_dir.iterdir()
        if f.suffix.lower() in (".docx", ".pdf", ".txt")
    ]


# ─────────────────────────────────────────────────────────
# API — Scheduler
# ─────────────────────────────────────────────────────────


@app.route("/api/scheduler/status")
def api_scheduler_status():
    return jsonify(scheduler_status())


@app.route("/api/scheduler/run-now", methods=["POST"])
def api_scheduler_run_now():
    """Trigger an immediate scheduled run (same as clicking Run but from cron context)."""
    import threading

    def _run():
        from pipeline import run_pipeline
        from notifier import notify_run_complete, notify_run_error

        try:
            result = run_pipeline()
            notify_run_complete(result)
        except Exception as e:
            notify_run_error(str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


# ─────────────────────────────────────────────────────────
# API — CV Versions
# ─────────────────────────────────────────────────────────


@app.route("/api/cv/list")
def api_cv_list():
    """List all CV files in the input directory."""
    input_dir = Path(config.INPUT_DIR)
    input_dir.mkdir(exist_ok=True)
    files = [
        {
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "active": f.name == (getattr(config, "ACTIVE_CV", "") or ""),
        }
        for f in sorted(
            input_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
        )
        if f.suffix.lower() in (".docx", ".pdf", ".txt")
    ]
    return jsonify({"files": files, "active": getattr(config, "ACTIVE_CV", "")})


@app.route("/api/cv/set-active", methods=["POST"])
def api_cv_set_active():
    """Pin a CV file as the active version for future runs."""
    data = request.json or {}
    filename = data.get("filename", "").strip()
    if filename and not (Path(config.INPUT_DIR) / filename).exists():
        return jsonify({"error": f"File not found: {filename}"}), 404
    # Write to .env
    _update_env_key("ACTIVE_CV", filename)
    config.reload()
    return jsonify({"status": "ok", "active": filename or "(auto)"})


@app.route("/api/cv/delete", methods=["POST"])
def api_cv_delete():
    data = request.json or {}
    filename = data.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "No filename provided"}), 400
    target = Path(config.INPUT_DIR) / filename
    if not target.exists():
        return jsonify({"error": "File not found"}), 404
    target.unlink()
    # If deleted file was the active one, clear it
    if getattr(config, "ACTIVE_CV", "") == filename:
        _update_env_key("ACTIVE_CV", "")
        config.reload()
    return jsonify({"status": "ok"})


# ─────────────────────────────────────────────────────────
# API — Portal
# ─────────────────────────────────────────────────────────


@app.route("/api/portal/status")
def api_portal_status():
    """Check if Playwright is installed and ready."""
    from portal_filler import portal_available

    available = portal_available()
    return jsonify(
        {
            "available": available,
            "enabled": str(getattr(config, "PORTAL_ENABLED", "false")).lower()
            == "true",
            "message": "Ready"
            if available
            else (
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            ),
        }
    )


@app.route("/api/job/<job_id>/portal-fill", methods=["POST"])
def api_portal_fill(job_id):
    """Manually trigger portal fill for a specific job."""
    from portal_filler import fill_portal

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    data = request.json or {}
    # Allow caller to override the apply URL
    if data.get("apply_url"):
        job["application_url"] = data["apply_url"]

    ok, msg = fill_portal(job)
    if ok:
        update_job(
            job_id,
            portal_status="submitted",
            portal_submitted_at=datetime.utcnow().isoformat(),
            portal_error="",
            status="applied",
        )
    else:
        update_job(job_id, portal_status="failed", portal_error=msg)

    return jsonify({"success": ok, "message": msg})


@app.route("/api/portal/eligible")
def api_portal_eligible():
    """Jobs that have an apply URL but no email sent — portal candidates."""
    from database import get_jobs_for_portal

    jobs = get_jobs_for_portal()
    return jsonify({"count": len(jobs), "jobs": jobs})


# ─────────────────────────────────────────────────────────
# API — Notifications
# ─────────────────────────────────────────────────────────


@app.route("/api/notify/test", methods=["POST"])
def api_notify_test():
    ok, msg = test_notification()
    return jsonify({"success": ok, "message": msg})


# ─────────────────────────────────────────────────────────
# API Key status
# ─────────────────────────────────────────────────────────


@app.route("/api/keys/status")
def api_keys_status():
    """Return live status of all Groq and Hunter API keys."""
    import time
    from core import groq_client, contact_extractor

    # ── Groq ──────────────────────────────────────────────
    groq_client._init_pool()
    now = time.time()
    groq_keys = []
    for i, entry in enumerate(groq_client._key_pool):
        exhausted_until = entry["exhausted_until"]
        if exhausted_until <= now:
            status = "ok"
            recovers_in = None
        else:
            status = "rate_limited"
            recovers_in = round(exhausted_until - now)
        groq_keys.append(
            {
                "index": i + 1,
                "key_hint": entry["key"][:8] + "…" + entry["key"][-4:],
                "status": status,
                "recovers_in": recovers_in,  # seconds, None if ok
            }
        )

    # ── Contact provider pools ─────────────────────────────
    def provider_status(base_name, keys):
        state = contact_extractor._KEY_STATE.get(base_name, {"dead": set()})
        dead = set(state.get("dead", set()))
        return [
            {
                "index": i + 1,
                "key_hint": k[:8] + "…" + k[-4:],
                "status": "exhausted" if k in dead else "ok",
            }
            for i, k in enumerate([key for key in keys if key])
        ]

    hunter_keys = provider_status("HUNTER_API_KEY", config.HUNTER_API_KEYS)
    prospeo_keys = provider_status("PROSPEO_API_KEY", config.PROSPEO_API_KEYS)
    reoon_keys = provider_status("REOON_API_KEY", config.REOON_API_KEYS)
    million_keys = provider_status(
        "MILLION_VERIFIER_API_KEY", config.MILLION_VERIFIER_API_KEYS
    )

    return jsonify(
        {
            "groq": {
                "total": len(groq_keys),
                "ok": sum(1 for k in groq_keys if k["status"] == "ok"),
                "rate_limited": sum(
                    1 for k in groq_keys if k["status"] == "rate_limited"
                ),
                "keys": groq_keys,
            },
            "hunter": {
                "total": len(hunter_keys),
                "ok": sum(1 for k in hunter_keys if k["status"] == "ok"),
                "exhausted": sum(1 for k in hunter_keys if k["status"] == "exhausted"),
                "keys": hunter_keys,
            },
            "prospeo": {"total": len(prospeo_keys), "keys": prospeo_keys},
            "reoon": {"total": len(reoon_keys), "keys": reoon_keys},
            "million_verifier": {"total": len(million_keys), "keys": million_keys},
        }
    )


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────


def _update_env_key(key: str, value: str):
    """Persist a single setting to the database."""
    _update_env_multi({key: value})


def _update_env_multi(updates: dict[str, str]):
    """
    Persist settings to the database (the UI-editable source of truth) and
    refresh the running config. Nothing is written to .env — a deployed user
    has no shell access to it, and Render wipes the code dir on redeploy.
    """
    from database import set_settings

    set_settings(updates)
    config.reload()


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
    Path(config.INPUT_DIR).mkdir(exist_ok=True)
    start_scheduler()
    app.run(
        host="0.0.0.0",
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        use_reloader=False,  # Disable reloader — it double-starts the scheduler
    )


# ─────────────────────────────────────────────────────────
# API — Portal backlog cleanup
# ─────────────────────────────────────────────────────────


@app.route("/api/portal/clear-login-backlog", methods=["POST"])
def api_portal_clear_login_backlog():
    """
    One-shot cleanup: mark all backlogged portal jobs that are stuck due
    to login walls as 'skipped' so they stop clogging the retry queue.
    """
    login_signals = [
        "portal requires login",
        "skip:login_required",
        "requires login",
        "login required",
    ]
    # All patterns are passed as parameters (never inlined) so this is safe
    # on both SQLite and Postgres — literal % in SQL would break psycopg2.
    signal_conditions = " OR ".join(["LOWER(portal_error) LIKE ?" for _ in login_signals])
    params = [f"%{s}%" for s in login_signals]
    params += ["%linkedin.com/jobs%", "%linkedin.com/authwall%"]

    try:
        with get_conn() as conn:
            result = conn.execute(
                f"""UPDATE jobs
                       SET portal_status = 'skipped',
                           portal_error  = 'SKIP:login_required — cleared by bulk cleanup'
                     WHERE portal_status IN ('pending', 'failed')
                       AND (
                             ({signal_conditions})
                             OR LOWER(application_url) LIKE ?
                             OR LOWER(application_url) LIKE ?
                           )""",
                params,
            )
            cleared = result.rowcount
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify(
        {
            "success": True,
            "cleared": cleared,
            "message": f"Marked {cleared} backlogged portal job(s) as skipped",
        }
    )
