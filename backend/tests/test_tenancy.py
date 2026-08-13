"""
Tenant-isolation and credential-encryption guarantees.

These are the load-bearing security invariants of the whole SaaS: one user must
never read another user's rows, secrets must never sit in the DB as plaintext,
and an unscoped query must be impossible. If any of these break, the multi-
tenant model is unsafe — so they are asserted directly.
"""

from __future__ import annotations

import pytest

from autojob.services import crypto
from autojob.services import repository as repo


def test_password_hashing(make_user):
    u = make_user("h@x.com", password="correct-horse")
    assert u.password_hash != "correct-horse"
    assert u.check_password("correct-horse")
    assert not u.check_password("wrong")


def test_same_url_yields_distinct_jobs_per_user(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    ja = repo.insert_job(a.id, {"url": "https://job/1", "title": "Dev"})
    jb = repo.insert_job(b.id, {"url": "https://job/1", "title": "Dev"})
    assert ja and jb and ja.id != jb.id


def test_jobs_are_isolated_between_tenants(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    jb = repo.insert_job(b.id, {"url": "https://job/1", "title": "Dev"})
    assert repo.get_job(a.id, jb.id) is None
    assert len(repo.get_jobs(a.id)) == 0
    assert len(repo.get_jobs(b.id)) == 1


def test_duplicate_url_for_same_user_is_rejected(make_user):
    a = make_user("a@x.com")
    assert repo.insert_job(a.id, {"url": "https://job/1"}) is not None
    assert repo.insert_job(a.id, {"url": "https://job/1"}) is None


def test_unscoped_query_is_refused():
    with pytest.raises(ValueError):
        repo.get_jobs("")


def test_credentials_encrypted_at_rest_and_isolated(make_user, db):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    repo.set_credential(a.id, "groq", "secret-key-A")
    doc = db.conn.user_credentials.find_one({"user_id": a.id})
    assert "secret-key-A" not in doc["ciphertext"]
    assert repo.get_credential(a.id, "groq") == "secret-key-A"
    assert repo.get_credential(b.id, "groq") == ""


def test_crypto_roundtrip(app_context):
    assert crypto.decrypt(crypto.encrypt("hello")) == "hello"
    assert crypto.decrypt("") == ""


def test_settings_autocreate_with_defaults(make_user):
    a = make_user("a@x.com")
    s = repo.get_or_create_settings(a.id)
    assert s.min_match_score == 60
    assert s.remote_only is True
    assert s.auto_send is False
