"""Tests for the SSE live-update stream (#198).

Acceptance mapping:
- latest_event_id: 0 on empty/disabled, correct MAX(id) after inserts.
- POST /v1/usage/stream-ticket: requires Bearer (401 without); returns ticket + expires_in.
- GET /v1/usage/stream: 401 for missing / invalid / expired ticket (these return
  BEFORE the infinite stream body, so the TestClient does not hang).
- The stream body itself (`hello`, disconnect-exit, `changed`) is tested by
  driving the `_stream_events` async generator DIRECTLY — never via
  `TestClient.stream()` against the live endpoint. Starlette's TestClient does
  not propagate client disconnect/cancellation into the generator, so reading
  its infinite body would hang the suite forever. Each generator drive is bounded
  with ``asyncio.wait_for(..., timeout=5)`` as a belt-and-braces guard.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain import telemetry
from fritz_local_brain.api import auth, routes
from fritz_local_brain.app import create_app
from fritz_local_brain.config import Settings

_AUTH = {"Authorization": "Bearer secret"}


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_API_TOKEN="secret", **overrides)


def _client(monkeypatch, settings) -> TestClient:
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return TestClient(create_app())


def _seed(settings, event_type="query", *, day="2026-06-01", time="12:00:00"):
    ts = datetime.fromisoformat(f"{day}T{time}+00:00").astimezone(timezone.utc)
    telemetry.record_event(settings, event_type, ts=ts)


# ---------------------------------------------------------------------------
# latest_event_id
# ---------------------------------------------------------------------------

def test_latest_event_id_empty_and_disabled(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert telemetry.latest_event_id(settings) == 0  # no db file yet

    disabled = _settings(tmp_path, LOCAL_BRAIN_TELEMETRY_ENABLED=False)
    assert telemetry.latest_event_id(disabled) == 0


def test_latest_event_id_grows_with_inserts(tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings)
    assert telemetry.latest_event_id(settings) == 1
    _seed(settings)
    _seed(settings)
    assert telemetry.latest_event_id(settings) == 3


# ---------------------------------------------------------------------------
# stream-ticket
# ---------------------------------------------------------------------------

def test_stream_ticket_requires_bearer(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    assert client.post("/v1/usage/stream-ticket").status_code == 401


def test_stream_ticket_returns_ticket_and_expiry(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    body = client.post("/v1/usage/stream-ticket", headers=_AUTH).json()
    assert isinstance(body["ticket"], str) and body["ticket"]
    assert body["expires_in"] > 0


# ---------------------------------------------------------------------------
# stream ticket validation
# ---------------------------------------------------------------------------

def test_stream_missing_ticket_is_401(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    assert client.get("/v1/usage/stream").status_code == 401


def test_stream_invalid_ticket_is_401(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    assert client.get("/v1/usage/stream?ticket=nope").status_code == 401


def test_stream_expired_ticket_is_401(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    ticket = client.post("/v1/usage/stream-ticket", headers=_AUTH).json()["ticket"]
    # Force the ticket past its expiry directly in the in-process store.
    routes._stream_tickets[ticket] = 0.0
    assert client.get(f"/v1/usage/stream?ticket={ticket}").status_code == 401


# ---------------------------------------------------------------------------
# stream body — driven DIRECTLY through the async generator (never via the live
# infinite endpoint, which would hang the TestClient). Each drive is bounded by
# asyncio.wait_for(timeout=5).
# ---------------------------------------------------------------------------

class _FakeReq:
    """Stub Request: report connected for the first ``connected_polls`` checks,
    disconnected afterwards, so the generator exits cleanly and the test ends."""

    def __init__(self, connected_polls: int) -> None:
        self._connected_polls = connected_polls
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > self._connected_polls


def test_stream_emits_hello_then_exits_on_disconnect(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "STREAM_POLL_S", 0.01)

    async def drive():
        # Connected for the first disconnect-check, then disconnected -> exits.
        return [f async for f in routes._stream_events(_FakeReq(1), settings)]

    frames = asyncio.run(asyncio.wait_for(drive(), timeout=5))
    assert frames and "event: hello" in frames[0]


def test_stream_emits_changed_when_latest_event_id_grows(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "STREAM_POLL_S", 0.01)

    # The generator captures its baseline last_id only AFTER yielding `hello`
    # (when it is first resumed). To exercise growth we must seed the new event
    # while the loop is running — i.e. after that baseline is taken — not before.
    # A connected-check side effect seeds exactly one event on the first poll's
    # disconnect check, so the *next* latest_event_id read sees the growth.
    class SeedingReq:
        def __init__(self) -> None:
            self.checks = 0

        async def is_disconnected(self) -> bool:
            self.checks += 1
            if self.checks == 1:
                _seed(settings)  # grow the store mid-loop, after baseline taken
            return self.checks > 5  # stay connected for a few polls, then exit

    async def drive():
        frames = []
        async for f in routes._stream_events(SeedingReq(), settings):
            frames.append(f)
            if "event: changed" in f:
                break
        return frames

    frames = asyncio.run(asyncio.wait_for(drive(), timeout=5))
    assert any("event: changed" in f for f in frames)


# ---------------------------------------------------------------------------
# change-detection helper — directly testable, no live polling needed
# ---------------------------------------------------------------------------

def test_change_detection_via_latest_event_id(tmp_path) -> None:
    """The stream's change source is latest_event_id growing; assert that the
    helper reports growth after a new event so the `changed` frame would fire."""
    settings = _settings(tmp_path)
    before = telemetry.latest_event_id(settings)
    _seed(settings)
    after = telemetry.latest_event_id(settings)
    assert after > before
