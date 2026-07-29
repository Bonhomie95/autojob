# ── AutoJob (single-user app) image ──────────────────────────
# Runs the no-sign-up dashboard (root app.py / wsgi.py) — NOT the multi-tenant
# SaaS package in autojob/. This is what Render builds and runs.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# curl is used by the healthcheck; build-essential for any wheels that need it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt gunicorn

COPY . .

# Writable dirs for uploaded CVs, generated packages, and the local SQLite
# fallback. On Render these point at the mounted disk (/var/data).
RUN mkdir -p /app/input /app/output /var/data

# NOTE: runs as root on purpose. Render mounts the persistent disk as
# root-owned, so a non-root user could not write uploaded CVs / generated
# packages to /var/data. This is a single-user personal tool, so root in the
# container is an acceptable, reliable trade-off.

# Render injects $PORT; default to 10000 for a plain `docker run`.
ENV PORT=10000
EXPOSE 10000

# Single worker (the live log stream, in-process run lock, and APScheduler all
# assume one process); threads handle the concurrent SSE connections.
# Shell form so ${PORT} is expanded at runtime.
CMD gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 \
    --timeout 120 --access-logfile - --error-logfile -
