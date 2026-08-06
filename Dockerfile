# ── AutoJob (multi-tenant SaaS) image ────────────────────────
# Runs the multi-tenant app in autojob/ (sign-up/login, per-user CV, isolated
# jobs/settings/credentials). This is what Render builds and runs.
#
# It runs as a SINGLE web service — no Redis and no separate Celery worker:
# discovery runs execute in a background thread in-process and progress streams
# over an in-process bus. That requires exactly ONE gunicorn worker (with
# threads), so a run's thread and its SSE stream live in the same process.
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

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt gunicorn

COPY . .

# Writable dir for uploaded CVs and generated packages (per-user subdirs).
# On Render this points at the mounted disk (/var/data) via STORAGE_LOCAL_ROOT.
RUN mkdir -p /var/data

# Render injects $PORT; default to 10000 for a plain `docker run`.
ENV PORT=10000
EXPOSE 10000

# Apply DB migrations, then serve. One gthread worker + threads: the in-process
# progress bus and background run threads require a single process, while
# threads still handle concurrent SSE streams. --timeout 0 keeps long-lived SSE
# connections and multi-minute background runs from tripping the worker timeout.
# Shell form so ${PORT} is expanded at runtime.
CMD python manage.py init-db && \
    exec gunicorn autojob.wsgi:app --bind 0.0.0.0:${PORT} \
        --worker-class gthread --workers 1 --threads 8 --timeout 0 \
        --access-logfile - --error-logfile -
