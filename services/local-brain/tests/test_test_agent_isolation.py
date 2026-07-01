"""Tests for test-agent isolation (#206).

Covers:
- _is_test_agent: unit cases for all pattern variants
- agents(): test agents excluded from the distinct-agent list
- activity(by="agent"): test agents excluded from agent dimension grouping
- purge_test_agents(): deletes exactly test-agent rows, returns correct count
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from fritz_local_brain import telemetry
from fritz_local_brain.config import Settings
from fritz_local_brain.usage import TEST_AGENT_PATTERNS, _is_test_agent, agents, activity


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, **overrides)


def _seed(settings: Settings, event_type: str, *, agent: str | None = None,
          day: str = "2026-06-01") -> None:
    ts = datetime.fromisoformat(f"{day}T12:00:00+00:00").astimezone(timezone.utc)
    telemetry.record_event(settings, event_type, agent=agent, ts=ts)


# ---------------------------------------------------------------------------
# _is_test_agent unit cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    # exact matches
    ("diag",      True),
    ("pwsse",     True),
    # prefix
    ("pwtest",    True),
    ("pwtest199", True),
    ("pwtest0",   True),
    # non-matching
    ("pi",        False),
    ("claude",    False),
    ("diagnostic", False),
    ("unknown",   False),
    ("pwss",      False),
    ("",          False),
])
def test_is_test_agent(name: str, expected: bool) -> None:
    assert _is_test_agent(name) is expected


# ---------------------------------------------------------------------------
# TEST_AGENT_PATTERNS constant
# ---------------------------------------------------------------------------

def test_test_agent_patterns_present() -> None:
    assert "diag" in TEST_AGENT_PATTERNS
    assert "pwsse" in TEST_AGENT_PATTERNS
    assert "pwtest" in TEST_AGENT_PATTERNS


# ---------------------------------------------------------------------------
# agents() excludes test agents
# ---------------------------------------------------------------------------

def test_agents_excludes_test_agents(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for agent in ("pi", "diag", "pwtest199", "pwsse", "claude"):
        _seed(settings, "capture", agent=agent)

    result = agents(settings)
    names = {row["agent"] for row in result}

    assert "pi" in names
    assert "claude" in names
    assert "diag" not in names
    assert "pwtest199" not in names
    assert "pwsse" not in names


def test_agents_empty_when_only_test_agents(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for agent in ("diag", "pwtest1", "pwsse"):
        _seed(settings, "capture", agent=agent)

    assert agents(settings) == []


# ---------------------------------------------------------------------------
# activity(by="agent") excludes test agents
# ---------------------------------------------------------------------------

def test_activity_by_agent_excludes_test_agents(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for agent in ("pi", "diag", "pwtest199", "pwsse", "claude"):
        _seed(settings, "capture", agent=agent, day="2026-06-01")

    buckets = activity(settings, since="2026-06-01", until="2026-06-01", by="agent")
    day_keys = buckets.get("2026-06-01", {})

    assert "pi" in day_keys
    assert "claude" in day_keys
    assert "diag" not in day_keys
    assert "pwtest199" not in day_keys
    assert "pwsse" not in day_keys


def test_activity_by_type_includes_all_events(tmp_path: Path) -> None:
    """by=type grouping is unaffected: test-agent events still count under their type."""
    settings = _settings(tmp_path)
    for agent in ("pi", "diag"):
        _seed(settings, "capture", agent=agent, day="2026-06-01")

    buckets = activity(settings, since="2026-06-01", until="2026-06-01", by="type")
    day_keys = buckets.get("2026-06-01", {})
    # Both events are "capture" type — total count is 2
    assert day_keys.get("capture", 0) == 2


# ---------------------------------------------------------------------------
# purge_test_agents()
# ---------------------------------------------------------------------------

def test_purge_test_agents_deletes_only_test_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for agent in ("pi", "diag", "pwtest199", "pwsse", "claude", "pwtest0"):
        _seed(settings, "capture", agent=agent)

    deleted = telemetry.purge_test_agents(settings)

    # diag + pwtest199 + pwsse + pwtest0 = 4 test rows
    assert deleted == 4

    remaining = telemetry.read_events(settings)
    remaining_agents = {r["agent"] for r in remaining}
    assert remaining_agents == {"pi", "claude"}


def test_purge_test_agents_returns_zero_when_no_test_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "capture", agent="pi")
    _seed(settings, "capture", agent="claude")

    assert telemetry.purge_test_agents(settings) == 0


def test_purge_test_agents_noop_when_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_TELEMETRY_ENABLED=False)
    # No db was created; should return 0 silently
    assert telemetry.purge_test_agents(settings) == 0


def test_purge_test_agents_noop_when_no_db(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    # telemetry enabled but db not yet created
    assert telemetry.purge_test_agents(settings) == 0


def test_purge_test_agents_mixed_pwtest_names(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for agent in ("pwtest", "pwtest1", "pwtest999", "pwss", "pi"):
        _seed(settings, "query", agent=agent)

    deleted = telemetry.purge_test_agents(settings)
    # pwtest, pwtest1, pwtest999 match; pwss and pi do not
    assert deleted == 3

    remaining_agents = {r["agent"] for r in telemetry.read_events(settings)}
    assert remaining_agents == {"pwss", "pi"}
