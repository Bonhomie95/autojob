"""CV validation, storage, and tenant isolation of documents & credentials."""

from __future__ import annotations

import pytest

from autojob.services import cv_service, storage
from autojob.services import runtime_config as rc

# Minimal valid signatures.
PDF_BYTES = b"%PDF-1.4\n% minimal\n" + b"x" * 200
DOCX_BYTES = b"PK\x03\x04" + b"y" * 200
TXT_BYTES = b"Jane Dev\njane@x.com\nPython, Flask"


def test_validate_rejects_wrong_extension(app_context):
    with pytest.raises(cv_service.CvValidationError):
        cv_service.validate_upload("resume.exe", PDF_BYTES, 10_000_000)


def test_validate_rejects_extension_content_mismatch(app_context):
    # .pdf extension but not a PDF payload
    with pytest.raises(cv_service.CvValidationError):
        cv_service.validate_upload("resume.pdf", b"not a pdf", 10_000_000)


def test_validate_rejects_oversize(app_context):
    with pytest.raises(cv_service.CvValidationError):
        cv_service.validate_upload("resume.pdf", PDF_BYTES, max_bytes=10)


def test_validate_accepts_good_files(app_context):
    assert cv_service.validate_upload("cv.pdf", PDF_BYTES, 10_000_000) == ".pdf"
    assert cv_service.validate_upload("cv.docx", DOCX_BYTES, 10_000_000) == ".docx"
    assert cv_service.validate_upload("cv.txt", TXT_BYTES, 10_000_000) == ".txt"


def test_store_and_active_cv_is_tenant_scoped(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    doc = cv_service.store_cv(a.id, "cv.pdf", PDF_BYTES, 10_000_000)
    assert doc.is_active
    assert cv_service.active_cv(a.id).id == doc.id
    assert cv_service.active_cv(b.id) is None  # isolated


def test_storage_key_is_tenant_prefixed():
    key = storage.cv_key("user123", "deadbeef", ".pdf")
    assert key == "cv/user123/deadbeef.pdf"


def test_uploading_new_cv_deactivates_previous(make_user):
    a = make_user("a@x.com")
    first = cv_service.store_cv(a.id, "one.pdf", PDF_BYTES, 10_000_000)
    second = cv_service.store_cv(a.id, "two.txt", TXT_BYTES, 10_000_000)
    assert cv_service.active_cv(a.id).id == second.id
    assert second.id != first.id


def test_runtime_config_defaults_and_managed_fallback(make_user, app):
    a = make_user("a@x.com")
    app.config["MANAGED_GROQ_KEYS"] = "managed-key-1,managed-key-2"
    cfg = rc.build_runtime_config(a.id)
    assert cfg.min_match_score == 60
    assert cfg.auto_send is False
    # user set no groq key → managed pool is used
    assert cfg.groq_keys == ["managed-key-1", "managed-key-2"]
    assert cfg.managed_llm is True


def test_runtime_config_prefers_user_key(make_user, app):
    from autojob.services import repository as repo

    a = make_user("a@x.com")
    app.config["MANAGED_GROQ_KEYS"] = "managed-key"
    repo.set_credential(a.id, "groq", "user-key-1,user-key-2")
    cfg = rc.build_runtime_config(a.id)
    assert cfg.groq_keys == ["user-key-1", "user-key-2"]
    assert cfg.managed_llm is False
