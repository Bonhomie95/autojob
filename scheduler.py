"""
scheduler.py — Background scheduler for Job Hunter.

Uses APScheduler to fire the pipeline and follow-up cycle automatically
on the cron schedule defined in .env (SCHEDULE_CRON).

This module is started once when app.py boots. It checks
config.SCHEDULE_ENABLED on each tick so you can toggle it from
the Settings page without restarting.

Dependencies:
  pip install apscheduler

Cron examples (SCHEDULE_CRON in .env):
  "0 8 * * 1-5"   Mon–Fri at 08:00
  "0 9 * * *"     Every day at 09:00
  "0 8,17 * * *"  Daily at 08:00 and 17:00
  "*/30 * * * *"  Every 30 minutes (testing)
"""

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

_scheduler = None
_scheduler_lock = threading.Lock()


def _parse_cron(expr: str) -> dict:
    """Parse '0 8 * * 1-5' into APScheduler CronTrigger kwargs."""
    parts = expr.strip().split()
    if len(parts) != 5:
        logger.warning(f"[Scheduler] Invalid cron expression '{expr}' — using default 08:00 Mon-Fri")
        parts = ["0", "8", "*", "*", "1-5"]
    keys = ["minute", "hour", "day", "month", "day_of_week"]
    return dict(zip(keys, parts))


def _run_pipeline_job():
    """Called by the scheduler on each tick."""
    from config import config
    config.reload()

    if not config.SCHEDULE_ENABLED:
        return  # Toggled off without restart

    logger.info(f"[Scheduler] ⏰ Scheduled pipeline run starting at {datetime.now().strftime('%H:%M')}")

    try:
        from pipeline import run_pipeline
        from notifier import notify_run_complete, notify_run_error

        result = run_pipeline()
        notify_run_complete(result)
        logger.info(f"[Scheduler] ✅ Scheduled run complete: {result}")
    except Exception as e:
        logger.exception(f"[Scheduler] ❌ Scheduled run failed: {e}")
        try:
            from notifier import notify_run_error
            notify_run_error(str(e))
        except Exception:
            pass

    # Follow-up cycle runs ~5 min after the pipeline (offset to avoid overlap)
    if getattr(__import__('config').config, 'SCHEDULE_FOLLOWUP', True):
        import threading, time
        def _delayed_followup():
            time.sleep(300)
            try:
                from follow_up_scheduler import run_follow_up_cycle
                from notifier import notify_followup_complete
                summary = run_follow_up_cycle()
                notify_followup_complete(summary)
            except Exception as e:
                logger.warning(f"[Scheduler] Follow-up cycle error: {e}")
        threading.Thread(target=_delayed_followup, daemon=True).start()


def start_scheduler():
    """
    Start the APScheduler background scheduler.
    Safe to call multiple times — only starts once.
    """
    global _scheduler

    with _scheduler_lock:
        if _scheduler is not None:
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.warning(
                "[Scheduler] APScheduler not installed — scheduled runs disabled. "
                "Run: pip install apscheduler"
            )
            return

        from config import config

        _scheduler = BackgroundScheduler(timezone=config.TIMEZONE or "UTC")

        cron_kwargs = _parse_cron(config.SCHEDULE_CRON)
        _scheduler.add_job(
            _run_pipeline_job,
            trigger=CronTrigger(**cron_kwargs),
            id="pipeline_run",
            name="Scheduled Pipeline Run",
            replace_existing=True,
            misfire_grace_time=300,  # Allow up to 5 min late start
        )

        _scheduler.start()
        logger.info(
            f"[Scheduler] Started — cron: '{config.SCHEDULE_CRON}' "
            f"({'enabled' if config.SCHEDULE_ENABLED else 'disabled — set SCHEDULE_ENABLED=true'})"
        )


def stop_scheduler():
    global _scheduler
    with _scheduler_lock:
        if _scheduler:
            _scheduler.shutdown(wait=False)
            _scheduler = None


def get_next_run() -> str | None:
    """Return the next scheduled run time as an ISO string, or None."""
    if not _scheduler:
        return None
    job = _scheduler.get_job("pipeline_run")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
    return None


def scheduler_status() -> dict:
    from config import config
    return {
        "enabled":   getattr(config, "SCHEDULE_ENABLED", False),
        "cron":      getattr(config, "SCHEDULE_CRON", ""),
        "next_run":  get_next_run(),
        "running":   _scheduler is not None and _scheduler.running,
    }
