import logging
import os
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
    HackerNewsScraper,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def _get_cv_text(cv_filename: str = "") -> str:
    """
    Load CV text. Priority:
      1. cv_filename argument (passed from run_pipeline for per-run override)
      2. config.ACTIVE_CV (set in .env or via Settings page)
      3. First file found in input/ (original auto-pick behaviour)
    """
    input_dir = Path(config.INPUT_DIR)
    input_dir.mkdir(exist_ok=True)

    # Determine which file to use
    pin = cv_filename or getattr(config, "ACTIVE_CV", "")
    if pin:
        pinned = input_dir / pin
        if pinned.exists():
            logger.info(f"[Pipeline] Using pinned CV: {pinned.name}")
            return extract_cv_text(str(pinned))
        else:
            logger.warning(f"[Pipeline] Pinned CV '{pin}' not found — falling back to auto-pick")

    for ext in ("*.docx", "*.pdf", "*.txt"):
        matches = sorted(input_dir.glob(ext))
        if matches:
            logger.info(f"[Pipeline] Using CV: {matches[0].name}")
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
    if config.SCRAPE_HACKERNEWS:     scrapers.append(HackerNewsScraper())
    if config.SCRAPE_GOOGLE:         scrapers.append(GoogleJobsScraper())
    return scrapers


def _load_backlog() -> dict:
    """
    Load all unfinished work from previous runs:
      - pending_docs  : scored jobs that never got documents generated
      - unsent_emails : docs generated, has an email, but email never sent
      - failed_emails : email was attempted but failed
      - portal_retries: portal fill failed or never attempted (has apply URL, no email sent)
      - followup_due  : sent 6+ days ago, no reply, no follow-up yet
    """
    with get_conn() as conn:
        pending_docs = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'pending' AND score >= ?
               ORDER BY score DESC""",
            (config.MIN_MATCH_SCORE,),
        ).fetchall()
        unsent_emails = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'done'
                 AND output_dir IS NOT NULL AND output_dir != ''
                 AND email_status IN ('not_sent', 'no_contact')
                 AND (hr_email != '' OR application_email != '')
               ORDER BY score DESC""",
        ).fetchall()
        failed_emails = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'done' AND email_status = 'failed'
               ORDER BY created_at DESC""",
        ).fetchall()
        portal_retries = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'done'
                 AND output_dir IS NOT NULL AND output_dir != ''
                 AND portal_status IN ('pending', 'failed')
                 AND email_status NOT IN ('sent')
                 AND application_url IS NOT NULL AND application_url != ''
               ORDER BY score DESC""",
        ).fetchall()
    from database import get_jobs_needing_follow_up
    followup_due = get_jobs_needing_follow_up(
        follow_up_days=int(os.getenv("FOLLOW_UP_DAYS", "6"))
    )
    return {
        "pending_docs":   [dict(r) for r in pending_docs],
        "unsent_emails":  [dict(r) for r in unsent_emails],
        "failed_emails":  [dict(r) for r in failed_emails],
        "portal_retries": [dict(r) for r in portal_retries],
        "followup_due":   followup_due,
    }


# ──────────────────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────────────────

def run_pipeline(progress_cb=None, cv_filename: str = "") -> dict:
    def emit(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    init_db()
    run_id = start_run()
    # All counters initialised here so backlog phase can use them safely
    docs_generated    = 0
    emails_sent       = 0
    portals_submitted = 0
    jobs_found        = 0
    jobs_scored       = 0
    config.reload()

    emit(
        f"⚙ Config loaded — "
        f"SMTP: {'ON' if config.SMTP_AUTO_SEND else 'OFF'} | "
        f"Proxy: {'ON (' + str(len(config.PROXY_LIST)) + ')' if config.PROXY_ENABLED and config.PROXY_LIST else 'OFF'} | "
        f"HR-only docs: {'YES' if not config.GENERATE_DOCS_WITHOUT_HR else 'NO'} | "
        f"Company enrichment: {'ON' if str(getattr(config, 'ENRICH_COMPANY_DATA', 'true')).lower() == 'true' else 'OFF'}"
    )

    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)

    cv_text = _get_cv_text(cv_filename)
    if not cv_text:
        finish_run(run_id, 0, 0, 0, status="failed")
        return {"error": "No CV found in input/ folder."}

    # ─────────────────────────────────────────────────────────
    # Phase 1 — Clear the backlog before scraping anything new
    # ─────────────────────────────────────────────────────────
    backlog = _load_backlog()
    has_backlog = any(backlog[k] for k in backlog)

    if has_backlog:
        emit(
            f"📋 Backlog found — processing before scraping new jobs:\n"
            f"   • {len(backlog['pending_docs'])} job(s) need documents\n"
            f"   • {len(backlog['unsent_emails'])} unsent email(s)\n"
            f"   • {len(backlog['failed_emails'])} failed email(s) to retry\n"
            f"   • {len(backlog['portal_retries'])} portal fill(s) pending\n"
            f"   • {len(backlog['followup_due'])} follow-up(s) due"
        )

    # ── Backlog Step A: Generate missing documents ────────────
    if backlog["pending_docs"]:
        emit(f"📄 Generating {len(backlog['pending_docs'])} missing document package(s)…")
        for row in backlog["pending_docs"]:
            row["_job_id"]     = row["id"]
            row["_score_data"] = {
                "score": row.get("score", 0), "ats_keywords": [],
                "match_reasons": [], "gaps": [], "company_insight": "",
            }
            row["_contact"] = {
                "hr_name":           row.get("hr_name", ""),
                "hr_email":          row.get("hr_email", ""),
                "hr_title":          row.get("hr_title", ""),
                "application_email": row.get("application_email", ""),
                "application_url":   row.get("application_url", ""),
            }
            try:
                # Re-extract contacts if they were empty from the last run
                contact = row["_contact"]
                has_contact = bool(
                    contact.get("hr_email") or contact.get("application_email") or
                    contact.get("application_url")
                )
                if not has_contact:
                    emit(f"  🔍 Re-extracting contacts for {row.get('company')}…")
                    try:
                        fresh = extract_contacts(row)
                        if fresh:
                            contact = fresh
                            row["_contact"] = fresh
                            update_job(
                                row["id"],
                                hr_name=fresh.get("hr_name", ""),
                                hr_email=fresh.get("hr_email", ""),
                                hr_title=fresh.get("hr_title", ""),
                                application_email=fresh.get("application_email", ""),
                                application_url=fresh.get("application_url", ""),
                                contact_notes=fresh.get("contact_notes", ""),
                            )
                    except Exception as ce:
                        emit(f"  ⚠ Contact re-extraction failed: {ce}")

                success = generate_documents(
                    row, cv_text, contact, row["_score_data"], str(output_dir)
                )
                if success:
                    docs_generated += 1
                    folder = str(output_dir / f"{_safe_dirname(row.get('company','Company'))}_{_safe_dirname(row.get('title','Role'))}")
                    update_job(row["id"], status="done", output_dir=folder)
                    row["output_dir"] = folder
                    emit(f"  ✓ {row.get('company')} — {row.get('title')}")
                else:
                    emit(f"  ✗ Failed: {row.get('company')}")
            except Exception as e:
                emit(f"  ⚠ Error ({row.get('company')}): {e}")

    # ── Backlog Step B: Send unsent emails ───────────────────
    if backlog["unsent_emails"] and config.SMTP_AUTO_SEND:
        emit(f"📧 Sending {len(backlog['unsent_emails'])} previously unsent email(s)…")
        from mailer import group_jobs_by_contact, send_grouped_application
        groups = group_jobs_by_contact(backlog["unsent_emails"])
        for group in groups:
            sent, _ = send_grouped_application(group, emit=emit)
            emails_sent += sent

    # ── Backlog Step C: Retry failed emails ──────────────────
    if backlog["failed_emails"] and config.SMTP_AUTO_SEND:
        emit(f"🔁 Retrying {len(backlog['failed_emails'])} failed email(s)…")
        from mailer import send_application, smtp_configured
        if smtp_configured():
            for job in backlog["failed_emails"]:
                to_email       = job.get("hr_email") or job.get("application_email") or ""
                output_dir_job = job.get("output_dir", "")
                if not to_email or not output_dir_job:
                    continue
                emit(f"  📧 Retry → {job.get('company')} ({to_email})")
                ok, msg = send_application(to_email, output_dir_job)
                if ok:
                    update_job(job["id"], email_status="sent",
                               email_sent_at=datetime.utcnow().isoformat(),
                               email_error="", status="applied")
                    emails_sent += 1
                    emit(f"  ✅ {msg}")
                else:
                    update_job(job["id"], email_status="failed", email_error=msg)
                    emit(f"  ❌ {msg}")

    # ── Backlog Step D: Portal retries ───────────────────────
    portal_enabled = str(getattr(config, "PORTAL_ENABLED", "false")).lower() == "true"
    if backlog["portal_retries"] and portal_enabled:
        emit(f"🌐 Retrying {len(backlog['portal_retries'])} portal fill(s)…")
        from portal_filler import fill_portal
        for job in backlog["portal_retries"]:
            job_id = job.get("id", "")
            ok, msg = fill_portal(job, emit=emit)
            if ok:
                update_job(job_id, portal_status="submitted",
                           portal_submitted_at=datetime.utcnow().isoformat(),
                           portal_error="", status="applied")
                portals_submitted += 1
            else:
                update_job(job_id, portal_status="failed", portal_error=msg)
                emit(f"  ⚠ Portal failed ({job.get('company')}): {msg}")

    # ── Backlog Step E: Follow-ups due ───────────────────────
    if backlog["followup_due"] and getattr(config, "FOLLOW_UP_ENABLED", True):
        emit(f"📨 Sending {len(backlog['followup_due'])} overdue follow-up(s)…")
        from follow_up_scheduler import detect_replies, send_follow_up
        detect_replies(emit=emit)   # check for replies first so we don't follow up replied jobs
        for job in backlog["followup_due"]:
            if not job.get("reply_detected"):
                send_follow_up(job, emit=emit)

    if has_backlog:
        emit("✅ Backlog cleared — now scraping for new jobs…")

    # ── Step 1: Scrape ──────────────────────────────────────
    emit("🔍 Scraping job boards…")
    all_jobs: list[dict] = []
    location = "Remote" if config.REMOTE_ONLY else ", ".join(config.TARGET_COUNTRIES)

    for scraper in _get_scrapers():
        emit(f"  → {scraper.name}…")
        try:
            jobs = scraper.scrape(config.TARGET_ROLES, location)[:config.MAX_JOBS_PER_BOARD]
            all_jobs.extend(jobs)
            emit(f"  ✓ {scraper.name}: {len(jobs)} jobs")
        except Exception as e:
            emit(f"  ✗ {scraper.name} failed: {e}")

    emit(f"📋 Total scraped: {len(all_jobs)}")

    # ── Step 2: Deduplicate & Store ─────────────────────────
    new_jobs = [j for j in all_jobs if j.get("url") and insert_job(j)]
    emit(f"🆕 New (not seen before): {len(new_jobs)}")
    jobs_found = len(new_jobs)

    # ── Step 3: Score new jobs ──────────────────────────────
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
                job["_job_id"]     = job_id
                qualified.append(job)
                insight = score_data.get("company_insight", "")
                emit(f"    ✅ {score}/100 — qualified" + (f" | {insight[:60]}…" if insight else ""))
            else:
                update_job(job_id, status="skipped")
                emit(f"    ⚪ {score}/100 — below {config.MIN_MATCH_SCORE}")
        except Exception as e:
            emit(f"    ⚠ Scoring error: {e}")

    jobs_scored = len(qualified)

    emit(f"✅ New qualified jobs to process: {len(qualified)}")

    # ── Step 4: Extract HR Contacts ─────────────────────────
    emit("📞 Extracting HR contacts…")
    for job in qualified:
        if job.get("_contact"):
            continue
        try:
            contact = extract_contacts(job)
            scraper_email = job.get("application_email") or job.get("hr_email")
            if scraper_email and not (contact.get("hr_email") or contact.get("application_email")):
                contact["application_email"] = scraper_email
                contact["contact_notes"]     = contact.get("contact_notes") or "Email from job source"
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

    # ── Step 5: Generate Documents ──────────────────────────
    emit("📄 Generating documents…")
    for job in qualified:
        try:
            contact    = job.get("_contact", {})
            score_data = job.get("_score_data", {})
            job_id     = job.get("_job_id") or job.get("id", "")

            if not config.GENERATE_DOCS_WITHOUT_HR:
                has_any_contact = bool(
                    contact.get("hr_email") or contact.get("application_email") or
                    contact.get("application_url") or job.get("url")
                )
                if not has_any_contact:
                    update_job(job_id, status="skipped",
                               contact_notes="No contact info at all — skipped")
                    emit(f"  ⏭ No contact at all — skipping {job.get('company')}")
                    continue

            if job.get("output_dir") and Path(job["output_dir"]).exists():
                emit(f"  ↩ Already generated: {Path(job['output_dir']).name}")
                if config.SMTP_AUTO_SEND and job.get("email_status") in ("failed", "no_contact", None, ""):
                    if _try_send(job, contact, emit):
                        emails_sent += 1
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
            else:
                emit(f"  ✗ Doc generation failed for {job.get('company')}")
        except Exception as e:
            emit(f"  ⚠ Error for {job.get('company')}: {e}")

    # ── Step 6: Grouped email sends ─────────────────────────
    if config.SMTP_AUTO_SEND:
        # Only send for jobs that have output dirs and no email sent yet
        to_send = [
            j for j in qualified
            if j.get("output_dir") and Path(j.get("output_dir", "")).exists()
            and j.get("email_status") not in ("sent", "no_contact")
        ]

        if to_send:
            emit(f"📧 Sending {len(to_send)} application(s) (grouped by contact)…")
            from mailer import group_jobs_by_contact, send_grouped_application
            groups = group_jobs_by_contact(to_send)
            emit(f"  → {len(groups)} unique contact(s) / group(s)")
            for group in groups:
                sent, skipped = send_grouped_application(group, emit=emit)
                emails_sent += sent

    # ── Step 7: Portal auto-fill (apply-URL-only jobs) ────────
    portal_enabled = str(getattr(config, "PORTAL_ENABLED", "false")).lower() == "true"
    if portal_enabled:
        from database import get_jobs_for_portal
        from portal_filler import fill_portal
        portal_jobs = get_jobs_for_portal()
        if portal_jobs:
            emit(f"🌐 Portal auto-fill: {len(portal_jobs)} job(s) with apply URL but no email…")
            for job in portal_jobs:
                job_id = job.get("id", "")
                ok, msg = fill_portal(job, emit=emit)
                if ok:
                    update_job(
                        job_id,
                        portal_status="submitted",
                        portal_submitted_at=datetime.utcnow().isoformat(),
                        portal_error="",
                        status="applied",
                    )
                    portals_submitted += 1
                else:
                    update_job(job_id, portal_status="failed", portal_error=msg)
                    emit(f"  ⚠ Portal failed ({job.get('company')}): {msg}")

    finish_run(run_id, jobs_found, jobs_scored, docs_generated, emails_sent)
    summary = f"\n🎉 Done! {docs_generated} packages generated"
    if emails_sent:
        summary += f", {emails_sent} emails sent"
    if portals_submitted:
        summary += f", {portals_submitted} portal(s) submitted"
    if has_backlog:
        summary += f" (backlog cleared)"
    emit(summary)

    result = {
        "run_id":            run_id,
        "jobs_found":        jobs_found,
        "jobs_scored":       jobs_scored,
        "docs_generated":    docs_generated,
        "emails_sent":       emails_sent,
        "portals_submitted": portals_submitted,
    }

    try:
        from notifier import notify_run_complete
        notify_run_complete(result)
    except Exception:
        pass

    return result


def _try_send(job: dict, contact: dict, emit) -> bool:
    from mailer import send_for_job, smtp_configured
    if not smtp_configured():
        emit("  ⚠ Auto-send ON but SMTP password not set")
        return False
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
