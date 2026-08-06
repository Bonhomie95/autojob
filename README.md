# 🎯 AutoJob

**Upload your CV once. AutoJob finds real roles that match it, tailors an
application for each, sends it from your own mailbox, and follows up — all kept
private to your account.**

AutoJob is a **multi-tenant web app**: every user signs up, uploads their own
CV, configures their own email sender and API keys, and sees only their own
jobs, applications, and history. One person's data is never visible to another.

> Migrated from an earlier single-user tool. The stateless engine (CV parsing,
> job scoring, document generation, scrapers) lives in `core/` and `scrapers/`
> and is reused per tenant; everything user-facing and stateful lives in the
> multi-tenant `autojob/` package.

---

## How it works

1. **Sign up / log in.** Each account is isolated — separate jobs, settings, and
   credentials.
2. **Upload your CV** (PDF / DOCX / TXT). AutoJob parses it and shows what it
   understood — your roles, seniority, skills, and the searches it will run. If
   your CV states something twice (e.g. two emails), it asks you which to use.
3. **Connect your email + keys** in Settings. Your SMTP password and API keys are
   **encrypted at rest** (Fernet) and only ever decrypted in memory during a run.
   If you don't bring your own keys, the platform can fall back to managed pools
   (`MANAGED_*`) so you can "just upload a CV".
4. **Run.** It scrapes public job boards, scores each role against your CV,
   builds a tailored CV + cover letter per company, and — with your explicit
   consent and auto-send on — emails the application from *your* mailbox. It
   remembers every job it applied to (won't apply twice) and can follow up after
   a few days.

Progress streams live to your browser while a run executes.

---

## Run it locally

Requires Python 3.13 and the project `venv`.

```bash
make install                        # pip install -r requirements.txt
cp .env.example .env                # dev defaults are fine (SQLite, dev secret)
make upgrade                        # apply DB migrations
make dev                            # → http://localhost:9000
```

Then open <http://localhost:9000>, register, and upload a CV.

Common tasks: `make test` (run the suite), `make lint`, `make upgrade` (migrate).

---

## Deploy on Render (single service, no Redis)

The default deployment is **one web service + a Postgres database** — no Redis
and no separate worker. Discovery runs execute in a background thread inside the
web process and progress streams in-process, so a single always-on instance is
all you need.

1. Generate a credential-encryption key:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Push this repo to GitHub.
3. In Render → **New + → Blueprint** → pick the repo. Render reads
   [`render.yaml`](render.yaml) and provisions a managed Postgres database, a web
   service (built from the [`Dockerfile`](Dockerfile)), and a small disk at
   `/var/data` for uploaded CVs and generated packages.
4. Set the one secret Render can't generate for you: paste the Fernet key from
   step 1 into **`CREDENTIAL_ENCRYPTION_KEY`**. (`SECRET_KEY` and `DATABASE_URL`
   are wired up automatically.)
5. Open the app URL, register, upload your CV, connect email, run.

The Dockerfile runs `flask db upgrade` on boot and serves with a **single
gunicorn worker** (with threads) — required so a run's background thread and its
SSE progress stream share one process. Postgres holds all user data (so the
"don't apply twice" memory is safe across redeploys); the disk holds files.

### Scaling out (optional)

To run multiple web workers you need a real broker so runs and progress cross
process boundaries. Set `RUN_VIA_CELERY=true`, point `REDIS_URL` at Redis, and
run a Celery worker. [`docker-compose.yml`](docker-compose.yml) brings up that
full stack (web + worker + beat + Postgres + Redis) locally.

---

## Configuration

Application-level settings are environment variables (see
[`.env.example`](.env.example)); per-user settings live in the database and are
edited in the app UI. Production requires `SECRET_KEY`, `DATABASE_URL`, and
`CREDENTIAL_ENCRYPTION_KEY` and refuses to boot without them.

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) and
[`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) for more.

---

## Architecture

```
autojob/            Multi-tenant Flask app (auth, CV, settings, runs, jobs)
  ├─ models/        SQLAlchemy models — every row scoped to a user
  ├─ blueprints/    HTTP routes
  ├─ services/      repository, runtime_config, engine_adapter, mailer, progress
  ├─ tasks.py       per-user discovery run (reuses core/)
  └─ wsgi.py        gunicorn entrypoint: autojob.wsgi:app
core/               Stateless engine: cv_profile, scorer, discovery, document_generator, …
scrapers/           Public-API / RSS job-board scrapers
config.py, database.py   Legacy engine globals the core/ modules read; the SaaS
                         shims them per-tenant via services/engine_adapter.py
migrations/         Alembic migrations
tests/              Pytest suite (auth, tenancy, CV, runs, follow-up, health)
```
