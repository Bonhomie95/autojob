# 🎯 Job Hunter

> Automated job discovery, AI-powered scoring, HR contact extraction, and per-company application package generation — all in a clean Flask dashboard.

---

## What It Does

Job Hunter runs a full pipeline on demand:

1. **Scrapes** LinkedIn, Indeed, RemoteOK, and WeWorkRemotely for your target roles
2. **Scores** every job against your CV using Groq (`llama-3.3-70b-versatile`) — 0–100 match score
3. **Filters** out blacklisted keywords, low-scoring jobs, and duplicates automatically
4. **Extracts HR contacts** — name, title, email, application URL from the job posting (+ optional Hunter.io enrichment)
5. **Generates** a fully customized CV + cover letter per company, ATS-optimized for that specific job description
6. **Outputs** everything organized per company into the `output/` folder

```
output/
├── Microsoft_Senior_React_Native_Developer/
│   ├── CV.docx
│   ├── CV.pdf
│   ├── CoverLetter.docx
│   ├── CoverLetter.pdf
│   └── CONTACT_INFO.txt        ← HR name, email, apply link, match analysis
├── Stripe_Backend_Engineer/
│   └── ...
└── ...
```

You handle the actual sending. The tool hands you everything you need per company.

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
# Ubuntu / Debian
sudo apt install libreoffice

# macOS
brew install --cask libreoffice

# Windows — download from libreoffice.org
# If not installed, the app falls back to a reportlab PDF renderer
```

### 3. Configure your `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

```env
GROQ_API_KEY=your_groq_key_here
CANDIDATE_NAME=Your Full Name
CANDIDATE_EMAIL=you@example.com
TARGET_ROLES=React Native Developer,Full Stack Engineer,Backend Engineer
```

Get your free Groq API key at: https://console.groq.com

### 4. Add your CV

Drop your CV into the `input/` folder:

```
input/
└── YourCV.docx        # or .pdf or .txt
```

The app picks the first file it finds (`*.docx` → `*.pdf` → `*.txt`). You can also upload via the web UI.

### 5. Run

```bash
python app.py
```

Open: **http://localhost:5000**

---

## Configuration Reference (`.env`)

| Variable | Description | Default |
|---|---|---|
| `GROQ_API_KEY` | **Required.** Groq API key | — |
| `HUNTER_API_KEY` | Optional. Hunter.io for HR email discovery (25 free/month) | — |
| `TARGET_ROLES` | Comma-separated job titles to search for | — |
| `KEYWORDS` | Keywords to prioritize in matching | — |
| `BLACKLIST_KEYWORDS` | Jobs containing these are auto-skipped | `internship,unpaid` |
| `MIN_SALARY` | Minimum salary (where listed) | `0` |
| `MAX_SALARY` | Maximum salary | `999999` |
| `SALARY_CURRENCY` | Currency for salary filter | `USD` |
| `EXPERIENCE_LEVEL` | `junior`, `mid`, `senior` | `mid,senior` |
| `REMOTE_ONLY` | Only scrape remote jobs | `true` |
| `TARGET_COUNTRIES` | Comma-separated countries (when not remote-only) | `Remote` |
| `CANDIDATE_NAME` | Your full name — appears on all documents | — |
| `CANDIDATE_EMAIL` | Your email | — |
| `CANDIDATE_PHONE` | Your phone | — |
| `CANDIDATE_LOCATION` | e.g. `Lagos, Nigeria (Open to Remote)` | — |
| `CANDIDATE_LINKEDIN` | LinkedIn URL | — |
| `CANDIDATE_GITHUB` | GitHub URL | — |
| `SCRAPE_LINKEDIN` | Enable LinkedIn scraping | `true` |
| `SCRAPE_INDEED` | Enable Indeed scraping | `true` |
| `SCRAPE_REMOTEOK` | Enable RemoteOK scraping | `true` |
| `SCRAPE_WEWORKREMOTELY` | Enable WeWorkRemotely scraping | `true` |
| `MAX_JOBS_PER_BOARD` | Max jobs fetched per source per run | `50` |
| `MIN_MATCH_SCORE` | Minimum Groq score (0–100) to generate documents | `60` |
| `OUTPUT_DIR` | Where output folders are written | `output` |
| `INPUT_DIR` | Where the CV is read from | `input` |
| `FLASK_PORT` | Web UI port | `5000` |
| `TIMEZONE` | Your timezone | `Africa/Lagos` |

---

## Web UI Pages

| Page | URL | Purpose |
|---|---|---|
| Dashboard | `/` | Stats, trigger runs, upload CV, live progress log |
| Jobs | `/jobs` | Full job table with filters — HR contacts, score, salary |
| Job Detail | `/job/<id>` | Full description, contacts, download documents, mark applied |

---

## Output Folder Structure

Each qualified job gets its own folder inside `output/`:

```
output/
└── CompanyName_JobTitle/
    ├── CV.docx                 ← ATS-optimized CV, customized for this job
    ├── CV.pdf                  ← PDF version
    ├── CoverLetter.docx        ← Tailored cover letter (addressed to HR if found)
    ├── CoverLetter.pdf         ← PDF version
    └── CONTACT_INFO.txt        ← HR name/email, application link, match score + analysis
```

**CONTACT_INFO.txt** contains everything you need to send your application manually:

```
JOB: Senior React Native Developer at Stripe
URL: https://stripe.com/jobs/listing/...
MATCH SCORE: 87/100

── CONTACT INFO ──────────────────────────
HR Name:          Sarah Chen
HR Title:         Senior Technical Recruiter
HR Email:         s.chen@stripe.com
Application URL:  https://stripe.com/jobs/apply/...

── MATCH ANALYSIS ────────────────────────
Match Reasons: React Native, TypeScript, payments experience
Gaps:          Rust knowledge
ATS Keywords:  React Native, Expo, TypeScript, REST API, CI/CD
```

---

## General Purpose Usage

This tool is built for any candidate, not just one person. To use it for a different person:

1. Edit `.env` with their candidate info and target roles
2. Drop their CV into `input/`
3. Run the pipeline

The AI generates completely fresh, customized documents based on whatever CV is in `input/`.

---

## Notes on Scraping

- **LinkedIn**: Uses the guest job search API (no login required). Rate-limited automatically.
- **Indeed**: Scrapes public search results. Anti-bot measures may reduce results intermittently.
- **RemoteOK**: Uses their official public JSON API — most reliable source.
- **WeWorkRemotely**: Uses their official RSS feeds — also very reliable.
- All scrapers include polite delays (1.5–3.5s between requests) and retry logic.

---

## Groq API Usage

All AI tasks use Groq's `llama-3.3-70b-versatile` model:
- Job scoring: ~1 API call per new job
- Contact extraction: ~1 API call per qualified job
- CV customization: ~1 API call per qualified job
- Cover letter: ~1 API call per qualified job

On the free tier (30 req/min), a typical run of 30 new jobs → 10 qualified → ~40–50 Groq calls.

---

## Roadmap

- [ ] Scheduler (run every 6/12/24h automatically)
- [ ] Email digest of new qualified jobs
- [ ] LinkedIn authenticated scraping for better results
- [ ] Export all jobs to CSV/Excel
- [ ] Bulk mark-as-applied

---

## License

MIT
