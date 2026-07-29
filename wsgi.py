"""
wsgi.py — production entrypoint for the single-user AutoJob app.

`app.py` only initialises the database and starts the scheduler inside its
`__main__` block, which never runs under a WSGI server like gunicorn. This
module does that setup at import time so `gunicorn wsgi:app` behaves exactly
like `python app.py`.

Run it with a single worker (see Procfile / render.yaml): the live pipeline
log stream (SSE), the in-process run lock, and APScheduler all assume one
process.
"""

from pathlib import Path

from app import app  # noqa: F401  (exported for gunicorn as `wsgi:app`)
from config import config
from database import init_db
from scheduler import start_scheduler

# One-time setup, mirroring app.py's __main__ block.
init_db()
Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
Path(config.INPUT_DIR).mkdir(parents=True, exist_ok=True)
start_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.FLASK_PORT)
