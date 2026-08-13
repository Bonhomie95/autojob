# Deploying AutoJob SaaS

The SaaS application is split into two independently deployed pieces:

- **`backend/`** — a Flask JSON API (everything under `/api`), deployed to
  **Render**. The stateless engine — CV parsing, scoring, document
  generation, scrapers — lives in `backend/core/` and `backend/scrapers/` and
  is reused per tenant; the legacy `backend/config.py`/`backend/database.py`
  globals those modules read are shimmed per-run via
  `backend/autojob/services/engine_adapter.py`.
- **`frontend/`** — the React/Vite SPA, deployed to **Vercel** as a static
  site. It talks to the backend cross-origin (different domains), so cookies
  and CORS are configured explicitly — see "Cross-origin config" below.

The default backend deployment is a **single web service, no Redis**: runs
execute in a background thread and progress streams in-process (see the
Dockerfile / render.yaml). Celery + Redis is an optional scale-out path
(`RUN_VIA_CELERY=true`), shown below.

## Architecture

```
                 ┌───────────────┐          ┌─────────────────┐     ┌──────────────┐
  Browser ─────▶ │  Vercel (SPA)  │ ──/api──▶│  web (gunicorn)  │──│  MongoDB      │
                 │  static build  │  CORS +  │  autojob.wsgi:app│  │  (Atlas)      │
                 └───────────────┘  cookies  └────────┬─────────┘  └──────────────┘
                                                       │  enqueue  ┌──────────────┐
                                                       ▼           │  Redis        │
                                               ┌─────────────┐     │  (broker +    │
                                               │  worker (celery) │─│   pub/sub +   │
                                               │  beat (celery)   │ │   rate-limit) │
                                               └─────────────┘     └──────────────┘
```

- **Vercel** — builds and serves the static React SPA. Nothing runs
  server-side here; every dynamic request goes to the Render API.
- **web** — the JSON API (Gunicorn), one process.
- **worker** — runs pipeline tasks (`autojob.tasks`) — only exists in the
  optional scale-out path.
- **beat** — the hourly multi-tenant scheduler (`dispatch_scheduled_runs`) —
  same, optional.
- **MongoDB** — the only datastore. Schemaless, so there's no migration step;
  `autojob/db_bootstrap.py` ensures indexes exist on every boot.
- **Redis** — Celery broker, SSE progress pub/sub, and rate-limit storage —
  only needed in the scale-out path.

## Cross-origin config (Vercel ↔ Render)

The frontend and backend are on different domains in production, so three
things are wired for that specifically:

- **CORS** — set `FRONTEND_ORIGIN` on the backend to the Vercel URL (e.g.
  `https://autojob.vercel.app`). Without it, the API has no CORS headers at
  all and the browser blocks every request from the frontend.
- **Cookies** — the session cookie needs `SameSite=None; Secure` to be sent
  cross-site. `ProductionConfig` defaults `SESSION_COOKIE_SAMESITE` to
  `"None"` already; `SESSION_COOKIE_SECURE=true` (below) supplies `Secure`.
- **CSRF** — Flask-WTF's extra same-origin Referer check is disabled
  (`WTF_CSRF_SSL_STRICT=False`) since it assumes frontend and backend share a
  host; the actual token check is unaffected.
- **API base URL** — set `VITE_API_BASE_URL` in Vercel's project environment
  variables to the Render URL (e.g. `https://autojob.onrender.com`) — see
  `frontend/.env.example`. It's a *build-time* Vite variable, so it must be
  set before the Vercel build runs, not just at runtime.

## Required configuration (production)

Production refuses to boot without these (`ProductionConfig`):

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session/CSRF signing — stable & secret |
| `MONGODB_URI` | `mongodb+srv://user:pass@cluster/dbname` |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key encrypting per-user secrets |

Generate secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"          # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CREDENTIAL_ENCRYPTION_KEY
```

Also required to let the deployed frontend call this API at all:
`FRONTEND_ORIGIN` (the Vercel URL) and `SESSION_COOKIE_SECURE=true` — see
"Cross-origin config" above.

Recommended extras: `REDIS_URL`, `RATELIMIT_STORAGE_URI=redis://…`,
`SENTRY_DSN`, `LOG_JSON=true`, `AI_PROVIDER` + a matching `MANAGED_*_KEYS`
pool (server-side LLM pool so users needn't bring keys — see "AI providers"
in [README.md](../README.md) for the provider options), `METRICS_TOKEN`
(require a header to read `/metrics` if it's reachable from the public
internet).

## Local production-shaped stack

```bash
cp backend/.env.example .env.docker   # fill MONGODB_URI, SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY
docker compose up --build
```

App on http://localhost:9000. No migration/init step — MongoDB has no schema.

## Health & observability

- `GET /healthz` — liveness (no dependencies touched).
- `GET /readyz` — readiness (pings MongoDB); wire to your load balancer.
- `GET /metrics` — Prometheus request counts & latencies. Gate with
  `METRICS_TOKEN` if this port is public.
- Logs are JSON when `LOG_JSON=true`; every response carries `X-Request-ID`.

## Scaling notes

- Scale `web` and `worker` independently. State lives in MongoDB/Redis, so both
  are horizontally scalable (unlike the legacy in-process threads/queues).
- Generated per-job documents (tailored CV/cover letter/email) live in the
  storage abstraction (`backend/autojob/services/storage.py` — local disk or
  S3), not on the filesystem of whichever process generated them — so
  downloads and sends work correctly across replicas or a separate worker.
- Use Redis-backed rate limiting in production (`RATELIMIT_STORAGE_URI`), not the
  in-memory default, so limits hold across web replicas.
- For Prometheus with multiple Gunicorn workers, scrape each worker or enable
  `prometheus_client` multiprocess mode.

## Before going live

Work through [COMPLIANCE.md](COMPLIANCE.md) — sending consent, SPF/DKIM/DMARC,
suppression lists, and job-source ToS are launch blockers, not nice-to-haves.
