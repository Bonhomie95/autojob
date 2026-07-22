# Deploying AutoJob SaaS

The SaaS application is the `autojob/` package (Flask app factory + Celery). The
legacy single-user `app.py` and its modules remain in the repo as the engine
source; the SaaS reuses the engine's stateless parts.

## Architecture

```
                 ┌─────────────┐     ┌──────────────┐
  Browser ─────▶ │  web (gunicorn) │──│  Postgres     │
                 │  autojob.wsgi:app │ └──────────────┘
                 └───────┬───────┘     ┌──────────────┐
                         │  enqueue     │  Redis        │
                         ▼              │  (broker +    │
                 ┌─────────────┐        │   pub/sub +   │
                 │  worker (celery) │────│   rate-limit) │
                 │  beat (celery)   │    └──────────────┘
                 └─────────────┘
```

- **web** — serves the UI and API (Gunicorn).
- **worker** — runs pipeline tasks (`autojob.tasks`).
- **beat** — the hourly multi-tenant scheduler (`dispatch_scheduled_runs`).
- **Postgres** — primary datastore (SQLite is dev-only).
- **Redis** — Celery broker, SSE progress pub/sub, and rate-limit storage.

## Required configuration (production)

Production refuses to boot without these (`ProductionConfig`):

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session/CSRF signing — stable & secret |
| `DATABASE_URL` | `postgresql+psycopg2://…` |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key encrypting per-user secrets |

Generate secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"          # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CREDENTIAL_ENCRYPTION_KEY
```

Recommended extras: `REDIS_URL`, `RATELIMIT_STORAGE_URI=redis://…`,
`SESSION_COOKIE_SECURE=true`, `SENTRY_DSN`, `LOG_JSON=true`,
`MANAGED_GROQ_KEYS` (server-side LLM pool so users needn't bring keys).

## Local production-shaped stack

```bash
cp .env.saas.example .env.docker      # fill SECRET_KEY + CREDENTIAL_ENCRYPTION_KEY
export SECRET_KEY=... CREDENTIAL_ENCRYPTION_KEY=...
docker compose up --build
```

`migrate` runs `flask db upgrade` before `web`/`worker` start. App on
http://localhost:9000.

## Migrations

```bash
FLASK_APP=autojob.wsgi:app flask db upgrade          # apply
FLASK_APP=autojob.wsgi:app flask db migrate -m "msg" # generate after model changes
```

CI fails if models drift from migrations (see `.github/workflows/ci.yml`).

## Health & observability

- `GET /healthz` — liveness (no dependencies touched).
- `GET /readyz` — readiness (checks the database); wire to your load balancer.
- `GET /metrics` — Prometheus request counts & latencies.
- Logs are JSON when `LOG_JSON=true`; every response carries `X-Request-ID`.

## Scaling notes

- Scale `web` and `worker` independently. State lives in Postgres/Redis, so both
  are horizontally scalable (unlike the legacy in-process threads/queues).
- Use Redis-backed rate limiting in production (`RATELIMIT_STORAGE_URI`), not the
  in-memory default, so limits hold across web replicas.
- For Prometheus with multiple Gunicorn workers, scrape each worker or enable
  `prometheus_client` multiprocess mode.

## Before going live

Work through [COMPLIANCE.md](COMPLIANCE.md) — sending consent, SPF/DKIM/DMARC,
suppression lists, and job-source ToS are launch blockers, not nice-to-haves.
