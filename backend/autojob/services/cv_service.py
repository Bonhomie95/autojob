"""
Tenant-scoped CV handling: validate, store, parse, cache.

Reuses the proven parser in ``core/cv_profile.py`` but keeps every side effect
tenant-scoped: files land under the user's storage prefix, parsed profiles and
ambiguity choices are cached per user in the SaaS DB (never the legacy global
one), and the parser's ``.env`` contact fallback is neutralised so one
deployer's personal details can't bleed into another user's profile.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from ..models import CvDocument
from . import repository as repo
from . import storage

logger = logging.getLogger(__name__)

ALLOWED_EXT = {".pdf", ".docx", ".txt"}

# Magic-byte signatures — never trust the filename's extension alone.
_MAGIC = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),  # docx is a zip container
}


class CvValidationError(ValueError):
    pass


def validate_upload(filename: str, data: bytes, max_bytes: int) -> str:
    """Return the validated lowercase extension or raise CvValidationError."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise CvValidationError("Only PDF, DOCX, or TXT files are allowed.")
    if not data:
        raise CvValidationError("The file is empty.")
    if len(data) > max_bytes:
        raise CvValidationError("File is too large.")
    sigs = _MAGIC.get(ext)
    if sigs and not any(data.startswith(s) for s in sigs):
        raise CvValidationError(f"File doesn't look like a valid {ext[1:].upper()}.")
    if ext == ".txt":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                data.decode("latin-1")
            except UnicodeDecodeError as exc:
                raise CvValidationError("Text file isn't readable text.") from exc
    return ext


def store_cv(user_id: str, filename: str, data: bytes, max_bytes: int) -> CvDocument:
    """Validate, store, and register a CV; make it the active one."""
    ext = validate_upload(filename, data, max_bytes)
    digest = storage.content_hash(data)
    key = storage.cv_key(user_id, digest, ext)

    storage.get_storage().put(key, data)

    # Deactivate previous CVs, then insert/activate this one.
    repo.deactivate_cv_documents(user_id)

    existing = repo.find_cv_document(user_id, digest)
    if existing:
        repo.update_cv_document(user_id, existing.id, is_active=True, filename=filename)
        existing.is_active = True
        existing.filename = filename
        doc = existing
    else:
        doc = CvDocument(
            user_id=user_id, filename=filename, storage_key=key,
            content_hash=digest, size_bytes=len(data), is_active=True,
        )
        repo.insert_cv_document(doc)
    logger.info("Stored CV", extra={"user_id": user_id, "hash": digest[:12]})
    return doc


def active_cv(user_id: str) -> CvDocument | None:
    return repo.get_active_cv_document(user_id)


def remove_cv(user_id: str) -> bool:
    """Remove the user's active CV — the stored file and its document record.

    Cached parsed profiles/ambiguity choices for that file's hash are left in
    place (harmless, and a nice cache hit if the same file gets re-uploaded).
    Returns False if there was nothing to remove.
    """
    doc = active_cv(user_id)
    if not doc:
        return False
    with contextlib.suppress(OSError):
        storage.get_storage().delete(doc.storage_key)
    repo.delete_cv_document(user_id, doc.id)
    logger.info("Removed CV", extra={"user_id": user_id, "hash": doc.content_hash[:12]})
    return True


@contextlib.contextmanager
def _neutralise_env_contacts():
    """
    Stop the core parser's ``.env`` CANDIDATE_* fallback from injecting the
    deployer's contact details into a tenant's profile. Blank those attrs on
    the legacy config singleton for the duration of the parse (this SaaS
    process only), then restore them.
    """
    from config import config as legacy_config
    from core.cv_profile import CONTACT_FIELDS

    saved = {}
    for env_key in CONTACT_FIELDS.values():
        saved[env_key] = getattr(legacy_config, env_key, "")
        with contextlib.suppress(Exception):
            setattr(legacy_config, env_key, "")
    try:
        yield
    finally:
        for env_key, value in saved.items():
            with contextlib.suppress(Exception):
                setattr(legacy_config, env_key, value)


def get_profile(user_id: str, force: bool = False) -> dict | None:
    """
    Parse (or load the cached parse of) the user's active CV, applying their
    saved ambiguity choices. Returns None if the user has no CV.
    """
    doc = active_cv(user_id)
    if not doc:
        return None

    from core.cv_profile import build_profile, resolve_contacts
    from core.cv_text import extract_cv_text

    choices = repo.load_cv_choices(user_id, doc.content_hash)

    cached = None if force else repo.load_cv_profile(user_id, doc.content_hash)
    if cached:
        with _neutralise_env_contacts():
            return resolve_contacts(cached, choices)

    ext = Path(doc.filename).suffix.lower()
    tmp = storage.get_storage().open_tempfile(doc.storage_key, ext)
    try:
        text = extract_cv_text(tmp)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)

    if not text.strip():
        return {
            "issues": ["BLOCK: CV file produced no extractable text"],
            "skills": [], "titles": [], "projects": [], "experience": [],
            "contact_options": {}, "ambiguous_fields": [],
        }

    profile = build_profile(text)
    profile["source_file"] = doc.filename
    profile["cv_hash"] = doc.content_hash
    repo.save_cv_profile(user_id, doc.content_hash, doc.filename, profile)

    with _neutralise_env_contacts():
        return resolve_contacts(profile, choices)
