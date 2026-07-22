"""Health, readiness, metrics, and request-id plumbing."""

from __future__ import annotations


def test_healthz_liveness(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_readyz_checks_database(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.get_json()["checks"]["database"] == "ok"


def test_request_id_header_present(client):
    r = client.get("/healthz")
    assert r.headers.get("X-Request-ID")


def test_request_id_is_echoed(client):
    r = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers.get("X-Request-ID") == "abc-123"


def test_metrics_prometheus_format(client):
    client.get("/")  # generate at least one counted request
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "autojob_requests_total" in r.get_data(as_text=True)
