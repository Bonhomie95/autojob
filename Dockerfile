# ── AutoJob API image (backend only) ──────────────────────────
# The frontend is a separate static SPA deployed on its own (e.g. Vercel) —
# this image is the JSON API alone. Render builds this from the repo root,
# but only backend/ is actually used (see .dockerignore).
#
# Runs as a SINGLE web service — no Redis and no separate Celery worker:
# discovery runs execute in a background thread in-process and progress
# streams over an in-process bus. That requires exactly ONE gunicorn worker
# (with threads), so a run's thread and its SSE stream live in the same
# process.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    FLASK_APP=autojob.wsgi:app

# curl for the healthcheck; build-essential for wheels; tesseract-ocr so
# scanned/image-only CVs can be read via OCR.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential curl tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt gunicorn

COPY backend/ .

# Writable dir for uploaded CVs and generated packages (per-user subdirs).
# On Render this points at the mounted disk (/var/data) via STORAGE_LOCAL_ROOT.
RUN mkdir -p /var/data

# Render injects $PORT; default to 10000 for a plain `docker run`.
ENV PORT=10000
EXPOSE 10000

# MongoDB is schemaless — no migration step before serving. The app factory
# ensures indexes exist on every start (see autojob/db_bootstrap.py), so
# gunicorn loading the WSGI app is all boot needs to do.
# One gthread worker + threads: the in-process progress bus and background run
# threads require a single process, while threads still handle concurrent SSE
# streams. --timeout 0 keeps long-lived SSE connections and multi-minute
# background runs from tripping the worker timeout. Shell form so ${PORT} is
# expanded at runtime.
CMD exec gunicorn autojob.wsgi:app --bind 0.0.0.0:${PORT} \
        --worker-class gthread --workers 1 --threads 8 --timeout 0 \
        --access-logfile - --error-logfile -
