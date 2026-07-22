# 🎯 Auto Job

> **Two applications live in this repo:**
> - **`autojob/` — the multi-tenant SaaS** (new). Sign-up/login, per-user CV upload,
>   isolated data per tenant, encrypted per-user credentials, background job runs
>   via Celery, a landing page + dashboard UI. Run it with `python manage.py run`
>   (or Docker). See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** and
>   **[docs/COMPLIANCE.md](docs/COMPLIANCE.md)**.
> - **`app.py` + engine modules (`core/`, `scrapers/`, `mailer.py`, …) — the original
>   single-user tool** (below). The SaaS reuses these engine parts.
>
> Quickstart (SaaS): `pip install -r requirements.txt` → `cp .env.saas.example .env`
> → `FLASK_APP=autojob.wsgi:app flask db upgrade` → `python manage.py run` → http://localhost:9000

---

## Original single-user tool

> Automated job discovery, token-light scoring, HR contact extraction, per-company application package generation, sender rotation, follow-up scheduling, and reply detection — all in a clean Flask dashboard.

---

## What It Does

Auto Job runs a full pipeline on demand:

1. **Scrapes** 9 job boards (LinkedIn, Indeed, RemoteOK, WeWorkRemotely, Jobicy, Remotive, Arbeitnow, HackerNews, Google Jobs) for your target roles
2. **Researches** each company with a brief AI summary — so your cover letters mention real things about the company, not just generic fit language
3. **Scores** every job against your CV — 0–100 match score (AI via Groq, or offline keyword mode — see below)
4. **Filters** out blacklisted keywords, low-scoring jobs, and duplicates automatically
5. **Extracts HR contacts** — posting scrape first, then Hunter.io → Prospeo → headless Chromium/search, verified through Reoon/MillionVerifier
6. **Generates** a fully customized CV + cover letter per company, ATS-optimized for that specific job description
7. **Sends** application emails automatically with a duplicate guard (won't email the same person twice within 30 days) and smart grouping (one email for multiple roles at the same company)
8. **Follows up** automatically after 6 days if no reply has been detected
9. **Detects replies** via IMAP — if a recruiter responded, the follow-up is skipped

```
output/
├── Microsoft_Senior_React_Native_Developer/
│   ├── CV.docx
│   ├── CV.pdf
│   ├── CoverLetter.docx
│   ├── CoverLetter.pdf
│   ├── EMAIL_DRAFT.txt        ← ready-to-send email subject + body
│   └── CONTACT_INFO.txt       ← HR name, email, apply link, match analysis
├── Stripe_Backend_Engineer/
│   └── …
└── …
```

---

## Stack

| Layer | Tech |
|---|---|
| Scraping | `requests`, `BeautifulSoup`, `feedparser` |
| AI (scoring + generation) | Groq API — `llama-3.3-70b-versatile` (optional for scoring) |
| Document generation | `python-docx`, `reportlab`, LibreOffice (PDF) |
| Database | SQLite (via stdlib `sqlite3`) |
| Web UI | Flask 3 + vanilla JS (SSE for live logs) |
| Config | `.env` via `python-dotenv` |
| HR enrichment (optional) | Posting scrape → Hunter.io → Prospeo → Chromium/search → Reoon/MillionVerifier |
| Reply detection | IMAP (any provider) |

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/Bonhomie95/autojob.git
cd autojob
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install LibreOffice (for PDF conversion)

```bash
# macOS
brew install --cask libreoffice

# Ubuntu / Debian
sudo apt install libreoffice

# Windows — download from libreoffice.org
# Falls back to reportlab renderer if not installed
```

### 3. Configure `.env`

```bash
cp .env.example .env
```

At minimum, fill in:

```env
GROQ_API_KEY_1=your_groq_key_here
CANDIDATE_NAME=Your Full Name
CANDIDATE_EMAIL=you@example.com
TARGET_ROLES=React Native Developer,Full Stack Engineer,Backend Engineer
```

Get a free Groq key at: https://console.groq.com

> **No Groq key?** Set `LLM_SCORING=false` and the pipeline scores jobs with a keyword matcher — no API calls needed. Document generation still requires a Groq key (or Ollama).

### 4. Configure MailerSend / SMTP2GO (optional)

Auto-send is off until a sender is configured. MailerSend is tried first, then SMTP2GO.

```env
MAILERSEND_SMTP_USER=your_mailersend_smtp_user
MAILERSEND_API_KEY=your_mailersend_smtp_password_or_key
MAILERSEND_SMTP_FROM=verified@yourdomain.com

SMTP2GO_USERNAME=your_smtp2go_username
SMTP2GO_API_KEY=your_smtp2go_password_or_key
SMTP2GO_SMTP_FROM=verified@yourdomain.com

SMTP_REPLY_TO=you@example.com
SMTP_AUTO_SEND=true
```

### 5. Configure IMAP for reply detection (optional)

Reply detection can use any mailbox with IMAP access:

```env
IMAP_HOST=imap.yourmailhost.com
IMAP_PORT=993
IMAP_USER=you@example.com
IMAP_PASSWORD=your_imap_password
```

### 6. Add your CV

```
input/
└── YourCV.docx        # or .pdf or .txt
```

The app picks the first file it finds. You can also upload via the web UI.

### 7. Run

```bash
python app.py
```

Open: **http://localhost:9000**

---

## Offline / Free Mode

You can run the full pipeline with **zero paid API calls** by combining two features:

| Feature | Env var | Default |
|---|---|---|
| Keyword job scoring (no AI) | `LLM_SCORING=false` | `false` |
| Ollama for doc generation | `OLLAMA_ENABLED=true` | `false` |

```env
LLM_SCORING=false        # score jobs with keyword matcher, no quota
OLLAMA_ENABLED=true      # generate CVs/cover letters via local Ollama
OLLAMA_MODELS=qwen2.5-coder:32b,gemma3:12b,mistral
```

The keyword scorer uses TF-IDF-style overlap between your CV and each job description, with phrase bonuses for tech terms (React Native, Node.js, CI/CD, etc.) and a title-match boost. It produces the same score/gaps/ats_keywords schema as the Groq scorer, so nothing else in the pipeline changes.

> `ENRICH_COMPANY_DATA` has no effect when `LLM_SCORING=false` — company research is skipped automatically.

---

## HR Email Enrichment

When an email isn't in the job posting, AutoJob tries free/cheap sources first, stopping as soon as one delivers a verified result:

| Service | Free tier | Sign-up |
|---|---|---|
| Hunter.io | 25 domain searches/month | https://hunter.io |
| Prospeo | 75 domain searches/month | https://prospeo.io |
| Headless search | Free | DuckDuckGo/Bing/Google via Chromium |
| Reoon | Free verification credits | https://reoon.com/email-verifier/ |
| MillionVerifier | Free verification credits | https://www.millionverifier.com/ |

None require a credit card on the free tier. Add keys for any combination you want:

```env
HUNTER_API_KEY=your_hunter_key
PROSPEO_API_KEY=your_prospeo_key
REOON_API_KEY=your_reoon_key
MILLION_VERIFIER_API_KEY=your_million_verifier_key
CONTACT_SEARCH_ENABLED=true
```

If none are configured, AutoJob falls back to browser search and the application URL only. It never fabricates an email.

---

## Configuration Reference (`.env`)

### Core

| Variable | Description | Default |
|---|---|---|
| `GROQ_API_KEY` | Groq API key (or `GROQ_API_KEY_1`…`N` for pool) | — |
| `LLM_SCORING` | `true` = Groq scoring; `false` = keyword scoring (no API) | `false` |
| `HUNTER_API_KEY` | Hunter.io for HR email discovery (25/month free) | — |
| `PROSPEO_API_KEY` | Prospeo for HR email discovery (75/month free) | — |
| `REOON_API_KEY` | Email verification before sending | — |
| `MILLION_VERIFIER_API_KEY` | Secondary email verification before sending | — |
| `CONTACT_SEARCH_ENABLED` | Use headless Chromium/search for lead discovery | `true` |
| `TARGET_ROLES` | Comma-separated job titles to search | — |
| `KEYWORDS` | Keywords to prioritize in matching | — |
| `BLACKLIST_KEYWORDS` | Jobs containing these are auto-skipped | `internship,unpaid` |
| `MIN_MATCH_SCORE` | Min score (0–100) to generate documents | `60` |
| `ENRICH_COMPANY_DATA` | Fetch company summary for richer personalisation (LLM only) | `true` |
| `GENERATE_DOCS_WITHOUT_HR` | Generate docs even when no HR contact found | `false` |

### Candidate

| Variable | Description |
|---|---|
| `CANDIDATE_NAME` | Your full name — appears on all documents |
| `CANDIDATE_EMAIL` | Your email |
| `CANDIDATE_PHONE` | Your phone |
| `CANDIDATE_LOCATION` | e.g. `Lagos, Nigeria (Open to Remote)` |
| `CANDIDATE_LINKEDIN` | LinkedIn URL |
| `CANDIDATE_GITHUB` | GitHub URL |

### SMTP

| Variable | Description | Default |
|---|---|---|
| `SMTP_HOST` | Optional generic SMTP fallback hostname | — |
| `SMTP_PORT` | SMTP port (587 = STARTTLS, 465 = SSL) | `587` |
| `SMTP_USER` | SMTP username (usually your email) | — |
| `SMTP_PASSWORD` | Optional generic SMTP password | — |
| `SMTP_FROM` | From address | — |
| `SMTP_TLS` | Enable STARTTLS (required for port 587) | `true` |
| `SMTP_AUTO_SEND` | Send automatically during pipeline run | `false` |
| `SMTP_ATTACH_PDF` | Attach PDF documents | `true` |
| `SMTP_ATTACH_DOCX` | Attach DOCX documents | `false` |
| `SMTP_THROTTLE_SECONDS` | Min seconds between sends | `8` |
| `SMTP_FORMAT` | `plain` or `mixed` (plain+HTML) | `plain` |
| `SMTP_REPLY_TO` | Reply address used for all sender providers | `CANDIDATE_EMAIL` |
| `EMAIL_DAILY_LIMIT` | Hard cap across all senders | `100` |
| `MAILERSEND_API_KEY` | Optional MailerSend SMTP password/API key for sender rotation | — |
| `SMTP2GO_API_KEY` | Optional SMTP2GO key for sender rotation | — |

### Duplicate Guard

| Variable | Description | Default |
|---|---|---|
| `DEDUP_WINDOW_DAYS` | Days before the same HR address can be contacted again | `30` |

### Follow-Up & Reply Detection

| Variable | Description | Default |
|---|---|---|
| `FOLLOW_UP_ENABLED` | Enable automatic follow-up emails | `true` |
| `FOLLOW_UP_DAYS` | Days after first send before follow-up fires | `6` |
| `IMAP_HOST` | IMAP server for reply detection | — |
| `IMAP_PORT` | IMAP port | `993` |
| `IMAP_USER` | IMAP username (defaults to `SMTP_USER`) | — |
| `IMAP_PASSWORD` | IMAP password (defaults to `SMTP_PASSWORD`) | — |

### Scrapers

| Variable | Description | Default |
|---|---|---|
| `SCRAPE_LINKEDIN` | Enable LinkedIn | `true` |
| `SCRAPE_INDEED` | Enable Indeed | `true` |
| `SCRAPE_REMOTEOK` | Enable RemoteOK | `true` |
| `SCRAPE_WEWORKREMOTELY` | Enable WeWorkRemotely | `true` |
| `SCRAPE_HACKERNEWS` | Enable HN "Who is Hiring" | `true` |
| `SCRAPE_JOBICY` | Enable Jobicy | `true` |
| `SCRAPE_REMOTIVE` | Enable Remotive | `true` |
| `SCRAPE_ARBEITNOW` | Enable Arbeitnow | `true` |
| `SCRAPE_GOOGLE` | Enable Google Jobs (experimental) | `false` |
| `MAX_JOBS_PER_BOARD` | Max jobs per source per run | `50` |

### Proxy

| Variable | Description | Default |
|---|---|---|
| `PROXY_ENABLED` | Enable proxy rotation for scrapers | `false` |
| `PROXY_LIST` | Comma-separated proxy URLs | — |

---

## Web UI Pages

| Page | URL | Purpose |
|---|---|---|
| Dashboard | `/` | Stats, trigger runs, upload CV, live progress log |
| Jobs | `/jobs` | Full job table with filters |
| Job Detail | `/job/<id>` | Full description, contacts, download documents, mark applied |

---

## API Reference

### Pipeline
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/run` | Trigger a full pipeline run |
| `GET` | `/stream` | SSE stream for live pipeline logs |

### Jobs
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/jobs` | All jobs (last 200) |
| `GET` | `/api/stats` | Dashboard stats |
| `GET` | `/api/analytics` | Extended analytics (board breakdown, send trend) |
| `PATCH` | `/api/job/<id>/status` | Update job status |
| `POST` | `/api/job/<id>/send` | Manually send application email |

### Follow-Ups
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/followup/run` | Trigger follow-up cycle (reply detection + send) |
| `GET` | `/api/followup/stream` | SSE stream for follow-up cycle logs |
| `GET` | `/api/followup/eligible` | List jobs eligible for follow-up |
| `POST` | `/api/job/<id>/followup` | Send follow-up for a specific job |

### SMTP
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/smtp/test` | Test SMTP connection |
| `POST` | `/api/smtp/send-test` | Send a real preview email |

---

## How Follow-Ups Work

1. On any pipeline run (or when you hit `/api/followup/run` manually), the follow-up cycle:
   - Connects to your IMAP inbox and scans for replies from HR addresses in the DB
   - Marks any replied jobs so they won't get a follow-up
   - Sends a short polite follow-up to jobs that were sent `FOLLOW_UP_DAYS` ago with no reply

2. The follow-up email is plain-text, personalized with the company and role name, and goes to the same HR address as the original application.

3. A job gets at most **one** follow-up. Once sent, `follow_up_status` is set to `sent`.

---

## How the Duplicate Guard Works

Before any email send (auto or manual), the mailer checks the DB for jobs where the same HR email was already contacted within `DEDUP_WINDOW_DAYS` (default 30). If found, the send is skipped with a log message. You can override this for manual sends by passing `"force": true` in the POST body to `/api/job/<id>/send`.

---

## How Grouped Sends Work

If the pipeline finds multiple qualified jobs at the same company with the same HR email, it sends **one email** listing all matching roles rather than separate messages. This avoids spamming the same recruiter and increases the chance of a response.

---

## Output Folder Structure

```
output/
└── CompanyName_JobTitle/
    ├── CV.docx                 ← ATS-optimized, customized for this job
    ├── CV.pdf
    ├── CoverLetter.docx        ← Tailored cover letter (addressed to HR if found)
    ├── CoverLetter.pdf
    ├── EMAIL_DRAFT.txt         ← Ready-to-copy email subject + body
    └── CONTACT_INFO.txt        ← HR info, match score, ATS keywords
```

---

## Groq API Usage

When `LLM_SCORING=true`, all AI tasks use `llama-3.3-70b-versatile`:

| Task | Calls per job |
|---|---|
| Company research (if `ENRICH_COMPANY_DATA=true`) | ~1 |
| Job scoring | ~1 |
| Contact extraction | ~1 |
| CV customization | ~1 |
| Cover letter | ~1 |

A typical run with 30 new jobs → 10 qualified → ~60–65 Groq calls. Add multiple keys (`GROQ_API_KEY_1`…`N`) to parallelize and avoid rate-limit delays.

Set `LLM_SCORING=false` to eliminate the scoring and company-research calls entirely (~2 calls saved per job).

---

## Troubleshooting

**SMTP sender not configured**
Fill either the MailerSend or SMTP2GO SMTP fields, then set `SMTP_AUTO_SEND=true`.

**No jobs scraped from LinkedIn**
LinkedIn aggressively rate-limits by IP. Enable proxy rotation (`PROXY_ENABLED=true`, `PROXY_LIST=…`) with residential proxies.

**Follow-ups not firing**
Check `FOLLOW_UP_ENABLED=true` and that `IMAP_HOST` / credentials are set. Run `python follow_up_scheduler.py` directly to see debug output.

**Reply detection missing replies**
IMAP scans the last 500 messages in INBOX. Ensure IMAP is enabled on the mailbox you configured.

**Hunter/Prospeo/search returning no results**
Each API has a monthly quota on the free tier. The waterfall moves to the next provider automatically when one is exhausted, then browser search tries public recruiter/careers pages.

**Keyword scorer giving low scores for good matches**
The keyword scorer is literal — it won't match "React" in your CV to "frontend framework" in a JD. Lower `MIN_MATCH_SCORE` to 40–50 when running in keyword mode, or switch back to `LLM_SCORING=true` for semantic matching.

---

## License

MIT
