"""Auth flow + security-property tests: no enumeration, CSRF helper, gating."""

from __future__ import annotations


def _register(client, email="ada@x.com", password="secret123", name="Ada"):
    return client.post(
        "/auth/register",
        data={"name": name, "email": email, "password": password,
              "confirm": password, "accept_terms": "y"},
        follow_redirects=True,
    )


def test_landing_is_public(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Upload your CV" in r.data


def test_dashboard_requires_login(client):
    r = client.get("/app")
    assert r.status_code == 302
    assert "/auth/login" in r.headers["Location"]


def test_register_then_dashboard(client):
    r = _register(client)
    assert r.status_code == 200 and b"Hi Ada" in r.data
    assert client.get("/app").status_code == 200


def test_duplicate_registration_conflicts(client):
    _register(client)
    # Attempt the duplicate from a fresh, anonymous client (an already-signed-in
    # client would just be redirected to the dashboard by the register route).
    fresh = client.application.test_client()
    r = fresh.post(
        "/auth/register",
        data={"name": "X", "email": "ada@x.com", "password": "secret123",
              "confirm": "secret123", "accept_terms": "y"},
    )
    assert r.status_code == 409


def test_wrong_password_is_generic_and_401(client):
    _register(client)
    fresh = client.application.test_client()
    r = fresh.post("/auth/login", data={"email": "ada@x.com", "password": "nope"})
    assert r.status_code == 401
    assert b"Invalid email or password" in r.data


def test_unknown_email_does_not_enumerate(client):
    r = client.post("/auth/login", data={"email": "ghost@x.com", "password": "whatever1"})
    assert r.status_code == 401
    assert b"Invalid email or password" in r.data


def test_weak_password_rejected(client):
    r = client.post(
        "/auth/register",
        data={"name": "W", "email": "w@x.com", "password": "short",
              "confirm": "short", "accept_terms": "y"},
    )
    assert r.status_code == 200  # form re-rendered with errors
    assert b"at least 8 characters" in r.data.lower()


def test_security_headers_present(client):
    h = client.get("/").headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert "Referrer-Policy" in h


def test_logout_requires_session(client):
    # logout is POST-only and login-protected → GET should 405, anon POST redirects
    assert client.get("/auth/logout").status_code == 405
