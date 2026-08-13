# 🎯 AutoJob

**Upload your CV once. AutoJob finds real roles that match it, tailors an
application for each, sends it from your own mailbox, and follows up — all kept
private to your account.**

AutoJob is a **multi-tenant web app**: every user signs up, uploads their own
CV, configures their own email sender and API keys, and sees only their own
jobs, applications, and history. One person's data is never visible to another.

> Migrated from an earlier single-user tool. The stateless engine (CV parsing,
> job scoring, document generation, scrapers) lives in `backend/core/` and
> `backend/scrapers/` and is reused per tenant; everything user-facing and
> stateful lives in the `backend/autojob/` Flask API + the `frontend/` React app.

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
   remembers every job it applied to (won't apply twice), can follow up after a
   few days, and you can stop a run mid-flight or clear its memory to let
   previously-seen postings resurface.

Progress streams live to your browser while a run executes.

---

## Repo layout

```
backend/            Flask JSON API + the stateless engine
  autojob/             The multi-tenant app itself
    models/              Plain-dataclass models (MongoDB — no ORM)
    blueprints/          HTTP routes, mounted under /api
    services/            repository, runtime_config, engine_adapter, mailer, storage, progress
    tasks.py             per-user discovery run (reuses core/)
    wsgi.py              gunicorn entrypoint: autojob.wsgi:app
  core/                Stateless engine: cv_profile, scorer, discovery, document_generator, …
  scrapers/            Public-API / RSS job-board scrapers
  config.py, database.py   Legacy engine globals core/ reads; the SaaS shims
                            them per-tenant via services/engine_adapter.py
  tests/               Pytest suite (auth, tenancy, CV, runs, follow-up, health)
  manage.py            Dev entrypoint (`python manage.py run`)
frontend/            React + Vite SPA — owns all rendering; talks to backend/ over /api
docs/                Deployment + compliance notes
```

The **venv stays at the repo root** (Python virtualenvs aren't safely
relocatable), so backend commands `cd backend` first — `make` handles this for
you.

---

## Run it locally

Requires Python 3.13, Node 22, and the project `venv`.

```bash
make install                        # backend: pip install -r backend/requirements.txt
cp backend/.env.example backend/.env  # fill in MONGODB_URI (a free Atlas cluster works)
make dev                            # backend → http://localhost:9000

make frontend-install               # frontend: npm install
make frontend-dev                   # frontend → http://localhost:5173 (proxies /api to :9000)
```

Or run both together with one command: `make dev-full` (backend + Vite, both
in one terminal, Ctrl+C stops both).

Open <http://localhost:5173> in dev (Vite serves the SPA with hot reload and
proxies API calls to the Flask backend on :9000), register, and upload a CV.

Common tasks: `make test` (backend suite), `make lint`, `make frontend-build`
(production SPA bundle, for local testing — Vercel builds it independently on
deploy).

---

## Deploy: backend on Render, frontend on Vercel

The backend is **one Render web service + MongoDB Atlas** — no Redis and no
separate worker. Discovery runs execute in a background thread inside the web
process and progress streams in-process, so a single always-on instance is all
you need. The frontend is a separate static SPA on Vercel; the two talk to
each other cross-origin (see "Cross-origin config" in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)).

### 1. Backend → Render

1. Generate a credential-encryption key:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Push this repo to GitHub.
3. In Render → **New + → Blueprint** → pick the repo. Render reads
   [`render.yaml`](render.yaml) and provisions a web service (built from the
   root [`Dockerfile`](Dockerfile), backend only) plus a small disk at
   `/var/data` for uploaded CVs and generated packages.
4. Set the secrets Render can't generate for you, in the dashboard's
   Environment tab:
   - **`MONGODB_URI`** — a `mongodb+srv://...` connection string (Atlas free
     tier works fine) with the database name in the path, e.g. `.../autojob`.
     Render has no native Mongo provisioning, so this is always manual.
   - **`CREDENTIAL_ENCRYPTION_KEY`** — the Fernet key from step 1.
   - **`FRONTEND_ORIGIN`** — the Vercel URL from step 2 below (you'll need to
     circle back and set this once you have it).
   - (`SECRET_KEY` is generated automatically by Render.)
5. Note the Render service URL (e.g. `https://autojob.onrender.com`) — the
   frontend needs it next.

### 2. Frontend → Vercel

1. In Vercel → **Add New → Project** → pick the repo, with **Root Directory**
   set to `frontend/`. Vercel auto-detects the Vite framework preset — no
   build command override needed.
2. In the project's Environment Variables, set **`VITE_API_BASE_URL`** to the
   Render URL from step 1 above (e.g. `https://autojob.onrender.com`), for
   the Production environment (and Preview, if you want preview deploys to
   work against the same backend).
3. Deploy. Note the resulting Vercel URL (e.g. `https://autojob.vercel.app`).
4. Back in Render, set `FRONTEND_ORIGIN` to that Vercel URL and redeploy the
   backend — without this it rejects the frontend's requests outright (CORS).
5. Open the Vercel URL, register, upload your CV, connect email, run.

MongoDB holds all user data (so the "don't apply twice" memory is safe across
redeploys, and there's no migration step — see `backend/autojob/db_bootstrap.py`);
the disk holds files.

### Scaling out (optional)

To run multiple web workers you need a real broker so runs and progress cross
process boundaries. Set `RUN_VIA_CELERY=true`, point `REDIS_URL` at Redis, and
run a Celery worker. [`docker-compose.yml`](docker-compose.yml) brings up that
stack (web + worker + beat + Redis) locally — copy `.env.example` to
`.env.docker` first and fill in `MONGODB_URI` and the secrets.

---

## Configuration

Application-level settings are environment variables (see
[`backend/.env.example`](backend/.env.example)); per-user settings live in the
database and are edited in the app UI. Production requires `SECRET_KEY`,
`MONGODB_URI`, and `CREDENTIAL_ENCRYPTION_KEY` and refuses to boot without them.

### AI providers

CV tailoring, cover letters, and job scoring all go through one LLM call —
which provider handles it is a per-user choice in **Settings → Credentials**
(each user can pick their own, independent of what the operator set as the
platform default):

| Provider | Free tier? | Notes |
|---|---|---|
| **Groq** (default) | Yes | Fast, generous free tier — good default for most users |
| **OpenAI** | No | The most widely used; `gpt-4o-mini` is cheap |
| **Anthropic (Claude)** | No | `claude-3-5-haiku` is cheap and fast |
| **Google Gemini** | Yes | `gemini-2.0-flash` — free tier with rate limits |
| **xAI (Grok)** | No | `grok-3-mini` is the default model |
| **OpenRouter** | Some models | One key, many models/providers — check openrouter.ai/models for what's currently free |

To use a provider, get an API key from it (links in
[`backend/.env.example`](backend/.env.example)) and either:

- **Set it platform-wide** — put it in `.env` as `GROQ_API_KEY` /
  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `GROK_API_KEY` /
  `OPENROUTER_API_KEY`, and set `AI_PROVIDER` to match. This becomes the
  default for every user who hasn't picked their own.
- **Bring your own, per account** — in the app, Settings → Credentials, pick
  the provider from the dropdown and paste your key. This always wins over
  the platform default for that user.

An operator can also fund a "managed pool" per provider (`MANAGED_GROQ_KEYS`,
`MANAGED_OPENAI_KEYS`, etc. — see `.env.example`) so users can "just upload a
CV" without any key of their own, on whichever provider they pick.

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) and
[`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) for more.
