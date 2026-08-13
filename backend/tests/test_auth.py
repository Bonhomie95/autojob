"""Auth API tests: no enumeration, session gating, security headers."""

from __future__ import annotations


def _register(client, email="ada@x.com", password="secret123", name="Ada"):
    return client.post(
        "/api/auth/register",
        json={"name": name, "email": email, "password": password,
              "confirm": password, "accept_terms": True},
    )


def test_me_is_public_and_anonymous_by_default(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.get_json()["user"] is None


def test_dashboard_requires_login(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 401
    assert r.get_json()["error"] == "unauthorized"


def test_register_then_dashboard(client):
    r = _register(client)
    assert r.status_code == 201
    assert r.get_json()["user"]["name"] == "Ada"
    assert client.get("/api/dashboard").status_code == 200


def test_duplicate_registration_conflicts(client):
    _register(client)
    # Attempt the duplicate from a fresh, anonymous client (an already-signed-in
    # client would just return its own user instead of trying to register).
    fresh = client.application.test_client()
    r = fresh.post(
        "/api/auth/register",
        json={"name": "X", "email": "ada@x.com", "password": "secret123",
              "confirm": "secret123", "accept_terms": True},
    )
    assert r.status_code == 409


def test_wrong_password_is_generic_and_401(client):
    _register(client)
    fresh = client.application.test_client()
    r = fresh.post("/api/auth/login", json={"email": "ada@x.com", "password": "nope"})
    assert r.status_code == 401
    assert r.get_json()["error"] == "Invalid email or password."


def test_unknown_email_does_not_enumerate(client):
    r = client.post("/api/auth/login", json={"email": "ghost@x.com", "password": "whatever1"})
    assert r.status_code == 401
    assert r.get_json()["error"] == "Invalid email or password."


def test_weak_password_rejected(client):
    r = client.post(
        "/api/auth/register",
        json={"name": "W", "email": "w@x.com", "password": "short",
              "confirm": "short", "accept_terms": True},
    )
    assert r.status_code == 400
    assert "8 characters" in r.get_json()["errors"]["password"]


def test_security_headers_present(client):
    h = client.get("/api/auth/me").headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert "Referrer-Policy" in h


def test_logout_requires_session(client):
    # logout is POST-only and login-protected. A GET is a plain method
    # mismatch (405); an anonymous POST is unauthorized (JSON, not a
    # redirect — there's no server-rendered login page to redirect to).
    assert client.get("/api/auth/logout").status_code == 405
    r = client.post("/api/auth/logout")
    assert r.status_code == 401
