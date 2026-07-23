"""Reply + bounce detection over a faked IMAP mailbox."""

from __future__ import annotations

import imaplib

from autojob.models import Job
from autojob.services import followup
from autojob.services.runtime_config import RuntimeConfig


class _FakeIMAP:
    """Minimal IMAP4_SSL stand-in returning canned header blobs."""

    def __init__(self, messages):
        # messages: list of header strings (one per inbox item)
        self._messages = messages

    def login(self, user, password):
        return ("OK", [])

    def select(self, box):
        return ("OK", [])

    def search(self, charset, term):
        ids = " ".join(str(i + 1) for i in range(len(self._messages))).encode()
        return ("OK", [ids])

    def fetch(self, mid, fields):
        idx = int(mid) - 1
        if "TEXT" in str(fields):
            return ("OK", [(b"meta", b"")])
        return ("OK", [(b"meta", self._messages[idx].encode())])

    def logout(self):
        return ("OK", [])


def _cfg(uid):
    return RuntimeConfig(user_id=uid, imap={"host": "imap.x", "user": "u", "password": "p"},
                         follow_up_enabled=True, smtp={})


def _sent_job(uid, jid, msgid, hr_email, **kw):
    kw.setdefault("email_status", "sent")
    return Job(id=jid, user_id=uid, title="Role", company="Co", url="u/" + jid,
               hr_email=hr_email, email_message_id=msgid, **kw)


def test_reply_matched_by_thread_headers(make_user, monkeypatch, db):
    a = make_user("a@x.com")
    db.session.add(_sent_job(a.id, "j1", "<mid-1@autojob>", "hr@acme.com"))
    db.session.commit()

    inbox = ["From: someone@acme.com\nSubject: Re: Application\nIn-Reply-To: <mid-1@autojob>\n"]
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(inbox))

    result = followup.detect_replies(_cfg(a.id))
    assert result == {"replies": 1, "bounces": 0}
    db.session.refresh(db.session.get(Job, "j1"))
    assert db.session.get(Job, "j1").reply_detected is True


def test_reply_matched_by_sender_fallback(make_user, monkeypatch, db):
    a = make_user("a@x.com")
    db.session.add(_sent_job(a.id, "j2", "<mid-2@autojob>", "hr@globex.com"))
    db.session.commit()

    inbox = ["From: HR <hr@globex.com>\nSubject: Thanks for applying\n"]  # no threading headers
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(inbox))

    result = followup.detect_replies(_cfg(a.id))
    assert result["replies"] == 1
    assert db.session.get(Job, "j2").reply_detected is True


def test_bounce_marks_not_delivered(make_user, monkeypatch, db):
    a = make_user("a@x.com")
    db.session.add(_sent_job(a.id, "j3", "<mid-3@autojob>", "bad@nope.com"))
    db.session.commit()

    inbox = ["From: Mail Delivery Subsystem <mailer-daemon@mail>\n"
             "Subject: Delivery Status Notification (Failure)\n"
             "References: <mid-3@autojob>\n"]
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(inbox))

    result = followup.detect_replies(_cfg(a.id))
    assert result == {"replies": 0, "bounces": 1}
    j = db.session.get(Job, "j3")
    assert j.bounced is True
    assert j.email_status == "bounced"
    assert j.reply_detected is False


def test_bounced_job_is_not_followed_up(make_user, monkeypatch, db):
    from datetime import UTC, datetime, timedelta

    a = make_user("a@x.com")
    old = datetime.now(UTC) - timedelta(days=10)
    db.session.add(_sent_job(a.id, "j4", "<mid-4@autojob>", "bad@nope.com",
                             email_sent_at=old, bounced=True, email_status="bounced"))
    db.session.commit()

    # No inbox activity; ensure follow-up is skipped for the bounced job.
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP([]))
    from autojob.services import mailer
    monkeypatch.setattr(mailer, "send_follow_up",
                        lambda cfg, job: mailer.SendResult(True, "sent"))

    result = followup.run_follow_up_cycle(_cfg(a.id))
    assert result["follow_ups"] == 0
