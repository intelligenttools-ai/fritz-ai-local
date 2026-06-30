"""Tests for the log.md -> telemetry sync (#177).

Acceptance mapping:
1. test_each_log_type_imported_with_correct_fields  (event_type/agent/status)
2. test_idempotent_no_dupes_then_imports_only_new
3. test_disabled_writes_nothing_and_no_db
4. test_ts_normalized_to_canonical_utc
5. test_malformed_line_skipped_without_crash
6. test_capture_line_imported
"""

from __future__ import annotations

from fritz_local_brain import telemetry
from fritz_local_brain.config import Settings


def _settings(tmp_path, **overrides):
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, **overrides)


def _write_log(tmp_path, lines):
    (tmp_path / "log.md").write_text("".join(line + "\n" for line in lines), encoding="utf-8")


# Acceptance 1: one row per line, correct event_type/agent/status (incl. an error line).
def test_each_log_type_imported_with_correct_fields(tmp_path) -> None:
    settings = _settings(tmp_path)
    lines = [
        "2026-06-30 10:00 | COMPILE | local-brain | Processed 3 captures -> 2 proposals applied",
        "2026-06-30 10:01 | RECONCILE | local-brain | Reconciled 1 article",
        "2026-06-30 10:02 | LINT | local-brain | Processed 2 vaults (0 findings)",
        "2026-06-30 10:03 | SYNC | local-brain | Processed 2 vaults (1 git pushes)",
        "2026-06-30 10:04 | MIRROR | local-brain | Mirrored 4 targets",
        "2026-06-30 10:05 | RERECONCILE | local-brain | Swept 5 articles",
        "2026-06-30 10:06 | EMBEDDINGS | local-brain | Rebuilt index for 10 docs",
        "2026-06-30 10:07 | CAPTURE | claude_code | Stop: 5 topics from /x",
        "2026-06-30 10:08 | COMPILE | local-brain | Scheduler compile failed: boom",
    ]
    _write_log(tmp_path, lines)

    imported = telemetry.sync_log_to_telemetry(settings)
    assert imported == len(lines)

    rows = telemetry.read_events(settings)
    assert len(rows) == len(lines)

    by_type = {r["event_type"]: r for r in rows}
    # event_type is verbatim-lowercased; rereconcile/mirror not collapsed.
    assert set(by_type) == {
        "compile",
        "reconcile",
        "lint",
        "sync",
        "mirror",
        "rereconcile",
        "embeddings",
        "capture",
    }
    # agent = source; "local-brain" for workflow lines, the agent for capture.
    assert by_type["reconcile"]["agent"] == "local-brain"
    assert by_type["capture"]["agent"] == "claude_code"
    # status defaults to "ok".
    assert by_type["lint"]["status"] == "ok"
    # The error-summary COMPILE line is "error". (Two compile rows exist; find it.)
    compile_rows = [r for r in rows if r["event_type"] == "compile"]
    statuses = {r["status"] for r in compile_rows}
    assert "error" in statuses
    assert "ok" in statuses


# Regression: the production COMPILE summary ends "(N errors)" — a count, not a
# failure — and must NOT be misclassified as status="error".
def test_compile_error_count_is_not_a_failure(tmp_path) -> None:
    settings = _settings(tmp_path)
    _write_log(
        tmp_path,
        [
            "2026-06-30 14:23 | COMPILE | local-brain | Processed 5 captures "
            "(inbox=5, daily=0, sessions=0) -> 3 proposals applied (2 errors)",
        ],
    )

    telemetry.sync_log_to_telemetry(settings)
    rows = telemetry.read_events(settings)
    assert len(rows) == 1
    # "(2 errors)" is a count -> still a successful run.
    assert rows[0]["status"] == "ok"


# Regression: a genuine EMBEDDINGS failure ("crashed") IS status="error".
def test_embeddings_crash_is_error(tmp_path) -> None:
    settings = _settings(tmp_path)
    _write_log(
        tmp_path,
        ["2026-06-30 14:30 | EMBEDDINGS | local-brain | Embedding refresh crashed: oom"],
    )

    telemetry.sync_log_to_telemetry(settings)
    rows = telemetry.read_events(settings)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "embeddings"
    assert rows[0]["status"] == "error"


# Acceptance 2: re-running does not duplicate; new lines import exactly once.
def test_idempotent_no_dupes_then_imports_only_new(tmp_path) -> None:
    settings = _settings(tmp_path)
    base = [
        "2026-06-30 10:00 | COMPILE | local-brain | ok",
        "2026-06-30 10:01 | SYNC | local-brain | ok",
    ]
    _write_log(tmp_path, base)

    assert telemetry.sync_log_to_telemetry(settings) == 2
    assert telemetry.sync_log_to_telemetry(settings) == 0  # nothing new
    assert len(telemetry.read_events(settings)) == 2

    # Append 2 new lines, sync -> exactly 2 new events.
    with (tmp_path / "log.md").open("a", encoding="utf-8") as fh:
        fh.write("2026-06-30 10:02 | LINT | local-brain | ok\n")
        fh.write("2026-06-30 10:03 | MIRROR | local-brain | ok\n")
    assert telemetry.sync_log_to_telemetry(settings) == 2
    assert len(telemetry.read_events(settings)) == 4


# Acceptance 3: disabled -> writes nothing, no db, returns 0.
def test_disabled_writes_nothing_and_no_db(tmp_path) -> None:
    settings = _settings(tmp_path, TELEMETRY_ENABLED="false")
    _write_log(tmp_path, ["2026-06-30 10:00 | COMPILE | local-brain | ok"])

    assert telemetry.sync_log_to_telemetry(settings) == 0
    assert not (tmp_path / "telemetry.db").exists()


# Acceptance 4: "%Y-%m-%d %H:%M" timestamp stored as canonical UTC ISO.
def test_ts_normalized_to_canonical_utc(tmp_path) -> None:
    settings = _settings(tmp_path)
    _write_log(tmp_path, ["2026-06-30 14:23 | COMPILE | local-brain | ok"])

    telemetry.sync_log_to_telemetry(settings)
    stored = telemetry.read_events(settings)[0]["ts"]
    assert stored.startswith("2026-06-30")
    assert stored.endswith("+00:00")
    assert stored == "2026-06-30T14:23:00+00:00"


# Acceptance 5: malformed line skipped, no crash, no event.
def test_malformed_line_skipped_without_crash(tmp_path) -> None:
    settings = _settings(tmp_path)
    _write_log(
        tmp_path,
        [
            "garbage no pipes",
            "2026-06-30 10:00 | COMPILE | local-brain | ok",
        ],
    )

    imported = telemetry.sync_log_to_telemetry(settings)
    assert imported == 1
    rows = telemetry.read_events(settings)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "compile"


# Acceptance 6: capture line -> capture/claude_code/ok.
def test_capture_line_imported(tmp_path) -> None:
    settings = _settings(tmp_path)
    _write_log(tmp_path, ["2026-06-30 14:23 | CAPTURE | claude_code | Stop: 5 topics from /x"])

    telemetry.sync_log_to_telemetry(settings)
    rows = telemetry.read_events(settings)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "capture"
    assert rows[0]["agent"] == "claude_code"
    assert rows[0]["status"] == "ok"


# Extra: missing log.md -> no-op, no db.
def test_missing_log_is_noop(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert telemetry.sync_log_to_telemetry(settings) == 0
    assert not (tmp_path / "telemetry.db").exists()


# Extra: rotation (fewer lines than mark) re-imports from start.
def test_rotation_resets_high_water_mark(tmp_path) -> None:
    settings = _settings(tmp_path)
    _write_log(tmp_path, [f"2026-06-30 10:0{i} | COMPILE | local-brain | ok" for i in range(3)])
    assert telemetry.sync_log_to_telemetry(settings) == 3

    # Truncate to 1 line (rotation): mark resets, re-imports the single line.
    _write_log(tmp_path, ["2026-06-30 11:00 | SYNC | local-brain | ok"])
    assert telemetry.sync_log_to_telemetry(settings) == 1
    assert len(telemetry.read_events(settings)) == 4


# Hardening A: an incomplete trailing line (host hook mid-append, no trailing
# newline) is NOT imported now and is NOT lost — it imports exactly once when
# the next sync sees it completed.
def test_torn_trailing_line_imported_once_when_complete(tmp_path) -> None:
    import json

    settings = _settings(tmp_path)
    log_path = tmp_path / "log.md"
    # Two complete lines + a partial third line (no trailing newline).
    log_path.write_text(
        "2026-06-30 10:00 | COMPILE | local-brain | ok\n"
        "2026-06-30 10:01 | SYNC | local-brain | ok\n"
        "2026-06-30 10:02 | LINT | local-brain | partia",  # torn, still being written
        encoding="utf-8",
    )

    # Only the 2 complete lines import; the partial line is left for next time.
    assert telemetry.sync_log_to_telemetry(settings) == 2
    rows = telemetry.read_events(settings)
    assert [r["event_type"] for r in rows] == ["compile", "sync"]

    # High-water excludes the partial line (count == complete lines only).
    state = json.loads((tmp_path / "telemetry_backfill.json").read_text(encoding="utf-8"))
    assert state["global_log_lines_imported"] == 2

    # The host hook finishes the line and appends the next newline-terminated line.
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("l-from-/x\n")  # completes line 3
        fh.write("2026-06-30 10:03 | MIRROR | local-brain | ok\n")

    # Next sync imports the now-complete line 3 plus line 4 — each exactly once.
    assert telemetry.sync_log_to_telemetry(settings) == 2
    rows = telemetry.read_events(settings)
    assert [r["event_type"] for r in rows] == ["compile", "sync", "lint", "mirror"]
    # No duplication of the earlier lines.
    assert len(rows) == 4


# Hardening B: the state file ends up with the trimmed complete-line count and
# is valid JSON (written atomically; assert content reflects only complete lines).
def test_state_file_excludes_partial_line(tmp_path) -> None:
    import json

    settings = _settings(tmp_path)
    log_path = tmp_path / "log.md"
    log_path.write_text(
        "2026-06-30 10:00 | COMPILE | local-brain | ok\n"
        "2026-06-30 10:01 | SYNC | local-brain | par",  # partial, no newline
        encoding="utf-8",
    )

    telemetry.sync_log_to_telemetry(settings)
    state = json.loads((tmp_path / "telemetry_backfill.json").read_text(encoding="utf-8"))
    # Only the 1 complete line counts toward the high-water mark.
    assert state["global_log_lines_imported"] == 1
    # No leftover temp files from the atomic write.
    assert list(tmp_path.glob(".telemetry_backfill.json.*.tmp")) == []
