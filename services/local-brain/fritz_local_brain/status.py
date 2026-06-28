"""Service status assembly."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .captures import CAPTURE_SOURCES
from .config import Settings
from .models import StatusResult
from .run_history import last_successful_compile_at

_STATUS_CAPTURE_SCAN_LIMIT = 1000
_STATUS_CAPTURE_HASH_BYTE_LIMIT = 1024 * 1024
_STATUS_CAPTURE_HASH_TOTAL_BYTE_LIMIT = 4 * 1024 * 1024

_SERVICE_VERSION_PATH = Path("/app/VERSION")


def _read_service_version() -> str | None:
    """Return the VERSION baked into the image, or None if unavailable."""
    try:
        version = _SERVICE_VERSION_PATH.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return version or None


@dataclass(frozen=True)
class _StatusBacklog:
    by_source: dict[str, int] = field(default_factory=dict)
    oldest_path: Path | None = None
    oldest_mtime: float | None = None
    warnings: list[str] = field(default_factory=list)


def build_status(
    settings: Settings,
    *,
    service_running: bool = True,
    scheduler_task_running: bool | None = None,
) -> StatusResult:
    """Return runtime health and processing state without secrets."""

    pending = _best_effort_status_backlog(settings.brain_home)
    scheduler_dry_run = settings.scheduler_dry_run
    processing_mode = "dry-run" if scheduler_dry_run else "apply"
    if scheduler_task_running is None:
        scheduler_task_running = service_running and settings.scheduler_enabled
    processing_active = settings.scheduler_enabled and scheduler_task_running
    processing_note = _processing_note(
        scheduler_enabled=settings.scheduler_enabled,
        scheduler_dry_run=scheduler_dry_run,
        scheduler_task_running=scheduler_task_running,
        autostart_installed=settings.local_brain_autostart_installed,
    )

    return StatusResult(
        service_running=service_running,
        version=_read_service_version(),
        scheduler_enabled=settings.scheduler_enabled,
        scheduler_dry_run=scheduler_dry_run,
        processing_mode=processing_mode,
        processing_active=processing_active,
        processing_note=processing_note,
        interval_minutes=settings.interval_minutes,
        brain_home=str(settings.brain_home),
        skills_dir=str(settings.skills_dir),
        allow_first_external_sync=settings.allow_first_external_sync,
        last_successful_compile_at=last_successful_compile_at(),
        pending_captures_by_source=pending.by_source,
        oldest_pending_capture_path=str(pending.oldest_path) if pending.oldest_path else None,
        oldest_pending_capture_at=datetime.fromtimestamp(pending.oldest_mtime) if pending.oldest_mtime is not None else None,
        status_warnings=pending.warnings,
    )


def _best_effort_status_backlog(
    brain_home: Path,
    scan_limit: int = _STATUS_CAPTURE_SCAN_LIMIT,
    hash_byte_limit: int = _STATUS_CAPTURE_HASH_BYTE_LIMIT,
    hash_total_byte_limit: int = _STATUS_CAPTURE_HASH_TOTAL_BYTE_LIMIT,
) -> _StatusBacklog:
    """Return a bounded capture backlog approximation for status.

    Status mirrors compile's processed-capture hash check only for captures small enough
    to hash cheaply on the request path. Larger previously processed captures are
    treated as processed to keep status lightweight; compile still performs the exact
    content-hash check when it runs. Counts may be partial if filesystem discovery or
    hashing fails, or if the scan reaches the work cap.
    """

    counts = {source: 0 for source in CAPTURE_SOURCES}
    warnings: list[str] = []
    oldest_path: Path | None = None
    oldest_mtime: float | None = None
    examined = 0
    hash_bytes_read = 0

    capture_parent = brain_home / "capture"
    try:
        if capture_parent.is_symlink():
            return _StatusBacklog(by_source=counts)
    except OSError as exc:
        warnings.append(f"Unable to inspect capture directory: {exc}")
        return _StatusBacklog(by_source=counts, warnings=warnings)

    processed = _load_processed_capture_hashes_for_status(brain_home, warnings)

    capped_warning = f"Capture status scan capped at {scan_limit} files; counts may be partial."

    for source in CAPTURE_SOURCES:
        if examined >= scan_limit:
            if capped_warning not in warnings:
                warnings.append(capped_warning)
            break
        capture_dir = capture_parent / source
        try:
            if capture_dir.is_symlink() or not capture_dir.is_dir():
                continue
            with os.scandir(capture_dir) as entries:
                for entry in entries:
                    if examined >= scan_limit:
                        if capped_warning not in warnings:
                            warnings.append(capped_warning)
                        break
                    examined += 1
                    if not entry.name.endswith(".md"):
                        continue
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(entry_stat.st_mode):
                            continue
                        path = Path(entry.path)
                        resolved = path.resolve(strict=True)
                        processed_hash = processed.get(str(resolved))
                        if processed_hash is not None:
                            matches_processed, bytes_read = _capture_matches_processed_for_status(
                                path,
                                processed_hash,
                                entry_stat.st_size,
                                hash_byte_limit,
                                hash_total_byte_limit - hash_bytes_read,
                                warnings,
                            )
                            hash_bytes_read += bytes_read
                            if matches_processed:
                                continue
                    except (OSError, ValueError) as exc:
                        warnings.append(f"Skipped capture during status scan: {entry.name}: {exc}")
                        continue
                    counts[source] += 1
                    if oldest_mtime is None or entry_stat.st_mtime < oldest_mtime:
                        oldest_path = path
                        oldest_mtime = entry_stat.st_mtime
        except OSError as exc:
            warnings.append(f"Unable to scan capture/{source}: {exc}")

    return _StatusBacklog(by_source=counts, oldest_path=oldest_path, oldest_mtime=oldest_mtime, warnings=warnings)


def _capture_matches_processed_for_status(
    path: Path,
    processed_hash: str,
    size: int,
    hash_byte_limit: int,
    remaining_hash_byte_budget: int,
    warnings: list[str],
) -> tuple[bool, int]:
    if size > hash_byte_limit:
        warning = (
            f"Status capture hash skipped for files larger than {hash_byte_limit} bytes; "
            "large processed captures may not reflect content changes until compile runs."
        )
        if warning not in warnings:
            warnings.append(warning)
        return True, 0
    if size > remaining_hash_byte_budget:
        warning = (
            "Status capture hash byte budget exhausted; "
            "processed captures may not reflect content changes until compile runs."
        )
        if warning not in warnings:
            warnings.append(warning)
        return True, 0
    digest = hashlib.sha256()
    bytes_read = 0
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        fd_stat = os.fstat(fd)
        if not stat.S_ISREG(fd_stat.st_mode):
            return False, 0
        if fd_stat.st_size > hash_byte_limit:
            warning = (
                f"Status capture hash skipped for files larger than {hash_byte_limit} bytes; "
                "large processed captures may not reflect content changes until compile runs."
            )
            if warning not in warnings:
                warnings.append(warning)
            return True, 0
        if fd_stat.st_size > remaining_hash_byte_budget:
            warning = (
                "Status capture hash byte budget exhausted; "
                "processed captures may not reflect content changes until compile runs."
            )
            if warning not in warnings:
                warnings.append(warning)
            return True, 0
        read_budget = min(hash_byte_limit, remaining_hash_byte_budget)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            while bytes_read < read_budget:
                chunk = handle.read(min(1024 * 1024, read_budget - bytes_read))
                if not chunk:
                    return processed_hash == digest.hexdigest(), bytes_read
                digest.update(chunk)
                bytes_read += len(chunk)
            if handle.read(1):
                if read_budget == hash_byte_limit:
                    warning = (
                        f"Status capture hash skipped for files larger than {hash_byte_limit} bytes; "
                        "large processed captures may not reflect content changes until compile runs."
                    )
                else:
                    warning = (
                        "Status capture hash byte budget exhausted; "
                        "processed captures may not reflect content changes until compile runs."
                    )
                if warning not in warnings:
                    warnings.append(warning)
                return True, bytes_read
            return processed_hash == digest.hexdigest(), bytes_read
    finally:
        os.close(fd)


def _load_processed_capture_hashes_for_status(brain_home: Path, warnings: list[str]) -> dict[str, str]:
    state_path = brain_home / "capture" / "processed.json"
    try:
        if state_path.is_symlink() or not state_path.exists():
            return {}
        import json

        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append(f"Unable to read processed capture state for status: {exc}")
        return {}
    processed = data.get("processed", {}) if isinstance(data, dict) else {}
    if not isinstance(processed, dict):
        return {}
    return {path: digest for path, digest in processed.items() if isinstance(path, str) and isinstance(digest, str)}


def _processing_note(
    *,
    scheduler_enabled: bool,
    scheduler_dry_run: bool,
    scheduler_task_running: bool,
    autostart_installed: bool,
) -> str:
    if not scheduler_enabled:
        return "Scheduler disabled; processing runs only while the service/agent trigger invokes compile."
    mode = "dry-run simulation" if scheduler_dry_run else "apply-mode processing"
    if not scheduler_task_running:
        return (
            f"Scheduler enabled in {mode}, but no scheduler task is running in this process; "
            "scheduled processing is not active."
        )
    if not autostart_installed:
        return f"Scheduler enabled in {mode}, but autostart is not installed; processing is active only while this service process is running."
    return f"Scheduler enabled in {mode}."
