"""
Background tasks — the tenant-aware pipeline.

``run_discovery_for_user`` is the multi-tenant successor to the legacy
``pipeline.run_pipeline``: it runs entirely for one user, using that user's
parsed CV, settings, and (in later integration) credentials, and writes every
row through the tenant-scoped repository. It reuses the proven, stateless engine
pieces — the job-board scrapers and the offline scorer — while keeping all state
per user.

Document generation and sending consume the same per-user ``RuntimeConfig`` and
are gated behind the user's explicit consent; they build on this discovery loop.
"""

from __future__ import annotations

import logging

from .celery_app import celery
from .services import cv_service, progress
from .services import repository as repo
from .services.runtime_config import build_runtime_config

logger = logging.getLogger(__name__)


class _RunCancelled(Exception):
    """Raised at a checkpoint when the user has asked this run to stop."""


def _check_cancelled(user_id: str, run_id: str) -> None:
    if repo.run_cancel_requested(user_id, run_id):
        raise _RunCancelled()


def _scrapers():
    """
    The ToS-clean scraper set (public APIs / RSS). LinkedIn and Indeed are
    intentionally excluded for a commercial product — see Phase 7.
    """
    from scrapers import (
        ArbeitnowScraper,
        HackerNewsScraper,
        JobicyScraper,
        RemoteOKScraper,
        RemotiveScraper,
        WeWorkRemotelyScraper,
    )

    return [
        RemoteOKScraper(), WeWorkRemotelyScraper(), JobicyScraper(),
        RemotiveScraper(), ArbeitnowScraper(), HackerNewsScraper(),
    ]


@celery.task(bind=True, name="autojob.run_discovery_for_user")
def run_discovery_for_user(self, user_id: str, run_id: str, max_per_board: int = 40) -> dict:
    """Celery entry point — thin wrapper around :func:`run_discovery`."""
    return run_discovery(user_id, run_id, max_per_board=max_per_board)


@celery.task(name="autojob.dispatch_scheduled_runs")
def dispatch_scheduled_runs() -> dict:
    """Celery beat entry point — thin wrapper around :func:`dispatch_scheduled`."""
    return dispatch_scheduled()


def dispatch_scheduled() -> dict:
    """
    Multi-tenant scheduler (Celery beat, hourly).

    Enqueues a discovery run for every user who has scheduling enabled and does
    not already have a run in progress. This replaces the legacy single-user
    APScheduler cron with a fan-out that respects tenant isolation.
    """
    enabled = repo.list_users_with_schedule_enabled()

    dispatched = 0
    for s in enabled:
        try:
            run_id = repo.start_run(s.user_id)
        except repo.RunAlreadyActive:
            continue
        run_discovery_for_user.delay(s.user_id, run_id)
        dispatched += 1

    logger.info("Scheduled dispatch enqueued %d run(s)", dispatched)
    return {"dispatched": dispatched}


@celery.task(name="autojob.run_followups_for_user")
def run_followups_for_user(user_id: str) -> dict:
    return run_followups(user_id)


def run_followups(user_id: str) -> dict:
    """
    Detect replies for a user and follow up with non-responders.

    Always releases the follow-up lock on exit, however this got here — a
    manual button click or the daily scheduled fan-out both claim it before
    calling this, and the lock exists precisely so those two paths (or two
    manual clicks) can never run this concurrently for the same user.
    """
    from .services import followup
    try:
        cfg = build_runtime_config(user_id)
        return followup.run_follow_up_cycle(cfg)
    finally:
        repo.release_followup_lock(user_id)


@celery.task(name="autojob.dispatch_followups")
def dispatch_followups() -> dict:
    return dispatch_followup_cycles()


def dispatch_followup_cycles() -> dict:
    """Daily fan-out: run the reply-detection + follow-up cycle for each user."""
    users = repo.list_users_with_followup_enabled()
    dispatched = 0
    for s in users:
        if not repo.claim_followup_lock(s.user_id):
            continue  # a manual check or a previous cycle is still running
        run_followups_for_user.delay(s.user_id)
        dispatched += 1
    logger.info("Follow-up dispatch enqueued %d user(s)", dispatched)
    return {"dispatched": dispatched}


# Celery beat schedule — hourly discovery dispatch + daily follow-up cycle.
celery.conf.beat_schedule = {
    "dispatch-scheduled-runs-hourly": {
        "task": "autojob.dispatch_scheduled_runs",
        "schedule": 3600.0,
    },
    "dispatch-followups-daily": {
        "task": "autojob.dispatch_followups",
        "schedule": 86400.0,
    },
}


def run_discovery(user_id: str, run_id: str, max_per_board: int = 40) -> dict:
    from core.discovery import derive_queries, describe
    from core.scorer import score_job

    def emit(msg: str) -> None:
        logger.info("[run %s] %s", run_id, msg)
        progress.publish(run_id, msg)

    cfg = build_runtime_config(user_id)
    found = scored = 0

    try:
        profile = cv_service.get_profile(user_id)
        if not profile:
            emit("⛔ No CV uploaded — add your CV first.")
            repo.finish_run(user_id, run_id, 0, 0, 0, status="failed")
            progress.publish_done(run_id)
            return {"error": "no_cv"}

        emit(f"👤 {profile.get('name') or 'Candidate'} — {describe(profile)}")
        queries = derive_queries(profile)
        if not queries:
            emit("⛔ Couldn't derive any roles to search from your CV.")
            repo.finish_run(user_id, run_id, 0, 0, 0, status="failed")
            progress.publish_done(run_id)
            return {"error": "no_queries"}

        location = "Remote" if cfg.remote_only else ", ".join(cfg.target_countries)
        emit(f"🔍 Searching for: {', '.join(queries[:4])}…")

        from core.contact_extractor import extract_contacts
        from core.document_generator import generate_documents
        from core.scorer import learn_from_jobs

        from .services import mailer
        from .services.engine_adapter import use_runtime

        docs = emails = 0
        # Everything that touches the engine runs as this tenant.
        with use_runtime(cfg, profile):
            all_jobs: list[dict] = []
            for scraper in _scrapers():
                _check_cancelled(user_id, run_id)
                try:
                    jobs = scraper.scrape(queries, location)[:max_per_board]
                    all_jobs.extend(jobs)
                    emit(f"  ✓ {scraper.name}: {len(jobs)} jobs")
                except Exception as exc:  # noqa: BLE001
                    emit(f"  ✗ {scraper.name} failed: {exc}")

            new_jobs = [j for j in all_jobs if j.get("url") and repo.insert_job(user_id, j)]
            found = len(new_jobs)
            emit(f"🆕 {found} new job(s) stored")

            # Sharpen IDF weighting from this batch — into the SaaS corpus.
            if all_jobs:
                try:
                    learn_from_jobs(all_jobs)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("corpus learn skipped: %s", exc)

            # Score — relaxed experience gate so jobs surface on CV fit.
            emit("🔑 Scoring against your profile…")
            qualified: list[tuple[dict, str, dict]] = []
            for job in new_jobs:
                _check_cancelled(user_id, run_id)
                try:
                    data = score_job(profile, job, cfg.blacklist_keywords,
                                     relaxed_experience=True)
                    jid = repo.make_job_id(user_id, job["url"])
                    reason = data.get("rejected_reason", "")
                    if reason:
                        repo.update_job(user_id, jid, status="skipped", score=0,
                                        contact_notes=reason)
                        continue
                    score = data.get("score", 0)
                    repo.update_job(user_id, jid, score=score)
                    if score >= cfg.min_match_score:
                        scored += 1
                        qualified.append((job, jid, data))
                        emit(f"  ✅ {score:3}/100  {job.get('title')} @ {job.get('company')}")
                    else:
                        repo.update_job(user_id, jid, status="skipped")
                except Exception as exc:  # noqa: BLE001
                    emit(f"  ⚠ scoring error: {exc}")

            # Contact first — only build documents for jobs we can actually reach.
            emit("📞 Finding a contact for each qualified job…")
            scratch_root = _scratch_root()
            try:
                for job, jid, data in qualified:
                    _check_cancelled(user_id, run_id)
                    try:
                        contact = extract_contacts(job)
                    except Exception as exc:  # noqa: BLE001
                        emit(f"  ⚠ contact lookup failed ({job.get('company')}): {exc}")
                        contact = {}
                    repo.update_job(
                        user_id, jid,
                        hr_name=contact.get("hr_name", ""),
                        hr_email=contact.get("hr_email", ""),
                        hr_title=contact.get("hr_title", ""),
                        application_email=contact.get("application_email", ""),
                        application_url=contact.get("application_url", ""),
                        contact_notes=contact.get("contact_notes", ""),
                    )
                    to_email = contact.get("hr_email") or contact.get("application_email")
                    if not to_email:
                        repo.update_job(user_id, jid, status="skipped",
                                        contact_notes="No contact found — no document generated")
                        emit(f"  📭 No contact for {job.get('company')} — skipped")
                        continue

                    # Contact found → generate the tailored package into scratch
                    # space (PDF conversion needs real files on disk), then
                    # persist the result into the storage abstraction — so
                    # downloads/sends work regardless of which process or
                    # instance later serves them, same as CVs already do.
                    try:
                        ok, detail = generate_documents(job, profile, contact, data, scratch_root)
                    except Exception as exc:  # noqa: BLE001
                        emit(f"  ⚠ document error ({job.get('company')}): {exc}")
                        continue
                    if not ok:
                        repo.update_job(user_id, jid, status="skipped", contact_notes=detail)
                        continue
                    docs += 1
                    output_key = _persist_output(user_id, jid, detail)
                    repo.update_job(user_id, jid, status="done", output_dir=output_key)
                    emit(f"  📄 {job.get('company')} — package ready ({to_email})")

                    # Send only with explicit consent + auto-send on.
                    if cfg.auto_send and cfg.has_sending_consent and mailer.smtp_ready(cfg):
                        stored = repo.get_job(user_id, jid)
                        result = mailer.send_application(cfg, stored, to_email)
                        if result.ok:
                            from datetime import UTC, datetime
                            repo.update_job(user_id, jid, email_status="sent",
                                            email_sent_at=datetime.now(UTC),
                                            email_message_id=result.message_id,
                                            email_error="", status="applied")
                            emails += 1
                            emit(f"  📤 {result.message}")
                        else:
                            repo.update_job(user_id, jid, email_status="failed",
                                            email_error=result.message)
                            emit(f"  ⚠ {result.message}")
            finally:
                import shutil
                shutil.rmtree(scratch_root, ignore_errors=True)

        emit(f"🎉 Done — {found} found, {scored} qualified, {docs} package(s), {emails} sent")
        repo.finish_run(user_id, run_id, found, scored, docs, emails=emails, status="done")
        progress.publish_done(run_id)
        return {"found": found, "qualified": scored, "docs": docs, "emails": emails}

    except _RunCancelled:
        emit(f"⏹ Stopped — {found} found, {scored} qualified, {docs} package(s) before stopping.")
        repo.finish_run(user_id, run_id, found, scored, docs, emails=emails, status="cancelled")
        progress.publish_done(run_id)
        return {"cancelled": True, "found": found, "qualified": scored, "docs": docs}

    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline failed for run %s", run_id)
        emit(f"❌ Run failed: {exc}")
        repo.finish_run(user_id, run_id, found, scored, 0, status="failed")
        progress.publish_done(run_id)
        raise


def _scratch_root() -> str:
    """
    Local scratch space for one run's document generation.

    PDF conversion (LibreOffice/docx2pdf) needs real files on disk, so
    generation can't write directly into the storage abstraction — this is
    a temp dir instead of a persistent one precisely because nothing here is
    meant to outlive the run; see _persist_output.
    """
    import tempfile

    return tempfile.mkdtemp(prefix="autojob-docgen-")


def _persist_output(user_id: str, job_id: str, local_folder: str) -> str:
    """
    Upload every file a locally-generated output folder produced into the
    storage abstraction, keyed by job id rather than local path — so
    downloads/sends work regardless of which process or instance later
    serves them (a real gap under S3 or a separate Celery worker; the local
    scratch copy is deleted once its run finishes regardless of backend).

    Returns the storage key prefix to save on the job as ``output_dir``.
    """
    from pathlib import Path

    from .services import storage

    prefix = f"output/{user_id}/{job_id}"
    store = storage.get_storage()
    for f in Path(local_folder).iterdir():
        if f.is_file():
            store.put(f"{prefix}/{f.name}", f.read_bytes())
    return prefix
