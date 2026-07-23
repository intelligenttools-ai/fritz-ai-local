from __future__ import annotations

from fritz_local_brain.api import auth


class _Settings:
    def __init__(self, api_token: str | None) -> None:
        self.api_token = api_token


# require_token is a no-op on this loopback-only deployment: the /v1 HTTP surface
# is open (see auth.require_token docstring). It must never raise, regardless of
# whether a token is configured or supplied.


def test_require_token_is_open_when_token_is_unset() -> None:
    assert auth.require_token(None) is None


def test_require_token_is_open_for_missing_or_wrong_token() -> None:
    assert auth.require_token(None) is None
    assert auth.require_token("Bearer wrong") is None


def test_require_token_is_open_for_expected_bearer_token() -> None:
    assert auth.require_token("Bearer secret") is None


def test_token_matches_still_reflects_configured_token(monkeypatch) -> None:
    # token_matches remains the boundary for the MCP transport, so it must keep
    # comparing against the configured token.
    monkeypatch.setattr(auth, "get_settings", lambda: _Settings("secret"))
    assert auth.token_matches("Bearer secret") is True
    assert auth.token_matches("Bearer wrong") is False
    assert auth.token_matches(None) is False


# CSRF guard: auth is open on loopback, so cross-origin browser writes must be
# rejected (the Bearer token no longer doubles as CSRF protection).


def test_cross_origin_write_from_foreign_origin_is_forbidden() -> None:
    assert auth.is_forbidden_cross_origin("POST", "https://evil.example") is True
    assert auth.is_forbidden_cross_origin("PATCH", "http://attacker.test:8080") is True
    assert auth.is_forbidden_cross_origin("delete", "https://evil.example") is True


def test_loopback_and_tokenless_and_safe_requests_are_allowed() -> None:
    # Same-origin dashboard (loopback Origin), non-browser tools (no Origin), and
    # any safe method must NOT be treated as a forbidden cross-origin write.
    assert auth.is_forbidden_cross_origin("POST", "http://127.0.0.1:8765") is False
    assert auth.is_forbidden_cross_origin("POST", "http://localhost:8765") is False
    assert auth.is_forbidden_cross_origin("POST", None) is False
    assert auth.is_forbidden_cross_origin("GET", "https://evil.example") is False
