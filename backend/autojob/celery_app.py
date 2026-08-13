"""
Celery integration with the Flask application factory.

Every task runs inside a Flask app context (so ``current_app``, the DB session,
and config are all available), and the Celery config is sourced from the Flask
config. This replaces the legacy ``threading.Thread`` + in-memory ``queue``
approach, which only worked in a single process — Celery lets pipeline runs
scale across many workers and survive a web-process restart.

Entry points:
    celery -A autojob.celery_app.celery worker --loglevel=info
    celery -A autojob.celery_app.celery beat   --loglevel=info   # scheduler
"""

from __future__ import annotations

from celery import Celery, Task

from . import create_app


def make_celery(app=None) -> Celery:
    app = app or create_app()

    class FlaskTask(Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery = Celery(
        app.import_name,
        task_cls=FlaskTask,
        broker=app.config["CELERY_BROKER_URL"],
        backend=app.config["CELERY_RESULT_BACKEND"],
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        # Run tasks inline (no broker) when the app is configured for it —
        # used by tests and local dry-runs without Redis.
        task_always_eager=app.config.get("CELERY_TASK_ALWAYS_EAGER", False),
        task_eager_propagates=True,
    )
    celery.flask_app = app
    return celery


celery = make_celery()

# Import tasks so they register on the Celery instance.
from . import tasks  # noqa: E402,F401
