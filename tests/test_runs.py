"""
Discovery pipeline: tenant-scoped storage/scoring, and run-trigger auth.

The pipeline logic is tested by calling ``run_discovery`` directly (under the
test app context) with the scraper set monkeypatched to a deterministic fake —
no network, no flakiness. The HTTP trigger is tested for auth + concurrency
guarding.
"""

from __future__ import annotations

import io

from autojob import tasks
from autojob.services import repository as repo


class _FakeScraper:
    name = "FakeBoard"

    def __init__(self, jobs):
        self._jobs = jobs

    def scrape(self, queries, location):
        return self._jobs


def _upload_cv(client):
    return client.post(
        "/cv/upload",
        data={"cv": (io.BytesIO(open("input/Amos.pdf", "rb").read()), "Amos.pdf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _patch_engine(monkeypatch, contacts: dict[str, dict], docs_ok: bool = True):
    """
    Replace the engine's network-bound calls with deterministic fakes.

    ``contacts`` maps a job URL to the contact dict extract_contacts should
    return; missing URLs return {} (no contact). generate_documents writes a
    minimal email.json into a per-job folder and returns (docs_ok, folder).
    """
    import core.contact_extractor as ce
    import core.document_generator as dg

    monkeypatch.setattr(ce, "extract_contacts", lambda job: contacts.get(job.get("url"), {}))

    def _gen(job, profile, contact, score_data, out_root):
        import json
        import os
        folder = os.path.join(out_root, job.get("company", "co").replace(" ", "_"))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "email.json"), "w") as fh:
            json.dump({"to": contact.get("hr_email", ""), "subject": "Hi", "body": "Body"}, fh)
        return (docs_ok, folder)

    monkeypatch.setattr(dg, "generate_documents", _gen)


def test_discovery_stores_and_scores_scoped_to_user(client, make_user, monkeypatch):
    # A user with a real parsed CV.
    client.post("/auth/register", data={
        "name": "Amos", "email": "amos@x.com", "password": "secret123",
        "confirm": "secret123", "accept_terms": "y"})
    _upload_cv(client)

    from autojob.extensions import db
    from autojob.models import User
    uid = db.session.scalar(db.select(User.id).where(User.email == "amos@x.com"))

    jobs = [
        {"url": "https://board/1", "title": "Full-Stack Developer",
         "company": "Acme", "description": "React Node Python developer role",
         "source": "FakeBoard"},
        {"url": "https://board/2", "title": "DevOps Engineer",
         "company": "Globex", "description": "AWS Docker CI/CD pipeline engineer",
         "source": "FakeBoard"},
    ]
    monkeypatch.setattr(tasks, "_scrapers", lambda: [_FakeScraper(jobs)])
    _patch_engine(monkeypatch, contacts={})  # no contacts → no network, no docs

    result = tasks.run_discovery(uid, repo.start_run(uid), max_per_board=40)
    assert result["found"] == 2

    stored = repo.get_jobs(uid)
    assert len(stored) == 2
    assert {j.company for j in stored} == {"Acme", "Globex"}

    # Another user sees none of them.
    other = make_user("other@x.com")
    assert repo.get_jobs(other.id) == []


def test_documents_only_generated_when_contact_found(client, app_context, monkeypatch):
    client.post("/auth/register", data={
        "name": "Amos", "email": "amos@x.com", "password": "secret123",
        "confirm": "secret123", "accept_terms": "y"})
    _upload_cv(client)
    from autojob.extensions import db
    from autojob.models import User
    uid = db.session.scalar(db.select(User.id).where(User.email == "amos@x.com"))

    # Lower the bar so both jobs qualify regardless of keyword overlap.
    repo.get_or_create_settings(uid).min_match_score = 0
    db.session.commit()

    jobs = [
        {"url": "https://b/withcontact", "title": "Full-Stack Developer",
         "company": "Acme", "description": "Python React role", "source": "F"},
        {"url": "https://b/nocontact", "title": "Backend Developer",
         "company": "Globex", "description": "Python role", "source": "F"},
    ]
    monkeypatch.setattr(tasks, "_scrapers", lambda: [_FakeScraper(jobs)])
    _patch_engine(monkeypatch, contacts={
        "https://b/withcontact": {"hr_email": "hr@acme.com", "hr_name": "Pat"},
    })

    tasks.run_discovery(uid, repo.start_run(uid))

    by_company = {j.company: j for j in repo.get_jobs(uid)}
    assert by_company["Acme"].status == "done"
    assert by_company["Acme"].output_dir
    assert by_company["Acme"].hr_email == "hr@acme.com"
    # No contact → skipped, no documents.
    assert by_company["Globex"].status == "skipped"
    assert not by_company["Globex"].output_dir


def test_discovery_requires_a_cv(client, app_context, monkeypatch):
    client.post("/auth/register", data={
        "name": "NoCV", "email": "nocv@x.com", "password": "secret123",
        "confirm": "secret123", "accept_terms": "y"})
    from autojob.extensions import db
    from autojob.models import User
    uid = db.session.scalar(db.select(User.id).where(User.email == "nocv@x.com"))

    monkeypatch.setattr(tasks, "_scrapers", lambda: [])
    result = tasks.run_discovery(uid, repo.start_run(uid))
    assert result.get("error") == "no_cv"


def test_dispatch_scheduled_runs_is_tenant_scoped(make_user, monkeypatch):
    from autojob.services import repository as repo2

    a = make_user("a@x.com")
    make_user("b@x.com")  # a second, unscheduled tenant
    repo2.get_or_create_settings(a.id).schedule_enabled = True
    from autojob.extensions import db
    db.session.commit()

    calls = []
    monkeypatch.setattr(
        tasks.run_discovery_for_user, "delay",
        lambda uid, run_id: calls.append(uid),
    )
    result = tasks.dispatch_scheduled()
    assert result["dispatched"] == 1
    assert calls == [a.id]  # only the scheduled user


def test_start_run_requires_login(client):
    r = client.post("/runs/start")
    assert r.status_code in (302, 401)


def test_start_run_blocks_concurrent(client, app_context):
    client.post("/auth/register", data={
        "name": "Ada", "email": "ada@x.com", "password": "secret123",
        "confirm": "secret123", "accept_terms": "y"})
    from autojob.extensions import db
    from autojob.models import User
    uid = db.session.scalar(db.select(User.id).where(User.email == "ada@x.com"))
    repo.start_run(uid)  # a run already in 'running' state
    r = client.post("/runs/start", headers={"X-CSRFToken": "x"})
    assert r.status_code == 409


def test_followup_skips_replied_and_follows_nonresponders(make_user, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from autojob.extensions import db
    from autojob.models import Job
    from autojob.services import followup, mailer
    from autojob.services.runtime_config import build_runtime_config

    a = make_user("a@x.com")
    old = datetime.now(UTC) - timedelta(days=10)
    replied = Job(id="jr", user_id=a.id, title="R1", company="C1", url="u1",
                  hr_email="hr1@x.com", email_status="sent", email_sent_at=old,
                  reply_detected=True, follow_up_status="pending")
    silent = Job(id="js", user_id=a.id, title="R2", company="C2", url="u2",
                 hr_email="hr2@x.com", email_status="sent", email_sent_at=old,
                 reply_detected=False, follow_up_status="pending")
    db.session.add_all([replied, silent])
    db.session.commit()

    # No IMAP configured → detect_replies is a no-op; mock the SMTP send.
    monkeypatch.setattr(mailer, "send_follow_up",
                        lambda cfg, job: mailer.SendResult(True, "sent"))

    cfg = build_runtime_config(a.id)
    result = followup.run_follow_up_cycle(cfg)

    assert result["follow_ups"] == 1
    db.session.refresh(replied)
    db.session.refresh(silent)
    assert replied.follow_up_status == "pending"   # replied → left for manual follow-up
    assert silent.follow_up_status == "sent"       # non-responder → auto-followed
    # The replied job surfaces as needing the user's attention.
    awaiting = repo.jobs_with_replies_awaiting_action(a.id)
    assert [j.id for j in awaiting] == ["jr"]
