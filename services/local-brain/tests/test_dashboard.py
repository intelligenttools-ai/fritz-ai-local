"""Smoke tests for the /dashboard route (#182).

Acceptance:
- GET /dashboard returns 200 with content-type text/html.
- The body contains the expected title marker and references to the usage endpoints.
- No auth token required (the route is unauthenticated).
- The served file exists on disk and is non-empty.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain.app import create_app, _DASHBOARD


def _client() -> TestClient:
    return TestClient(create_app())


def test_dashboard_returns_200() -> None:
    resp = _client().get("/dashboard")
    assert resp.status_code == 200


def test_dashboard_content_type_is_html() -> None:
    resp = _client().get("/dashboard")
    assert "text/html" in resp.headers["content-type"]


def test_dashboard_contains_title() -> None:
    resp = _client().get("/dashboard")
    assert "<title>" in resp.text


def test_dashboard_references_usage_summary() -> None:
    resp = _client().get("/dashboard")
    assert "v1/usage/summary" in resp.text


def test_dashboard_references_usage_activity() -> None:
    resp = _client().get("/dashboard")
    assert "v1/usage/activity" in resp.text


def test_dashboard_no_auth_required() -> None:
    """GET /dashboard without any Authorization header must still return 200."""
    client = TestClient(create_app())
    resp = client.get("/dashboard")  # no headers
    assert resp.status_code == 200


def test_dashboard_file_exists_and_is_nonempty() -> None:
    assert _DASHBOARD.exists(), f"dashboard.html not found at {_DASHBOARD}"
    assert _DASHBOARD.stat().st_size > 0, "dashboard.html is empty"


def test_dashboard_has_html_escape_helper() -> None:
    """Regression guard for stored XSS: agent-supplied telemetry strings (query
    text, agent ids, vault names) are interpolated into innerHTML and MUST be
    HTML-escaped. A real DOM XSS test isn't feasible at the Python layer, so we
    assert the escape helper is present in the served body — if escaping is
    dropped, this fails."""
    resp = _client().get("/dashboard")
    assert "function esc(" in resp.text


def test_savetoken_dismisses_auth_overlay() -> None:
    """Regression guard (#193): saveToken() must hide the auth overlay after
    storing the token, otherwise the overlay stays up after a valid token is
    entered. A DOM test isn't feasible at the Python layer, so assert the
    saveToken function body calls hideAuthOverlay()."""
    body = _client().get("/dashboard").text
    start = body.index("function saveToken(")
    end = body.index("}", start)
    save_token_body = body[start:end]
    assert "hideAuthOverlay()" in save_token_body
