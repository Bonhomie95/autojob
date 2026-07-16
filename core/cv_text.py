"""
cv_text.py — Turn a CV file into clean, parseable plain text.

Extraction quality is load-bearing: every downstream stage (skills, titles,
years, generated documents) reads this text, so garbage here is garbage that
reaches an employer's inbox.

Two real-world problems this handles:

  Overlapping text layers. Some PDFs draw two versions of a line at nearly
  the same height — an edit layered over the original. pdfplumber's default
  y_tolerance of 3 merges those baselines into one line and sorts the glyphs
  by x, interleaving two strings into mush. A tight y_tolerance keeps them as
  separate lines. The cost is that small-caps headings split across lines, but
  those are page furniture we strip anyway.

  Private-use bullets. Word exports Wingdings bullets into the Unicode
  private-use area (U+F0A7, U+F0FC, …). They carry no meaning to anything
  downstream, so they're normalised to a plain "• ".
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

# Wingdings/Symbol glyphs Word emits into the private-use area. Anything in
# U+F000–U+F0FF is decorative in a CV, so map the common bullets to "•" and
# drop the rest.
_PUA_BULLETS = {"", "", "", "", "", "", ""}
_PUA_RANGE = re.compile(r"[-]")

_RULE_RE = re.compile(r"^[\s_\-=•·—–]{6,}$")
_PAGE_NUM_RE = re.compile(r"^\s*(page\s+\d+|\d+\s*/\s*\d+|\d{1,2})\s*$", re.I)


def _normalise_glyphs(text: str) -> str:
    for bullet in _PUA_BULLETS:
        text = text.replace(bullet, "• ")
    text = _PUA_RANGE.sub(" ", text)
    return text.replace(" ", " ").replace("’", "'")


def _strip_repeated_lines(pages: list[str]) -> list[str]:
    """
    Drop running headers and footers. A short line that appears on most pages
    at the top or bottom is page furniture, not content.
    """
    if len(pages) < 3:
        return pages

    counts: Counter[str] = Counter()
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        for line in lines[:2] + lines[-2:]:
            if len(line) <= 60:
                counts[line] += 1

    threshold = max(2, len(pages) // 2)
    furniture = {line for line, n in counts.items() if n >= threshold}
    if furniture:
        logger.debug(f"[CV] Dropping page furniture: {sorted(furniture)[:4]}")

    cleaned: list[str] = []
    for page in pages:
        kept = [ln for ln in page.split("\n") if ln.strip() not in furniture]
        cleaned.append("\n".join(kept))
    return cleaned


def _clean(text: str) -> str:
    text = _normalise_glyphs(text)
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.rstrip()
        if _RULE_RE.match(stripped.strip()):
            continue
        if _PAGE_NUM_RE.match(stripped.strip()):
            continue
        stripped = re.sub(r"[ \t]{2,}", "  ", stripped)
        out.append(stripped)

    # Collapse runs of blank lines to at most one.
    result = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def _extract_pdf(path: str) -> str:
    import pdfplumber  # type: ignore

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # y_tolerance=1 keeps overlapping text layers on separate lines
            # instead of interleaving them into unusable text.
            text = page.extract_text(y_tolerance=1, x_tolerance=1.5) or ""
            pages.append(text)

    pages = _strip_repeated_lines(pages)
    return "\n".join(pages)


def _extract_docx(path: str) -> str:
    from docx import Document  # type: ignore

    doc = Document(path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    # Skills and experience are often laid out in tables, which
    # doc.paragraphs skips entirely.
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append("  ".join(cells))
    return "\n".join(parts)


def extract_cv_text(cv_path: str) -> str:
    """Extract clean plain text from a .pdf, .docx, or .txt CV."""
    ext = Path(cv_path).suffix.lower()
    try:
        if ext == ".docx":
            raw = _extract_docx(cv_path)
        elif ext == ".pdf":
            raw = _extract_pdf(cv_path)
        else:
            raw = Path(cv_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"[CV] Extraction failed for {cv_path}: {e}")
        return ""

    cleaned = _clean(raw)
    if not cleaned:
        logger.error(f"[CV] No text extracted from {cv_path}")
    else:
        logger.info(f"[CV] Extracted {len(cleaned)} chars from {Path(cv_path).name}")
    return cleaned


def find_emails(text: str) -> list[str]:
    """All distinct email addresses in the text, in order of appearance."""
    seen: list[str] = []
    for match in re.finditer(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", text):
        email = match.group(0).lower()
        if email not in seen:
            seen.append(email)
    return seen
