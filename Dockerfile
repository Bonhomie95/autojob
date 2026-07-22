# ── AutoJob SaaS image ───────────────────────────────────────
# One image runs any role (web / worker / beat); the compose file picks the
# command. Slim base + non-root user + a healthcheck for orchestration.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

# System deps: libreoffice is optional (PDF rendering); kept out of the base
# image to stay small — add it in a build arg if you need server-side PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt gunicorn

COPY . .

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:9000/healthz || exit 1

# Default command runs the web server; override for worker/beat in compose.
CMD ["gunicorn", "autojob.wsgi:app", "--bind", "0.0.0.0:9000", \
     "--workers", "4", "--timeout", "120", "--access-logfile", "-"]
