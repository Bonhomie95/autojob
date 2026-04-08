import logging
from pathlib import Path
from config import config
from database import (
    init_db, get_conn, insert_job, update_job, make_job_id,
    start_run, finish_run,
)
from core.scorer import score_job
from core.contact_extractor import extract_contacts
from core.document_generator import generate_documents, extract_cv_text, _safe_dirname
from scrapers import (
    LinkedInScraper, IndeedScraper,
    RemoteOKScraper, WeWorkRemotelyScraper,
    GoogleJobsScraper, JobicyScraper,
    RemotiveScraper, ArbeitnowScraper,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def _get_cv_text() -> str:
    input_dir = Path(config.INPUT_DIR)
    input_dir.mkdir(exist_ok=True)
    for ext in ("*.docx", "*.pdf", "*.txt"):
        matches = sorted(input_dir.glob(ext))
        if matches:
            logger.info(f"[Pipeline] Using CV: {matches[0]}")
            return extract_cv_text(str(matches[0]))
    logger.error("[Pipeline] No CV found in input/ folder.")
    return ""


def _get_scrapers() -> list:
    scrapers = []
    if config.SCRAPE_LINKEDIN:       scrapers.append(LinkedInScraper())
    if config.SCRAPE_INDEED:         scrapers.append(IndeedScraper())
    if config.SCRAPE_REMOTEOK:       scrapers.append(RemoteOKScraper())
    if config.SCRAPE_WEWORKREMOTELY: scrapers.append(WeWorkRemotelyScraper())
    if config.SCRAPE_JOBICY:         scrapers.append(JobicyScraper())
    if config.SCRAPE_REMOTIVE:       scrapers.append(RemotiveScraper())
    if config.SCRAPE_ARBEITNOW:      scrapers.append(ArbeitnowScraper())
    if config.SCRAPE_GOOGLE:         scrapers.append(GoogleJobsScraper())
    return scrapers


def _has_sendable_email(contact: dict, job: dict) -> bool:
    """Return True only if we have an actual email address to send to."""
    return bool(
        contact.get("hr_email") or
        contact.get("application_email") or
        job.get("hr_email") or
        job.get("application_email")
    )


def _load_incomplete_jobs() -> list[dict]:
    """
    Resume support: find jobs from the DB that were interrupted mid-pipeline.
    Returns jobs needing docs generated or emails re-sent.
    """
    with get_conn() as conn:
        # Jobs scored/qualified but docs never generated (status still 'pending' with score)
        pending_docs = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'pending' AND score >= ?
               ORDER BY score DESC""",
            (config.MIN_MATCH_SCORE,),
        ).fetchall()

        # Jobs with docs done but email failed — retry
        failed_email = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'done' AND email_status = 'failed'
               ORDER BY created_at DESC""",
        ).fetchall()

    return [dict(r) for r in pending_docs], [dict(r) for r in failed_email]


# ──────────────────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────────────────

def run_pipeline(progress_cb=None) -> dict:
    def emit(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    init_db()
    run_id = start_run()
    config.reload()

    emit(
        f"⚙ Config loaded — "
        f"SMTP: {'ON' if config.SMTP_AUTO_SEND else 'OFF'} | "
        f"Proxy: {'ON (' + str(len(config.PROXY_LIST)) + ')' if config.PROXY_ENABLED and config.PROXY_LIST else 'OFF'} | "
        f"HR-only docs: {'YES' if not config.GENERATE_DOCS_WITHOUT_HR else 'NO'}"
    )

    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)

    cv_text = _get_cv_text()
    if not cv_text:
        finish_run(run_id, 0, 0, 0, status="failed")
        return {"error": "No CV found in input/ folder."}

    # ── Resume: pick up interrupted jobs ───────────────────────────────────
    resume_docs, resume_emails = _load_incomplete_jobs()
    if resume_docs or resume_emails:
        emit(f"🔄 Resuming: {len(resume_docs)} jobs need docs, "
             f"{len(resume_emails)} failed emails to retry")

    # ── Step 1: Scrape ──────────────────────────────────────────────────────
    emit("🔍 Scraping job boards…")
    all_jobs: list[dict] = []
    location = "Remote" if config.REMOTE_ONLY else ", ".join(config.TARGET_COUNTRIES)

    for scraper in _get_scrapers():
        emit(f"  → {scraper.name}…")
        try:
            jobs = scraper.scrape(config.TARGET_ROLES, location)[: config.MAX_JOBS_PER_BOARD]
            all_jobs.extend(jobs)
            emit(f"  ✓ {scraper.name}: {len(jobs)} jobs")
        except Exception as e:
            emit(f"  ✗ {scraper.name} failed: {e}")

    emit(f"📋 Total scraped: {len(all_jobs)}")

    # ── Step 2: Deduplicate & Store ─────────────────────────────────────────
    new_jobs = [j for j in all_jobs if j.get("url") and insert_job(j)]
    emit(f"🆕 New (not seen before): {len(new_jobs)}")
    jobs_found = len(new_jobs)

    # ── Step 3: Score new jobs ──────────────────────────────────────────────
    emit("🤖 Scoring with Groq…")
    qualified: list[dict] = []

    for i, job in enumerate(new_jobs, 1):
        emit(f"  Scoring {i}/{len(new_jobs)}: {job.get('title')} @ {job.get('company')}")
        try:
            score_data = score_job(cv_text, job, config.BLACKLIST_KEYWORDS)
            job_id = make_job_id(job["url"])
            score  = score_data.get("score", 0)

            if score_data.get("is_blacklisted"):
                update_job(job_id, status="skipped", score=0,
                           contact_notes=f"Blacklisted: {score_data.get('blacklist_reason')}")
                emit(f"    ⛔ Blacklisted")
                continue

            update_job(job_id, score=score)

            if score >= config.MIN_MATCH_SCORE:
                job["_score_data"] = score_data
                job["_job_id"] = job_id
                qualified.append(job)
                emit(f"    ✅ {score}/100 — qualified")
            else:
                update_job(job_id, status="skipped")
                emit(f"    ⚪ {score}/100 — below {config.MIN_MATCH_SCORE}")
        except Exception as e:
            emit(f"    ⚠ Scoring error: {e}")

    jobs_scored = len(qualified)

    # Add resumed pending-doc jobs to qualified
    for row in resume_docs:
        row["_job_id"] = row["id"]
        row["_score_data"] = {"score": row.get("score", 0), "ats_keywords": [], "match_reasons": [], "gaps": []}
        row["_contact"] = {
            "hr_name":          row.get("hr_name", ""),
            "hr_email":         row.get("hr_email", ""),
            "hr_title":         row.get("hr_title", ""),
            "application_email":row.get("application_email", ""),
            "application_url":  row.get("application_url", ""),
        }
        qualified.append(row)

    emit(f"✅ Total to process: {len(qualified)} jobs")

    # ── Step 4: Extract HR Contacts ─────────────────────────────────────────
    emit("📞 Extracting HR contacts…")
    for job in qualified:
        # Skip if already extracted (resumed jobs have it from DB)
        if job.get("_contact"):
            continue
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
            has_email = contact.get("hr_email") or contact.get("application_email")
            emit(f"  {'📬' if has_email else '📭'} {job.get('company')}: "
                 f"{'email found' if has_email else 'no email found'}")
        except Exception as e:
            emit(f"  ⚠ Contact error for {job.get('company')}: {e}")
            job["_contact"] = {}

    # ── Step 5: Generate Documents ──────────────────────────────────────────
    emit("📄 Generating documents…")
    docs_generated = 0
    emails_sent    = 0

    for job in qualified:
        try:
            contact    = job.get("_contact", {})
            score_data = job.get("_score_data", {})
            job_id     = job.get("_job_id") or job.get("id", "")

            # ── Contact gate ─────────────────────────────────────────────
            # When GENERATE_DOCS_WITHOUT_HR=false:
            #   - application_url = sufficient to generate docs (you apply via link)
            #   - actual email (hr_email / application_email) = needed for auto-send
            # This way docs are generated for all reachable jobs, auto-send only
            # fires when we have a real email address.
            if not config.GENERATE_DOCS_WITHOUT_HR:
                has_any_contact = bool(
                    contact.get("hr_email") or
                    contact.get("application_email") or
                    contact.get("application_url") or
                    job.get("url")   # the job listing URL itself is always a fallback
                )
                if not has_any_contact:
                    update_job(job_id, status="skipped",
                               contact_notes="No contact info at all — skipped")
                    emit(f"  ⏭ No contact at all — skipping {job.get('company')}")
                    continue

            # Skip if docs already exist (resumed job already had them)
            if job.get("output_dir") and Path(job["output_dir"]).exists():
                emit(f"  ↩ Already generated: {Path(job['output_dir']).name}")
                # Jump straight to email send if needed
                if config.SMTP_AUTO_SEND and job.get("email_status") in ("failed", "no_contact", None, ""):
                    _try_send(job, contact, emit)
                continue

            success = generate_documents(job, cv_text, contact, score_data, str(output_dir))
            if success:
                docs_generated += 1
                company_dir = _safe_dirname(job.get("company", "Company"))
                role_dir    = _safe_dirname(job.get("title", "Role"))
                folder      = str(output_dir / f"{company_dir}_{role_dir}")
                update_job(job_id, status="done", output_dir=folder)
                job["output_dir"]  = folder
                job["_output_dir"] = folder
                emit(f"  ✓ {company_dir}_{role_dir}/")

                if config.SMTP_AUTO_SEND:
                    sent = _try_send(job, contact, emit)
                    if sent:
                        emails_sent += 1
            else:
                emit(f"  ✗ Doc generation failed for {job.get('company')}")
        except Exception as e:
            emit(f"  ⚠ Error for {job.get('company')}: {e}")

    # ── Resume: retry failed emails ─────────────────────────────────────────
    if resume_emails and config.SMTP_AUTO_SEND:
        emit(f"🔁 Retrying {len(resume_emails)} previously failed emails…")
        from mailer import send_application, smtp_configured
        if smtp_configured():
            for job in resume_emails:
                to_email = job.get("hr_email") or job.get("application_email") or ""
                output_dir_job = job.get("output_dir", "")
                if not to_email or not output_dir_job:
                    continue
                emit(f"  📧 Retry → {job.get('company')} ({to_email})")
                ok, msg = send_application(to_email, output_dir_job)
                if ok:
                    from datetime import datetime
                    update_job(job["id"], email_status="sent",
                               email_sent_at=datetime.utcnow().isoformat(),
                               email_error="", status="applied")
                    emails_sent += 1
                    emit(f"  ✅ Retry success: {msg}")
                else:
                    update_job(job["id"], email_status="failed", email_error=msg)
                    emit(f"  ❌ Retry failed: {msg}")

    finish_run(run_id, jobs_found, jobs_scored, docs_generated, emails_sent)
    summary = f"\n🎉 Done! {docs_generated} packages generated"
    if emails_sent:
        summary += f", {emails_sent} emails sent"
    if resume_docs or resume_emails:
        summary += f" (including resumed jobs)"
    emit(summary)

    return {
        "run_id": run_id, "jobs_found": jobs_found,
        "jobs_scored": jobs_scored, "docs_generated": docs_generated,
        "emails_sent": emails_sent,
    }


def _try_send(job: dict, contact: dict, emit) -> bool:
    """Attempt auto-send. Only fires when a real email address exists."""
    from mailer import send_for_job, smtp_configured
    if not smtp_configured():
        emit("  ⚠ Auto-send ON but SMTP password not set")
        return False
    # Require an actual email — application URL alone is not enough to auto-send
    has_email = bool(
        contact.get("hr_email") or contact.get("application_email") or
        job.get("hr_email") or job.get("application_email")
    )
    if not has_email:
        emit(f"  📭 No email for {job.get('company')} — docs ready but skipping auto-send")
        return False
    job["_contact"] = contact
    emit(f"  📤 Sending to {job.get('company')}…")
    return send_for_job(job, emit=emit)
