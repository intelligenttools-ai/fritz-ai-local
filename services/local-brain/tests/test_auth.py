from __future__ import annotations

import pytest
from fastapi import HTTPException

from fritz_local_brain.api import auth


class _Settings:
    def __init__(self, api_token: str | None) -> None:
        self.api_token = api_token


def test_require_token_fails_closed_when_token_is_unset(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _Settings(None))

    with pytest.raises(HTTPException) as exc:
        auth.require_token(None)

    assert exc.value.status_code == 503


def test_require_token_rejects_missing_or_wrong_token(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _Settings("secret"))

    with pytest.raises(HTTPException) as missing:
        auth.require_token(None)
    with pytest.raises(HTTPException) as wrong:
        auth.require_token("Bearer wrong")

    assert missing.value.status_code == 401
    assert wrong.value.status_code == 401


def test_require_token_accepts_expected_bearer_token(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _Settings("secret"))

    auth.require_token("Bearer secret")
