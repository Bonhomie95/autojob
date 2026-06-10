# 🎯 Job Hunter

> Automated job discovery, AI-powered scoring, HR contact extraction, per-company application package generation, auto-send with duplicate guard, follow-up scheduling, and reply detection — all in a clean Flask dashboard.

---

## What It Does

Job Hunter runs a full pipeline on demand:

1. **Scrapes** 9 job boards (LinkedIn, Indeed, RemoteOK, WeWorkRemotely, Jobicy, Remotive, Arbeitnow, HackerNews, Google Jobs) for your target roles
2. **Researches** each company with a brief AI summary — so your cover letters mention real things about the company, not just generic fit language
3. **Scores** every job against your CV using Groq (`llama-3.3-70b-versatile`) — 0–100 match score
4. **Filters** out blacklisted keywords, low-scoring jobs, and duplicates automatically
5. **Extracts HR contacts** — name, title, email, application URL from the posting (+ optional Hunter.io enrichment)
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
| AI (scoring + generation) | Groq API — `llama-3.3-70b-versatile` |
| Document generation | `python-docx`, `reportlab`, LibreOffice (PDF) |
| Database | SQLite (via stdlib `sqlite3`) |
| Web UI | Flask 3 + vanilla JS (SSE for live logs) |
| Config | `.env` via `python-dotenv` |
| HR enrichment (optional) | Hunter.io API |
| Reply detection | IMAP (Gmail or any provider) |

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/yourname/job-hunter.git
cd job-hunter
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
GROQ_API_KEY=your_groq_key_here
CANDIDATE_NAME=Your Full Name
CANDIDATE_EMAIL=you@example.com
TARGET_ROLES=React Native Developer,Full Stack Engineer,Backend Engineer
```

Get a free Groq key at: https://console.groq.com

### 4. Configure Gmail SMTP (recommended)

Gmail is the most reliable SMTP option — port 587 is almost never blocked by ISPs.

1. Enable 2-Step Verification on your Google account
2. Go to **myaccount.google.com → Security → App Passwords**
3. Generate a password for "Mail"
4. Add to `.env`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx   # 16-char App Password
SMTP_FROM=you@gmail.com
SMTP_TLS=true
SMTP_AUTO_SEND=true
```

> **Why not Namecheap cPanel?** Ports 465 and 25 are commonly blocked by Nigerian and other ISPs. Port 587 may work but requires `SMTP_TLS=true`. Gmail is simpler and more reliable.

### 5. Configure Gmail IMAP for reply detection (optional but recommended)

Reply detection uses the same Gmail App Password:

```env
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
# IMAP_USER and IMAP_PASSWORD default to your SMTP credentials
```

Make sure IMAP is enabled in Gmail: **Gmail Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP**.

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

## Configuration Reference (`.env`)

### Core

| Variable | Description | Default |
|---|---|---|
| `GROQ_API_KEY` | **Required.** Groq API key (or `GROQ_API_KEY_1`…`N` for pool) | — |
| `HUNTER_API_KEY` | Hunter.io for HR email discovery (25/month free) | — |
| `TARGET_ROLES` | Comma-separated job titles to search | — |
| `KEYWORDS` | Keywords to prioritize in matching | — |
| `BLACKLIST_KEYWORDS` | Jobs containing these are auto-skipped | `internship,unpaid` |
| `MIN_MATCH_SCORE` | Min Groq score (0–100) to generate documents | `60` |
| `ENRICH_COMPANY_DATA` | Fetch company summary for richer personalisation | `true` |
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
| `SMTP_HOST` | SMTP server hostname | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP port (587 = STARTTLS, 465 = SSL) | `587` |
| `SMTP_USER` | SMTP username (usually your email) | — |
| `SMTP_PASSWORD` | SMTP password / Gmail App Password | — |
| `SMTP_FROM` | From address | — |
| `SMTP_TLS` | Enable STARTTLS (required for port 587) | `true` |
| `SMTP_AUTO_SEND` | Send automatically during pipeline run | `false` |
| `SMTP_ATTACH_PDF` | Attach PDF documents | `true` |
| `SMTP_ATTACH_DOCX` | Attach DOCX documents | `false` |
| `SMTP_THROTTLE_SECONDS` | Min seconds between sends | `8` |
| `SMTP_FORMAT` | `plain` or `mixed` (plain+HTML) | `plain` |

### Duplicate Guard

| Variable | Description | Default |
|---|---|---|
| `DEDUP_WINDOW_DAYS` | Days before the same HR address can be contacted again | `30` |

### Follow-Up & Reply Detection

| Variable | Description | Default |
|---|---|---|
| `FOLLOW_UP_ENABLED` | Enable automatic follow-up emails | `true` |
| `FOLLOW_UP_DAYS` | Days after first send before follow-up fires | `6` |
| `IMAP_HOST` | IMAP server for reply detection | `imap.gmail.com` |
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

All AI tasks use `llama-3.3-70b-versatile`:

| Task | Calls per job |
|---|---|
| Company research (if `ENRICH_COMPANY_DATA=true`) | ~1 |
| Job scoring | ~1 |
| Contact extraction | ~1 |
| CV customization | ~1 |
| Cover letter | ~1 |

A typical run with 30 new jobs → 10 qualified → ~60–65 Groq calls. Add multiple keys (`GROQ_API_KEY_1`…`N`) to parallelize and avoid rate-limit delays.

---

## Troubleshooting

**SMTP times out on port 465 / 25**
Your ISP is blocking those ports (common in Nigeria, some other regions). Switch to Gmail SMTP on port 587 with `SMTP_TLS=true`.

**SMTP connection refused on port 587**
Run `nc -zv smtp.gmail.com 587` in your terminal to confirm port is reachable. If it fails, use a VPN.

**No jobs scraped from LinkedIn**
LinkedIn aggressively rate-limits by IP. Enable proxy rotation (`PROXY_ENABLED=true`, `PROXY_LIST=…`) with residential proxies.

**Follow-ups not firing**
Check `FOLLOW_UP_ENABLED=true` and that `IMAP_HOST` / credentials are set. Run `python follow_up_scheduler.py` directly to see debug output.

**Reply detection missing replies**
IMAP scans the last 500 messages in INBOX. Ensure IMAP is enabled in Gmail settings and that the App Password has IMAP scope.

---

## License

MIT
