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
)
from settings_routes import settings_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.register_blueprint(settings_bp)

# ── Global run state ──────────────────────────────────────
_run_lock = threading.Lock()
_run_active = False
_progress_queue: queue.Queue = queue.Queue()


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

    # List output files if available
    output_files = []
    if job.get("output_dir") and Path(job["output_dir"]).exists():
        output_files = [
            f.name for f in Path(job["output_dir"]).iterdir()
            if f.is_file()
        ]
    return render_template("job_detail.html", job=job, output_files=output_files)


@app.route("/run", methods=["POST"])
def trigger_run():
    global _run_active
    with _run_lock:
        if _run_active:
            return jsonify({"error": "A run is already in progress"}), 409
        _run_active = True

    # Flush old messages
    while not _progress_queue.empty():
        try:
            _progress_queue.get_nowait()
        except queue.Empty:
            break

    def run_in_thread():
        global _run_active
        try:
            from pipeline import run_pipeline
            def progress_cb(msg):
                _progress_queue.put(msg)
            run_pipeline(progress_cb=progress_cb)
        except Exception as e:
            _progress_queue.put(f"❌ Fatal error: {e}")
            logger.exception("Pipeline error")
        finally:
            _progress_queue.put("__DONE__")
            with _run_lock:
                _run_active = False

    t = threading.Thread(target=run_in_thread, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/stream")
def stream():
    """SSE endpoint for live progress updates."""
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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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
    return jsonify({"active": _run_active})


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
    """Manually trigger sending the application email for a job."""
    from mailer import send_application, smtp_configured
    if not smtp_configured():
        return jsonify({"error": "SMTP not configured — configure it in Settings first"}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    output_dir = job.get("output_dir", "")
    if not output_dir:
        return jsonify({"error": "No output folder for this job — run the pipeline first"}), 400

    to_email = (
        job.get("hr_email")
        or job.get("application_email")
        or ""
    )

    # Allow override from POST body
    data = request.json or {}
    to_email = data.get("to_email") or to_email
    subject  = data.get("subject")
    body     = data.get("body")

    if not to_email:
        return jsonify({"error": "No recipient email address found for this job"}), 400

    success, message = send_application(to_email, output_dir, subject, body)
    if success:
        from datetime import datetime
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


@app.route("/api/smtp/test", methods=["POST"])
def test_smtp():
    """Test SMTP connection with current settings."""
    from mailer import test_smtp as _test
    ok, msg = _test()
    return jsonify({"success": ok, "message": msg})


@app.route("/api/smtp/send-test", methods=["POST"])
def send_test_email():
    """
    Send a real preview email to any address.
    Uses the most recent EMAIL_DRAFT.txt (real subject + body) with real attachments.
    No 'test' labels — exactly what a recruiter would receive.
    """
    from mailer import send_application, smtp_configured
    data = request.json or {}
    to_email = (data.get("to_email") or "").strip()

    if not to_email:
        return jsonify({"success": False, "message": "No recipient email provided"}), 400

    if not smtp_configured():
        return jsonify({"success": False,
                        "message": "SMTP not configured — set your password in Settings first"}), 400

    # Find most recent output folder with both a CV.pdf and EMAIL_DRAFT.txt
    output_root = Path(config.OUTPUT_DIR)
    best_folder = None
    if output_root.exists():
        folders = sorted(
            [d for d in output_root.iterdir()
             if d.is_dir() and (d / "CV.pdf").exists() and (d / "EMAIL_DRAFT.txt").exists()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if folders:
            best_folder = str(folders[0])

    if not best_folder:
        # Fallback: any folder with CV.pdf
        if output_root.exists():
            folders = sorted(
                [d for d in output_root.iterdir() if d.is_dir() and (d / "CV.pdf").exists()],
                key=lambda d: d.stat().st_mtime, reverse=True,
            )
            if folders:
                best_folder = str(folders[0])

    if not best_folder:
        return jsonify({
            "success": False,
            "message": "No generated documents found. Run the pipeline first.",
        }), 400

    folder_name = Path(best_folder).name

    # send_application reads EMAIL_DRAFT.txt automatically when subject/body are None
    ok, msg = send_application(to_email, best_folder)

    return jsonify({
        "success": ok,
        "message": msg,
        "folder_used": folder_name,
    })


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _list_cv_files() -> list[str]:
    input_dir = Path(config.INPUT_DIR)
    input_dir.mkdir(exist_ok=True)
    return [f.name for f in input_dir.iterdir()
            if f.suffix.lower() in (".docx", ".pdf", ".txt")]


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
    Path(config.INPUT_DIR).mkdir(exist_ok=True)
    app.run(
        host="0.0.0.0",
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )
