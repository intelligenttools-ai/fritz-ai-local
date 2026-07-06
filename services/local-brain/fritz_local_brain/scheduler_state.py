"""Persisted scheduler health state."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .logs import append_global_log
from .telemetry import record_event

if TYPE_CHECKING:
    from .config import Settings


SCHEDULER_COMPILE_FAILURE_STATE = ".scheduler-compile-failures.json"


def scheduler_compile_failure_state_path(brain_home: Path) -> Path:
    return Path(brain_home).expanduser() / SCHEDULER_COMPILE_FAILURE_STATE


def read_scheduler_compile_failure_state(brain_home: Path) -> dict[str, Any] | None:
    path = scheduler_compile_failure_state_path(brain_home)
    if path.is_symlink() or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_scheduler_compile_failure_state(settings: "Settings") -> None:
    path = scheduler_compile_failure_state_path(settings.brain_home)
    try:
        if path.exists() and not path.is_symlink():
            path.unlink()
    except OSError:
        pass


def record_scheduler_compile_failure(settings: "Settings", summary: str, *, now: datetime | None = None) -> dict[str, Any]:
    when = now or datetime.now()
    prior = read_scheduler_compile_failure_state(settings.brain_home) or {}
    count = int(prior.get("count", 0) or 0) + 1
    since = prior.get("since") if count > 1 else None
    if not isinstance(since, str) or not since.strip():
        since = when.isoformat(timespec="seconds")
    state = {
        "count": count,
        "since": since,
        "last_failure": when.isoformat(timespec="seconds"),
        "summary": summary,
        "alert_threshold": settings.scheduler_compile_failure_alarm_threshold,
        "alerted_count": int(prior.get("alerted_count", 0) or 0),
    }
    _write_state(settings.brain_home, state)
    return state


def maybe_alert_scheduler_compile_failure(settings: "Settings", state: dict[str, Any], *, dry_run: bool) -> None:
    threshold = settings.scheduler_compile_failure_alarm_threshold
    count = int(state.get("count", 0) or 0)
    alerted_count = int(state.get("alerted_count", 0) or 0)
    if count < threshold or alerted_count >= count:
        return

    since = str(state.get("since") or "unknown")
    summary = str(state.get("summary") or "unknown failure")
    append_global_log(
        settings.brain_home,
        "COMPILE",
        f"ALERT scheduler compile failing since {since}, see log: {summary}",
        dry_run,
    )
    try:
        record_event(
            settings,
            "scheduler_compile_failure_alarm",
            agent="local-brain",
            status="alert",
            payload={"count": count, "since": since, "summary": summary, "threshold": threshold},
        )
    except Exception:  # noqa: BLE001 - telemetry must never break scheduler health.
        pass
    state["alerted_count"] = count
    _write_state(settings.brain_home, state)


def _write_state(brain_home: Path, state: dict[str, Any]) -> None:
    path = scheduler_compile_failure_state_path(brain_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
