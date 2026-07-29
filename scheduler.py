"""
scheduler.py — Background scheduler for Auto Job.

Fixes over previous version:
  - Pipeline runs in its own thread — never blocks the scheduler heartbeat
  - misfire_grace_time raised to 1 hour — late starts are retried not skipped
  - Startup catch-up — if the scheduled time was missed while the app was
    down (within the last 2 hours), the pipeline fires immediately on boot
  - SCHEDULE_ENABLED checked at scheduling time, not inside the job
  - Proper logging at every decision point so missed runs are visible
  - Double-run guard — won't start a second pipeline if one is already running

Cron examples (SCHEDULE_CRON in .env):
  "0 8 * * 1-5"    Mon–Fri at 08:00
  "0 9 * * *"      Every day at 09:00
  "0 8,17 * * *"   Twice daily at 08:00 and 17:00
  "*/30 * * * *"   Every 30 minutes (for testing)
"""

import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_scheduler        = None
_scheduler_lock   = threading.Lock()
_pipeline_running = False
_pipeline_lock    = threading.Lock()


# ── Cron parser ───────────────────────────────────────────────

def _parse_cron(expr: str) -> dict:
    parts = expr.strip().split()
    if len(parts) != 5:
        logger.warning(
            f"[Scheduler] Invalid cron '{expr}' — falling back to 08:00 Mon-Fri"
        )
        parts = ["0", "8", "*", "*", "1-5"]
    return dict(zip(["minute", "hour", "day", "month", "day_of_week"], parts))


# ── Pipeline runner ───────────────────────────────────────────

def _run_pipeline_in_thread(reason: str = "scheduled"):
    """
    Fires the pipeline in a dedicated thread so the scheduler
    heartbeat is never blocked. Includes a double-run guard.
    """
    global _pipeline_running

    with _pipeline_lock:
        if _pipeline_running:
            logger.warning(
                f"[Scheduler] Skipping {reason} run — pipeline already in progress"
            )
            return
        _pipeline_running = True

    logger.info(
        f"[Scheduler] ⏰ Starting {reason} pipeline run at "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    def _run():
        global _pipeline_running
        try:
            from pipeline import run_pipeline
            from notifier import notify_run_complete, notify_run_error

            result = run_pipeline()
            notify_run_complete(result)
            logger.info(f"[Scheduler] ✅ {reason.capitalize()} run complete — {result}")

            # Follow-up cycle 5 min after pipeline finishes
            from config import config as _cfg
            if getattr(_cfg, "SCHEDULE_FOLLOWUP", True):
                import time
                logger.info("[Scheduler] Waiting 5 min before follow-up cycle…")
                time.sleep(300)
                try:
                    from follow_up_scheduler import run_follow_up_cycle
                    from notifier import notify_followup_complete
                    summary = run_follow_up_cycle()
                    notify_followup_complete(summary)
                    logger.info(f"[Scheduler] ✅ Follow-up cycle complete — {summary}")
                except Exception as e:
                    logger.warning(f"[Scheduler] Follow-up cycle error: {e}")

        except Exception as e:
            logger.exception(f"[Scheduler] ❌ {reason.capitalize()} run failed: {e}")
            try:
                from notifier import notify_run_error
                notify_run_error(str(e))
            except Exception:
                pass
        finally:
            with _pipeline_lock:
                _pipeline_running = False
            logger.info(f"[Scheduler] Pipeline thread exited ({reason})")

    t = threading.Thread(target=_run, name=f"pipeline-{reason}", daemon=True)
    t.start()


def _scheduled_job():
    """Called by APScheduler on each cron tick."""
    from config import config
    config.reload()

    if not config.SCHEDULE_ENABLED:
        logger.info("[Scheduler] Tick fired but SCHEDULE_ENABLED=false — skipping")
        return

    _run_pipeline_in_thread(reason="scheduled")


# ── Catch-up on startup ───────────────────────────────────────

def _check_catchup(cron_kwargs: dict, timezone: str):
    """
    If the scheduled time occurred within the last 2 hours while
    the app was offline, fire the pipeline immediately on startup.
    """
    try:
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.schedulers.background import BackgroundScheduler
        import pytz

        tz       = pytz.timezone(timezone) if timezone else pytz.utc
        now      = datetime.now(tz)
        two_h    = now - timedelta(hours=2)

        # Build a temporary trigger to find the previous fire time
        tmp      = BackgroundScheduler(timezone=tz)
        trigger  = CronTrigger(**cron_kwargs, timezone=tz)
        prev     = trigger.get_next_fire_time(None, two_h)

        if prev and two_h <= prev <= now:
            logger.info(
                f"[Scheduler] ⚡ Catch-up: scheduled time {prev.strftime('%H:%M')} "
                f"was missed while app was down — firing now"
            )
            _run_pipeline_in_thread(reason="catch-up")
        else:
            logger.info(
                f"[Scheduler] No missed run to catch up on "
                f"(last scheduled: {prev.strftime('%H:%M') if prev else 'unknown'})"
            )
    except Exception as e:
        logger.debug(f"[Scheduler] Catch-up check failed (non-fatal): {e}")


# ── Public API ────────────────────────────────────────────────

def start_scheduler():
    """
    Start the APScheduler background scheduler and apply the current schedule.
    Safe to call multiple times — only creates the scheduler once. The cron job
    itself is added/removed by _apply_schedule so the dashboard can turn
    automation on/off and change the time without a restart.
    """
    global _scheduler

    with _scheduler_lock:
        if _scheduler is None:
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
            except ImportError:
                logger.warning(
                    "[Scheduler] APScheduler not installed — auto-schedule disabled.\n"
                    "  Fix: pip install apscheduler"
                )
                return
            from config import config

            tz = config.TIMEZONE or "UTC"
            _scheduler = BackgroundScheduler(timezone=tz)
            _scheduler.start()

    _apply_schedule()


def _apply_schedule():
    """
    Read the current config and add / replace / remove the pipeline cron job so
    the live schedule matches what the dashboard shows. Called on startup and
    whenever schedule settings are saved.
    """
    global _scheduler
    if _scheduler is None:
        return

    try:
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        return

    from config import config

    with _scheduler_lock:
        existing = _scheduler.get_job("pipeline_run")

        if not config.SCHEDULE_ENABLED:
            if existing:
                _scheduler.remove_job("pipeline_run")
                logger.info("[Scheduler] Auto-run disabled — cron job removed.")
            else:
                logger.info("[Scheduler] Auto-run disabled — nothing scheduled.")
            return

        tz = config.TIMEZONE or "UTC"
        cron_kwargs = _parse_cron(config.SCHEDULE_CRON)
        _scheduler.add_job(
            _scheduled_job,
            trigger=CronTrigger(**cron_kwargs, timezone=tz),
            id="pipeline_run",
            name="Scheduled Pipeline Run",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )

    logger.info(
        f"[Scheduler] ✅ Auto-run every '{config.SCHEDULE_CRON}' "
        f"({config.TIMEZONE or 'UTC'}) | next run: {get_next_run()}"
    )


def reschedule():
    """Re-read settings and update the live cron job. Call after saving settings."""
    from config import config

    config.reload()
    if _scheduler is None:
        start_scheduler()
    else:
        _apply_schedule()


def stop_scheduler():
    global _scheduler
    with _scheduler_lock:
        if _scheduler:
            _scheduler.shutdown(wait=False)
            _scheduler = None
            logger.info("[Scheduler] Stopped")


def trigger_now():
    """Manually fire the pipeline outside of the cron schedule."""
    _run_pipeline_in_thread(reason="manual")


def is_pipeline_running() -> bool:
    """
    Shared check so the dashboard's /run endpoint and the cron/catch-up
    scheduler never start overlapping pipeline runs (concurrent runs corrupt
    each other's scraping/dedup and can stall contact extraction).
    """
    with _pipeline_lock:
        return _pipeline_running


def mark_pipeline_running(value: bool):
    """Let external callers (e.g. app.py's dashboard run) register that a
    pipeline is in flight, so the scheduler's own guard also sees it."""
    global _pipeline_running
    with _pipeline_lock:
        _pipeline_running = value


def get_next_run() -> str | None:
    if not _scheduler:
        return None
    job = _scheduler.get_job("pipeline_run")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
    return None


def scheduler_status() -> dict:
    from config import config
    return {
        "enabled":          getattr(config, "SCHEDULE_ENABLED", False),
        "cron":             getattr(config, "SCHEDULE_CRON", ""),
        "next_run":         get_next_run(),
        "running":          _scheduler is not None and _scheduler.running,
        "pipeline_active":  _pipeline_running,
    }
