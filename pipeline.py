import os
import logging
from pathlib import Path
from config import config
from database import (
    init_db, insert_job, update_job, make_job_id,
    get_pending_jobs, start_run, finish_run,
)
from core.scorer import score_job
from core.contact_extractor import extract_contacts
from core.document_generator import generate_documents, extract_cv_text
from scrapers import (
    LinkedInScraper, IndeedScraper,
    RemoteOKScraper, WeWorkRemotelyScraper,
    GoogleJobsScraper,
)

logger = logging.getLogger(__name__)


def _get_cv_text() -> str:
    """Find and extract the CV from the input folder."""
    input_dir = Path(config.INPUT_DIR)
    input_dir.mkdir(exist_ok=True)

    # Prefer DOCX, then PDF, then any .txt
    for ext in ("*.docx", "*.pdf", "*.txt"):
        matches = sorted(input_dir.glob(ext))
        if matches:
            logger.info(f"[Pipeline] Using CV: {matches[0]}")
            return extract_cv_text(str(matches[0]))

    logger.error("[Pipeline] No CV found in input/ folder.")
    return ""


def _get_scrapers() -> list:
    scrapers = []
    if config.SCRAPE_LINKEDIN:
        scrapers.append(LinkedInScraper())
    if config.SCRAPE_INDEED:
        scrapers.append(IndeedScraper())
    if config.SCRAPE_REMOTEOK:
        scrapers.append(RemoteOKScraper())
    if config.SCRAPE_WEWORKREMOTELY:
        scrapers.append(WeWorkRemotelyScraper())
    if config.SCRAPE_GOOGLE:
        scrapers.append(GoogleJobsScraper())
    return scrapers


def run_pipeline(progress_cb=None) -> dict:
    """
    Full pipeline: Scrape → Filter → Score → Extract Contacts → Generate Docs.

    progress_cb: optional callable(message: str) for real-time UI updates.
    """
    def emit(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    init_db()
    run_id = start_run()

    # Reload config so any Settings page changes are picked up
    config.reload()
    emit(f"⚙ Config loaded — SMTP auto-send: {'ON' if config.SMTP_AUTO_SEND else 'OFF'} | "
         f"Proxy: {'ON (' + str(len(config.PROXY_LIST)) + ' proxies)' if config.PROXY_ENABLED and config.PROXY_LIST else 'OFF'}")
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)

    cv_text = _get_cv_text()
    if not cv_text:
        finish_run(run_id, 0, 0, 0, status="failed")
        return {"error": "No CV found in input/ folder. Add your CV (DOCX/PDF/TXT) and retry."}

    # ── Step 1: Scrape ──────────────────────────────────────────────────────
    emit("🔍 Starting scrape across all job boards…")
    all_jobs = []
    scrapers = _get_scrapers()
    location = "Remote" if config.REMOTE_ONLY else ", ".join(config.TARGET_COUNTRIES)

    for scraper in scrapers:
        emit(f"  → Scraping {scraper.name}…")
        try:
            jobs = scraper.scrape(config.TARGET_ROLES, location)
            # Enforce max per board
            jobs = jobs[: config.MAX_JOBS_PER_BOARD]
            all_jobs.extend(jobs)
            emit(f"  ✓ {scraper.name}: {len(jobs)} jobs found")
        except Exception as e:
            emit(f"  ✗ {scraper.name} failed: {e}")

    emit(f"📋 Total scraped: {len(all_jobs)} jobs")

    # ── Step 2: Deduplicate & Store ─────────────────────────────────────────
    new_jobs = []
    for job in all_jobs:
        if not job.get("url"):
            continue
        if insert_job(job):
            new_jobs.append(job)

    emit(f"🆕 New jobs (not seen before): {len(new_jobs)}")
    jobs_found = len(new_jobs)

    # ── Step 3: Score ───────────────────────────────────────────────────────
    emit("🤖 Scoring jobs with Groq llama-3.3-70b-versatile…")
    qualified = []

    for i, job in enumerate(new_jobs, 1):
        emit(f"  Scoring {i}/{len(new_jobs)}: {job.get('title')} @ {job.get('company')}")
        try:
            score_data = score_job(cv_text, job, config.BLACKLIST_KEYWORDS)
            job_id = make_job_id(job["url"])
            score = score_data.get("score", 0)

            if score_data.get("is_blacklisted"):
                update_job(job_id, status="skipped",
                           score=0,
                           contact_notes=f"Blacklisted: {score_data.get('blacklist_reason')}")
                emit(f"    ⛔ Skipped (blacklisted)")
                continue

            update_job(job_id, score=score)

            if score >= config.MIN_MATCH_SCORE:
                job["_score_data"] = score_data
                job["_job_id"] = job_id
                qualified.append(job)
                emit(f"    ✅ Score: {score}/100 — qualified")
            else:
                update_job(job_id, status="skipped")
                emit(f"    ⚪ Score: {score}/100 — below threshold ({config.MIN_MATCH_SCORE})")
        except Exception as e:
            emit(f"    ⚠ Scoring error: {e}")

    emit(f"✅ Qualified jobs: {len(qualified)}/{len(new_jobs)}")
    jobs_scored = len(qualified)

    # ── Step 4: Extract HR Contacts ─────────────────────────────────────────
    emit("📞 Extracting HR contact information…")
    for job in qualified:
        try:
            contact = extract_contacts(job)
            job["_contact"] = contact
            job_id = job["_job_id"]
            update_job(
                job_id,
                hr_name=contact.get("hr_name", ""),
                hr_email=contact.get("hr_email", ""),
                hr_title=contact.get("hr_title", ""),
                application_email=contact.get("application_email", ""),
                application_url=contact.get("application_url", ""),
                contact_notes=contact.get("contact_notes", ""),
            )
            hr_found = contact.get("hr_name") or contact.get("hr_email")
            emit(f"  {'📬' if hr_found else '📭'} {job.get('company')}: {'Found HR info' if hr_found else 'No HR info found'}")
        except Exception as e:
            emit(f"  ⚠ Contact extraction error for {job.get('company')}: {e}")
            job["_contact"] = {}

    # ── Step 5: Generate Documents ──────────────────────────────────────────
    emit("📄 Generating customized CV + Cover Letters…")
    docs_generated = 0
    emails_sent = 0

    for job in qualified:
        try:
            contact = job.get("_contact", {})
            score_data = job.get("_score_data", {})

            # Check HR contact requirement
            has_hr = bool(
                contact.get("hr_email") or
                contact.get("hr_name") or
                contact.get("application_email") or
                contact.get("application_url")
            )
            if not has_hr and not config.GENERATE_DOCS_WITHOUT_HR:
                update_job(job["_job_id"], status="skipped",
                           contact_notes="Skipped: no HR contact found and GENERATE_DOCS_WITHOUT_HR=false")
                emit(f"  ⏭ Skipped (no HR contact): {job.get('company')}")
                continue
            success = generate_documents(job, cv_text, contact, score_data, str(output_dir))
            if success:
                docs_generated += 1
                job_id = job["_job_id"]
                from core.document_generator import _safe_dirname
                company_dir = _safe_dirname(job.get("company", "Company"))
                role_dir = _safe_dirname(job.get("title", "Role"))
                folder = str(output_dir / f"{company_dir}_{role_dir}")
                update_job(job_id, status="done", output_dir=folder)
                job["output_dir"] = folder
                job["_output_dir"] = folder
                emit(f"  ✓ Documents ready: {company_dir}_{role_dir}/")

                # ── Auto-send email if configured
                if config.SMTP_AUTO_SEND:
                    from mailer import send_for_job, smtp_configured
                    if smtp_configured():
                        emit(f"  📤 Auto-sending email for {job.get('company')}…")
                        sent = send_for_job(job, emit=emit)
                        if sent:
                            emails_sent += 1
                    else:
                        emit("  ⚠ Auto-send ON but SMTP password not set — configure in Settings → Email (SMTP)")
            else:
                emit(f"  ✗ Doc generation failed for {job.get('company')}")
        except Exception as e:
            emit(f"  ⚠ Doc gen error for {job.get('company')}: {e}")

    finish_run(run_id, jobs_found, jobs_scored, docs_generated, emails_sent)
    summary = f"\n🎉 Pipeline complete! {docs_generated} packages generated"
    if emails_sent:
        summary += f", {emails_sent} emails sent"
    emit(summary)

    return {
        "run_id": run_id,
        "jobs_found": jobs_found,
        "jobs_scored": jobs_scored,
        "docs_generated": docs_generated,
        "emails_sent": emails_sent,
    }
