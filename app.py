import os
import json
import queue
import threading
import logging
from pathlib import Path
from datetime import datetime
from flask import (
    Flask, render_template, jsonify, request,
    redirect, url_for, send_from_directory, Response, stream_with_context,
)
from werkzeug.utils import secure_filename
from config import config
from database import (
    init_db, get_all_jobs, get_job, get_recent_runs, get_stats, update_job,
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
app.secret_key = os.urandom(24)
app.register_blueprint(settings_bp)


# ─────────────────────────────────────────────────────────
# JSON error handlers — prevent Flask returning HTML error
# pages to fetch() calls that expect JSON
# ─────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found", "detail": str(e)}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed", "detail": str(e)}), 405


@app.errorhandler(500)
def internal_error(e):
    logger.exception("Unhandled 500 error")
    return jsonify({"error": "Internal server error", "detail": str(e)}), 500


@app.errorhandler(Exception)
def unhandled_exception(e):
    logger.exception("Unhandled exception")
    return jsonify({"error": "Unexpected error", "detail": str(e)}), 500


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
    stats    = get_stats()
    runs     = get_recent_runs(5)
    jobs     = get_all_jobs(20)
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


@app.route("/jobs")
def jobs_page():
    status   = request.args.get("status", "all")
    source   = request.args.get("source", "all")
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
        output_files = [f.name for f in Path(job["output_dir"]).iterdir() if f.is_file()]
    return render_template("job_detail.html", job=job, output_files=output_files)


@app.route("/run", methods=["POST"])
def trigger_run():
    global _run_active
    with _run_lock:
        if _run_active:
            return jsonify({"error": "A run is already in progress"}), 409
        _run_active = True

    while not _progress_queue.empty():
        try:
            _progress_queue.get_nowait()
        except queue.Empty:
            break

    data = request.get_json(silent=True, force=True) or {}
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
    data = request.get_json(silent=True, force=True) or {}
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

    data     = request.get_json(silent=True, force=True) or {}
    to_email = data.get("to_email") or job.get("hr_email") or job.get("application_email") or ""
    subject  = data.get("subject")
    body     = data.get("body")

    if not to_email:
        return jsonify({"error": "No recipient email address found"}), 400

    # Allow override of dedup check when manually sending
    skip_dedup = data.get("force", False)
    success, message = send_application(to_email, output_dir, subject, body,
                                        skip_dedup_check=skip_dedup)
    if success:
        update_job(job_id, email_status="sent",
                   email_sent_at=datetime.utcnow().isoformat(),
                   email_error="", status="applied")
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
    return jsonify({"success": ok, "message": msg})


@app.route("/api/smtp/send-test", methods=["POST"])
def send_test_email():
    from mailer import send_application, smtp_configured
    data     = request.get_json(silent=True, force=True) or {}
    to_email = (data.get("to_email") or "").strip()

    if not to_email:
        return jsonify({"success": False, "message": "No recipient email provided"}), 400
    if not smtp_configured():
        return jsonify({"success": False, "message": "SMTP not configured"}), 400

    output_root = Path(config.OUTPUT_DIR)
    best_folder = None
    if output_root.exists():
        folders = sorted(
            [d for d in output_root.iterdir()
             if d.is_dir() and (d / "CV.pdf").exists() and (d / "EMAIL_DRAFT.txt").exists()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        if folders:
            best_folder = str(folders[0])

    if not best_folder and output_root.exists():
        folders = sorted(
            [d for d in output_root.iterdir() if d.is_dir() and (d / "CV.pdf").exists()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        if folders:
            best_folder = str(folders[0])

    if not best_folder:
        return jsonify({"success": False,
                        "message": "No generated documents found. Run the pipeline first."}), 400

    ok, msg = send_application(to_email, best_folder, skip_dedup_check=True)
    return jsonify({"success": ok, "message": msg, "folder_used": Path(best_folder).name})


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
    return [f.name for f in input_dir.iterdir()
            if f.suffix.lower() in (".docx", ".pdf", ".txt")]


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
            "size_kb":  round(f.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "active":   f.name == (getattr(config, "ACTIVE_CV", "") or ""),
        }
        for f in sorted(input_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
        if f.suffix.lower() in (".docx", ".pdf", ".txt")
    ]
    return jsonify({"files": files, "active": getattr(config, "ACTIVE_CV", "")})


@app.route("/api/cv/set-active", methods=["POST"])
def api_cv_set_active():
    """Pin a CV file as the active version for future runs."""
    data     = request.get_json(silent=True, force=True) or {}
    filename = data.get("filename", "").strip()
    if filename and not (Path(config.INPUT_DIR) / filename).exists():
        return jsonify({"error": f"File not found: {filename}"}), 404
    # Write to .env
    _update_env_key("ACTIVE_CV", filename)
    config.reload()
    return jsonify({"status": "ok", "active": filename or "(auto)"})


@app.route("/api/cv/delete", methods=["POST"])
def api_cv_delete():
    data     = request.get_json(silent=True, force=True) or {}
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
# API — Notifications
# ─────────────────────────────────────────────────────────

@app.route("/api/notify/test", methods=["POST"])
def api_notify_test():
    ok, msg = test_notification()
    return jsonify({"success": ok, "message": msg})


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _update_env_key(key: str, value: str):
    """Update or add a key=value line in .env file."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n")
        return
    lines = env_path.read_text().splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n")


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
        use_reloader=False,   # Disable reloader — it double-starts the scheduler
    )
