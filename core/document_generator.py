import os
import re
import sys
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from core.groq_client import chat_json
from config import config

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Theme
# ──────────────────────────────────────────────────────────
ACCENT  = RGBColor(0x1A, 0x56, 0xDB)
DARK    = RGBColor(0x0F, 0x17, 0x2A)
MID     = RGBColor(0x37, 0x41, 0x51)
GRAY    = RGBColor(0x6B, 0x72, 0x80)
RULE_BLUE  = "1A56DB"
RULE_LIGHT = "DBEAFE"


# ──────────────────────────────────────────────────────────
# Groq Prompts
# ──────────────────────────────────────────────────────────

CV_SYSTEM = """You are an expert CV writer and ATS optimization specialist.

ABSOLUTE RULES — violation means the output is wrong:
1. If "FEATURED PROJECTS FROM THIS CV" is provided in the input, you MUST use ONLY those projects — copy names EXACTLY, never rename, never invent new ones
2. If no projects section is found, use experience bullets as highlights instead — do NOT invent project names
3. Keep all company names and dates EXACTLY as in the base CV
4. Reorder projects by relevance to the job (most relevant first), max 4
5. Weave ATS keywords naturally into bullets — do not force them awkwardly

Return ONLY valid JSON, no markdown fences, no explanation:
{
  "profile_summary": "3-4 sentence tailored profile",
  "core_skills": {
    "Frontend": ["skill1"],
    "Mobile": [],
    "Backend": [],
    "Web3": [],
    "AI / ML": [],
    "DevOps": [],
    "Tooling": []
  },
  "experience": [
    {
      "title": "Job Title",
      "company": "Company Name",
      "period": "YYYY – Present",
      "bullets": ["achievement bullet 1", "achievement bullet 2"]
    }
  ],
  "projects": [
    {
      "name": "EXACT project name from CV — never invent",
      "stack": "Tech stack as listed in CV",
      "bullets": ["tailored achievement bullet 1", "tailored achievement bullet 2"]
    }
  ],
  "ats_keywords_used": ["kw1", "kw2"]
}"""

CL_SYSTEM = """You are an expert cover letter writer. Must NOT sound generic.

Return ONLY valid JSON:
{
  "opening_paragraph": "...",
  "body_paragraph_1": "...",
  "body_paragraph_2": "...",
  "closing_paragraph": "..."
}

Rules:
- Address to the HR name if provided, otherwise 'Hiring Manager'
- Reference the specific company and role
- Connect 2-3 specific achievements to job requirements
- Under 400 words, professional but human tone"""

EMAIL_SYSTEM = """Write a short cold job application email.
Return ONLY this exact JSON — no other text, no markdown:
{"subject": "your subject line", "body": "your email body"}

Body rules (STRICT 150 word max):
- Line 1: Who you are and the role (one sentence, no filler)
- 2 short paragraphs: concrete achievements matching the job
- Final line: ask for a call
- Sign off with name, email, phone, LinkedIn using literal \\n
- Confident, direct tone. No "excited to" or "thrilled"."""


# ──────────────────────────────────────────────────────────
# Main Entry
# ──────────────────────────────────────────────────────────

def generate_documents(job: dict, cv_text: str, contact: dict, score_data: dict, output_dir: str) -> bool:
    company = _safe_dirname(job.get("company", "Company"))
    role    = _safe_dirname(job.get("title", "Role"))
    folder  = Path(output_dir) / f"{company}_{role}"
    folder.mkdir(parents=True, exist_ok=True)

    logger.info(f"[DocGen] Generating docs for {company} — {role}")

    cv_data = _gen_cv(cv_text, job, score_data)
    if not cv_data:
        logger.warning("[DocGen] No CV data from Groq, skipping")
        return False

    cl_data = _gen_cl(cv_text, job, contact)
    if not cl_data:
        logger.warning("[DocGen] No cover letter data from Groq, skipping")
        return False

    email_data = _gen_email(cv_text, job, contact)

    cv_docx = folder / "CV.docx"
    cl_docx = folder / "CoverLetter.docx"

    _write_cv(cv_data, str(cv_docx))
    _write_cl(cl_data, job, contact, str(cl_docx))
    _write_email(email_data, job, contact, score_data, folder)

    _to_pdf(str(cv_docx), str(folder))
    _to_pdf(str(cl_docx), str(folder))

    logger.info(f"[DocGen] Done → {folder}")
    return True


# ──────────────────────────────────────────────────────────
# Groq generators
# ──────────────────────────────────────────────────────────

def _extract_projects_from_cv(cv_text: str) -> str:
    """
    Extract FEATURED PROJECTS from CV text.
    Priority:
      1. CANDIDATE_PROJECTS in .env (explicit override — most reliable)
      2. Auto-detect FEATURED PROJECTS / PROJECTS section in cv_text
      3. Fallback: find numbered items like "1. PulseQuiz"
    Returns a string injected into the Groq prompt so it always
    has the full project list regardless of cv_text truncation.
    """
    # ── 1. env override takes priority
    if config.CANDIDATE_PROJECTS:
        found: list[str] = []
        for name in config.CANDIDATE_PROJECTS:
            # Try to find the project block in cv_text
            pattern = rf"(?:^|\n)([^\n]*{re.escape(name)}[^\n]*(?:\n(?!\d+[.)]\s|\n).+){{0,8}})"
            m = re.search(pattern, cv_text, re.IGNORECASE)
            found.append(m.group(0).strip() if m else f"- {name}")
        return "\n\n".join(found)

    # ── 2. Find projects section heading
    section_patterns = [
        r"FEATURED PROJECTS(.*?)(?=EDUCATION|CERTIFICATIONS|REFERENCES|$)",
        r"PROJECTS(.*?)(?=EDUCATION|CERTIFICATIONS|REFERENCES|$)",
        r"KEY PROJECTS(.*?)(?=EDUCATION|CERTIFICATIONS|REFERENCES|$)",
    ]
    for pat in section_patterns:
        m = re.search(pat, cv_text, re.IGNORECASE | re.DOTALL)
        if m:
            raw = m.group(1).strip()
            if len(raw) > 50:
                return raw[:3000]

    # ── 3. Fallback: numbered items
    project_lines: list[str] = []
    capturing = False
    for line in cv_text.split("\n"):
        s = line.strip()
        if re.match(r"^[1-9][.)]\s+\w", s):
            capturing = True
        if capturing:
            project_lines.append(s)
            if len(project_lines) > 80:
                break

    return "\n".join(project_lines) if project_lines else ""


def _gen_cv(cv_text: str, job: dict, score_data: dict) -> Optional[dict]:
    ats_kws = ", ".join(score_data.get("ats_keywords", []))

    # Pre-extract projects so they survive cv_text truncation
    projects_block = _extract_projects_from_cv(cv_text)
    projects_section = (
        f"\n\nFEATURED PROJECTS FROM THIS CV (you MUST use ONLY these — exact names):\n{projects_block}"
        if projects_block
        else "\n\n(No projects section found in CV — use experience bullets as project highlights)"
    )

    user = (
        f"BASE CV:\n{cv_text[:1800]}"
        f"{projects_section}\n\n"
        f"TARGET ROLE: {job.get('title','')} at {job.get('company','')}\n"
        f"ATS KEYWORDS: {ats_kws}\n\n"
        f"JOB DESCRIPTION:\n{job.get('description','')[:1200]}"
    )
    result = chat_json(CV_SYSTEM, user, temperature=0.3, max_tokens=2000)
    return result if isinstance(result, dict) else None


def _gen_cl(cv_text: str, job: dict, contact: dict) -> Optional[dict]:
    hr = contact.get("hr_name") or "Hiring Manager"
    user = (
        f"CANDIDATE CV:\n{cv_text[:1200]}\n\n"
        f"ROLE: {job.get('title','')} at {job.get('company','')}\n"
        f"ADDRESS TO: {hr}\n\n"
        f"JOB DESCRIPTION:\n{job.get('description','')[:1500]}"
    )
    result = chat_json(CL_SYSTEM, user, temperature=0.4, max_tokens=800)
    return result if isinstance(result, dict) else None


def _gen_email(cv_text: str, job: dict, contact: dict) -> dict:
    hr = contact.get("hr_name") or "Hiring Manager"
    user = (
        f"CANDIDATE: {config.CANDIDATE_NAME} | {config.CANDIDATE_EMAIL} | "
        f"{config.CANDIDATE_PHONE} | {config.CANDIDATE_LINKEDIN}\n\n"
        f"CV HIGHLIGHTS:\n{cv_text[:800]}\n\n"
        f"ROLE: {job.get('title','')} at {job.get('company','')}\n"
        f"ADDRESS TO: {hr}\n\n"
        f"JOB DESCRIPTION:\n{job.get('description','')[:1000]}"
    )
    result = chat_json(EMAIL_SYSTEM, user, temperature=0.3, max_tokens=1000)
    if isinstance(result, dict) and "subject" in result and "body" in result:
        return result
    return {
        "subject": f"Application: {job.get('title','Role')} — {config.CANDIDATE_NAME}",
        "body": (
            f"Hi {hr},\n\nI'm a Full-Stack Engineer with 5+ years experience applying for "
            f"the {job.get('title','')} role at {job.get('company','')}.\n\n"
            f"My CV is attached. Happy to jump on a call.\n\n"
            f"Best,\n{config.CANDIDATE_NAME}\n{config.CANDIDATE_EMAIL}\n"
            f"{config.CANDIDATE_PHONE}\n{config.CANDIDATE_LINKEDIN}"
        ),
    }


# ──────────────────────────────────────────────────────────
# CV DOCX
# ──────────────────────────────────────────────────────────

def _write_cv(data: dict, path: str):
    doc = Document()
    for sec in doc.sections:
        sec.top_margin    = Cm(1.8)
        sec.bottom_margin = Cm(1.8)
        sec.left_margin   = Cm(1.8)
        sec.right_margin  = Cm(1.8)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"  # type: ignore[attr-defined]
    normal.font.size = Pt(10)     # type: ignore[attr-defined]

    _cv_header(doc)

    _heading(doc, "PROFILE")
    p = doc.add_paragraph(data.get("profile_summary", ""))
    p.runs[0].font.color.rgb = MID  # type: ignore[attr-defined]
    p.runs[0].font.size = Pt(10)    # type: ignore[attr-defined]
    p.paragraph_format.space_after = Pt(4)

    _heading(doc, "CORE SKILLS")
    for cat, skills in (data.get("core_skills") or {}).items():
        if not skills:
            continue
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        rc = p.add_run(f"{cat}  ")
        rc.bold = True
        rc.font.size = Pt(9)  # type: ignore[attr-defined]
        rc.font.color.rgb = ACCENT  # type: ignore[attr-defined]
        rs = p.add_run("  ·  ".join(skills))
        rs.font.size = Pt(9)  # type: ignore[attr-defined]
        rs.font.color.rgb = MID  # type: ignore[attr-defined]

    _heading(doc, "PROFESSIONAL EXPERIENCE")
    for exp in (data.get("experience") or []):
        _exp_block(doc, exp)

    _heading(doc, "FEATURED PROJECTS")
    for proj in (data.get("projects") or [])[:4]:
        _proj_block(doc, proj)

    _heading(doc, "EDUCATION")
    for edu in _get_education():
        p = doc.add_paragraph()
        r1 = p.add_run(edu.get("degree", ""))
        r1.bold = True
        r1.font.color.rgb = DARK  # type: ignore[attr-defined]
        if edu.get("school"):
            r2 = p.add_run(f"  |  {edu['school']}")
            r2.font.color.rgb = MID  # type: ignore[attr-defined]
        p.paragraph_format.space_after = Pt(1)
        if edu.get("year"):
            py = doc.add_paragraph(edu["year"])
            py.runs[0].font.color.rgb = GRAY  # type: ignore[attr-defined]
            py.runs[0].font.size = Pt(9)      # type: ignore[attr-defined]
            py.paragraph_format.space_after = Pt(5)

    doc.save(path)
    logger.info(f"[DocGen] CV saved → {path}")


def _cv_header(doc: Document):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(config.CANDIDATE_NAME.upper())
    r.bold = True
    r.font.size = Pt(22)  # type: ignore[attr-defined]
    r.font.color.rgb = DARK  # type: ignore[attr-defined]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after  = Pt(3)

    roles_str = "  ·  ".join(config.TARGET_ROLES[:3]) if config.TARGET_ROLES else "Full-Stack Engineer"
    pt = doc.add_paragraph(roles_str)
    pt.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pt.runs[0].bold = True
    pt.runs[0].font.size = Pt(10)  # type: ignore[attr-defined]
    pt.runs[0].font.color.rgb = ACCENT  # type: ignore[attr-defined]
    pt.paragraph_format.space_after = Pt(4)

    contacts = [c for c in [
        config.CANDIDATE_PHONE, config.CANDIDATE_EMAIL,
        config.CANDIDATE_LINKEDIN, config.CANDIDATE_GITHUB,
        config.CANDIDATE_LOCATION,
    ] if c]
    pc = doc.add_paragraph("  ·  ".join(contacts))
    pc.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pc.runs[0].font.size = Pt(8.5)  # type: ignore[attr-defined]
    pc.runs[0].font.color.rgb = MID  # type: ignore[attr-defined]
    pc.paragraph_format.space_after = Pt(6)
    _bottom_rule(doc, RULE_BLUE, size=12)


def _heading(doc: Document, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after  = Pt(2)
    pPr = p._p.get_or_add_pPr()
    # Left border
    pBdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "6")
    left.set(qn("w:color"), RULE_BLUE)
    pBdr.append(left)
    pPr.append(pBdr)
    # Left indent
    ind = OxmlElement("w:ind")
    ind.set(qn("w:left"), "120")
    pPr.append(ind)

    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(10.5)  # type: ignore[attr-defined]
    r.font.color.rgb = ACCENT  # type: ignore[attr-defined]
    r.font.all_caps = True     # type: ignore[attr-defined]
    _bottom_rule(doc, RULE_LIGHT, size=4)


def _bottom_rule(doc: Document, color: str, size: int = 4):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after  = Pt(3)
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot = OxmlElement("w:bottom")
    bot.set(qn("w:val"), "single")
    bot.set(qn("w:sz"), str(size))
    bot.set(qn("w:space"), "1")
    bot.set(qn("w:color"), color)
    pBdr.append(bot)
    pPr.append(pBdr)


def _exp_block(doc: Document, exp: dict):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(2)
    rt = p.add_run(exp.get("title", ""))
    rt.bold = True
    rt.font.size = Pt(10.5)  # type: ignore[attr-defined]
    rt.font.color.rgb = DARK  # type: ignore[attr-defined]
    if exp.get("company"):
        rs = p.add_run(f"  ·  {exp['company']}")
        rs.font.color.rgb = MID  # type: ignore[attr-defined]
        rs.bold = True
    if exp.get("period"):
        rd = p.add_run(f"  |  {exp['period']}")
        rd.font.color.rgb = GRAY  # type: ignore[attr-defined]
        rd.font.italic = True  # type: ignore[attr-defined]
        rd.font.size = Pt(9)  # type: ignore[attr-defined]
    for bullet in (exp.get("bullets") or []):
        bp = doc.add_paragraph(style="List Bullet")
        bp.paragraph_format.space_after = Pt(1)
        bp.paragraph_format.left_indent = Inches(0.2)
        rb = bp.add_run(bullet)
        rb.font.color.rgb = MID  # type: ignore[attr-defined]
        rb.font.size = Pt(9.5)  # type: ignore[attr-defined]
    doc.add_paragraph("").paragraph_format.space_after = Pt(2)


def _proj_block(doc: Document, proj: dict):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(2)
    rn = p.add_run(proj.get("name", ""))
    rn.bold = True
    rn.font.color.rgb = DARK  # type: ignore[attr-defined]
    rn.font.size = Pt(10)  # type: ignore[attr-defined]
    if proj.get("stack"):
        rst = p.add_run(f"  —  {proj['stack']}")
        rst.font.color.rgb = ACCENT  # type: ignore[attr-defined]
        rst.font.italic = True  # type: ignore[attr-defined]
        rst.font.size = Pt(9)   # type: ignore[attr-defined]
    for bullet in (proj.get("bullets") or []):
        bp = doc.add_paragraph(style="List Bullet")
        bp.paragraph_format.space_after = Pt(1)
        bp.paragraph_format.left_indent = Inches(0.2)
        rb = bp.add_run(bullet)
        rb.font.color.rgb = MID  # type: ignore[attr-defined]
        rb.font.size = Pt(9.5)  # type: ignore[attr-defined]
    doc.add_paragraph("").paragraph_format.space_after = Pt(2)


# ──────────────────────────────────────────────────────────
# Cover Letter DOCX
# ──────────────────────────────────────────────────────────

def _write_cl(data: dict, job: dict, contact: dict, path: str):
    doc = Document()
    for sec in doc.sections:
        sec.top_margin    = Cm(2.5)
        sec.bottom_margin = Cm(2.5)
        sec.left_margin   = Cm(2.8)
        sec.right_margin  = Cm(2.8)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"  # type: ignore[attr-defined]
    normal.font.size = Pt(11)     # type: ignore[attr-defined]

    # Header
    ph = doc.add_paragraph()
    rh = ph.add_run(config.CANDIDATE_NAME.upper())
    rh.bold = True
    rh.font.size = Pt(16)  # type: ignore[attr-defined]
    rh.font.color.rgb = ACCENT  # type: ignore[attr-defined]
    ph.paragraph_format.space_after = Pt(2)

    contacts = [c for c in [config.CANDIDATE_EMAIL, config.CANDIDATE_PHONE, config.CANDIDATE_LINKEDIN] if c]
    pc = doc.add_paragraph("  |  ".join(contacts))
    pc.runs[0].font.size = Pt(9)  # type: ignore[attr-defined]
    pc.runs[0].font.color.rgb = GRAY  # type: ignore[attr-defined]
    pc.paragraph_format.space_after = Pt(14)
    _bottom_rule(doc, RULE_BLUE)

    doc.add_paragraph(datetime.now().strftime("%B %d, %Y")).paragraph_format.space_after = Pt(14)

    hr_name  = contact.get("hr_name", "")
    hr_title = contact.get("hr_title", "")
    company  = job.get("company", "")
    for line, sp in [(hr_name, 0), (hr_title, 0), (company, 14)]:
        if line:
            px = doc.add_paragraph(line)
            px.paragraph_format.space_after = Pt(sp)

    sal = doc.add_paragraph(f"Dear {hr_name}," if hr_name else "Dear Hiring Manager,")
    sal.paragraph_format.space_after = Pt(12)

    for key in ["opening_paragraph", "body_paragraph_1", "body_paragraph_2", "closing_paragraph"]:
        text = data.get(key, "")
        if text:
            pp = doc.add_paragraph(text)
            pp.paragraph_format.space_after = Pt(10)

    doc.add_paragraph("Sincerely,").paragraph_format.space_after = Pt(20)
    ps = doc.add_paragraph(config.CANDIDATE_NAME)
    ps.runs[0].bold = True
    ps.runs[0].font.color.rgb = DARK  # type: ignore[attr-defined]
    for line in [config.CANDIDATE_EMAIL, config.CANDIDATE_PHONE, config.CANDIDATE_LINKEDIN]:
        if line:
            pl = doc.add_paragraph(line)
            pl.paragraph_format.space_after = Pt(0)
            pl.runs[0].font.color.rgb = GRAY  # type: ignore[attr-defined]

    doc.save(path)
    logger.info(f"[DocGen] Cover letter saved → {path}")


# ──────────────────────────────────────────────────────────
# Education from .env
# ──────────────────────────────────────────────────────────

def _get_education() -> list[dict]:
    """
    Supports multiple education entries via .env:
      CANDIDATE_EDUCATION_1=BSc Computer Science | Lagos State University | 2018 - 2023
      CANDIDATE_EDUCATION_2=AWS Certified Solutions Architect | Amazon | 2022
    Also supports legacy single entry:
      CANDIDATE_DEGREE + CANDIDATE_SCHOOL + CANDIDATE_GRAD_YEAR
    """
    entries: list[dict] = []
    for i in range(1, 10):
        raw = os.getenv(f"CANDIDATE_EDUCATION_{i}", "").strip()
        if not raw:
            break
        parts = [p.strip() for p in raw.split("|")]
        entries.append({
            "degree": parts[0] if parts else raw,
            "school": parts[1] if len(parts) > 1 else "",
            "year":   parts[2] if len(parts) > 2 else "",
        })
    if not entries:
        d = os.getenv("CANDIDATE_DEGREE", "").strip()
        s = os.getenv("CANDIDATE_SCHOOL", "").strip()
        y = os.getenv("CANDIDATE_GRAD_YEAR", "").strip()
        if d or s:
            entries.append({"degree": d, "school": s, "year": y})
    if not entries:
        entries.append({
            "degree": "BSc, Computer Science",
            "school": "Lagos State University, Lagos, Nigeria",
            "year":   "2018 – 2023",
        })
    return entries


# ──────────────────────────────────────────────────────────
# Email Draft
# ──────────────────────────────────────────────────────────

def _write_email(email_data: dict, job: dict, contact: dict, score_data: dict, folder: Path):
    to_email = (
        contact.get("hr_email")
        or contact.get("application_email")
        or "[ EMAIL NOT FOUND — check application URL ]"
    )
    hr_name  = contact.get("hr_name", "") or "Not found"
    hr_title = contact.get("hr_title", "") or ""
    subject  = email_data.get("subject", f"Application: {job.get('title','')} — {config.CANDIDATE_NAME}")
    body     = email_data.get("body", "").replace("\\n", "\n")
    sep      = "─" * 60

    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║                    EMAIL DRAFT                          ║",
        "╚══════════════════════════════════════════════════════════╝",
        "",
        "── SEND TO ───────────────────────────────────────────────",
        f"  TO:       {to_email}",
        f"  HR NAME:  {hr_name}" + (f"  ({hr_title})" if hr_title else ""),
        f"  SUBJECT:  {subject}",
        "",
        "── APPLICATION LINKS ─────────────────────────────────────",
        f"  Job URL:         {job.get('url','—')}",
        f"  Apply URL:       {contact.get('application_url') or job.get('url','—')}",
        f"  App Email (alt): {contact.get('application_email') or '—'}",
        "",
        sep,
        "  EMAIL BODY  (copy from here)",
        sep,
        "",
        body,
        "",
        sep,
        "",
        "── MATCH ANALYSIS ────────────────────────────────────────",
        f"  Score:         {score_data.get('score', 0)}/100",
        f"  Match Reasons: {', '.join(score_data.get('match_reasons', [])) or '—'}",
        f"  Gaps:          {', '.join(score_data.get('gaps', [])) or '—'}",
        f"  ATS Keywords:  {', '.join(score_data.get('ats_keywords', [])) or '—'}",
        f"  Salary Listed: {job.get('salary') or 'Not listed'}",
        f"  Source:        {job.get('source','—')}",
        f"  Generated:     {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    (folder / "EMAIL_DRAFT.txt").write_text("\n".join(lines), encoding="utf-8")


# ──────────────────────────────────────────────────────────
# PDF Conversion — Windows aware
# ──────────────────────────────────────────────────────────

def _find_soffice() -> Optional[str]:
    import shutil
    found = shutil.which("soffice")
    if found:
        return found
    if sys.platform == "win32":
        candidates: list[str] = []
        for base in [r"C:\Program Files", r"C:\Program Files (x86)"]:
            bp = Path(base)
            if bp.exists():
                for d in bp.iterdir():
                    if "libreoffice" in d.name.lower():
                        exe = d / "program" / "soffice.exe"
                        if exe.exists():
                            candidates.insert(0, str(exe))
        for c in [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]:
            if c not in candidates:
                candidates.append(c)
        for c in candidates:
            if Path(c).exists():
                return c
    if sys.platform == "darwin":
        mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if Path(mac).exists():
            return mac
    return None


def _to_pdf(docx_path: str, out_dir: str):
    pdf_path = Path(out_dir) / (Path(docx_path).stem + ".pdf")

    # 1. docx2pdf
    try:
        from docx2pdf import convert  # type: ignore
        convert(docx_path, str(pdf_path))
        if pdf_path.exists():
            logger.info(f"[PDF] docx2pdf → {pdf_path}")
            return
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"[PDF] docx2pdf failed: {e}")

    # 2. LibreOffice
    soffice = _find_soffice()
    if soffice:
        try:
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_path],
                capture_output=True, text=True, timeout=90,
            )
            if result.returncode == 0 and pdf_path.exists():
                logger.info(f"[PDF] LibreOffice → {pdf_path}")
                return
            logger.warning(f"[PDF] LibreOffice stderr: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning("[PDF] LibreOffice timed out")
        except Exception as e:
            logger.warning(f"[PDF] LibreOffice error: {e}")
    else:
        logger.warning("[PDF] LibreOffice not found in PATH or standard locations. "
                       "Install it OR run: pip install docx2pdf (requires MS Word on Windows)")

    # 3. ReportLab
    _to_pdf_reportlab(docx_path, out_dir)


def _to_pdf_reportlab(docx_path: str, out_dir: str):
    try:
        from reportlab.lib.pagesizes import A4            # type: ignore
        from reportlab.lib.styles import getSampleStyleSheet  # type: ignore
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer  # type: ignore
        from reportlab.lib.units import cm                # type: ignore

        pdf_path = str(Path(out_dir) / (Path(docx_path).stem + ".pdf"))
        texts    = [p.text for p in Document(docx_path).paragraphs if p.text.strip()]
        styles   = getSampleStyleSheet()
        story    = []
        for t in texts:
            story.append(Paragraph(t.replace("&", "&amp;").replace("<", "&lt;"), styles["Normal"]))
            story.append(Spacer(1, 0.15 * cm))
        pdf = SimpleDocTemplate(pdf_path, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        pdf.build(story)
        logger.info(f"[PDF] ReportLab → {pdf_path}")
    except Exception as e:
        logger.error(f"[PDF] All methods failed: {e}")


# ──────────────────────────────────────────────────────────
# CV Text Extraction
# ──────────────────────────────────────────────────────────

def extract_cv_text(cv_path: str) -> str:
    ext = Path(cv_path).suffix.lower()
    try:
        if ext == ".docx":
            return "\n".join(p.text for p in Document(cv_path).paragraphs if p.text.strip())
        elif ext == ".pdf":
            import pdfplumber  # type: ignore
            parts: list[str] = []
            with pdfplumber.open(cv_path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            return "\n".join(parts)
        else:
            return Path(cv_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"[CV] Extraction failed: {e}")
        return ""


# ──────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────

def _safe_dirname(name: str) -> str:
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:50]
