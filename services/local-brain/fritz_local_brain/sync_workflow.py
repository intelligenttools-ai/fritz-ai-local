"""Sync workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .agents.sync_agent import BrainSyncAgent
from .config import Settings
from .logs import append_global_log, append_vault_log
from .telemetry import sync_log_to_telemetry_quietly
from .manifests import load_manifest
from .models import SyncRunRequest, SyncRunResult
from .paths import PathMapper
from .registry import load_registry, registered_vault_paths
from .skill_loader import load_skill


async def run_sync(settings: Settings, request: SyncRunRequest) -> SyncRunResult:
    started = datetime.now()
    run_id = str(uuid4())
    errors: list[str] = []

    mapper = PathMapper(settings.path_map)
    registry = load_registry(settings.brain_home)
    vault_paths = registered_vault_paths(registry, mapper)
    skill_text = load_skill(settings.skills_dir, settings.sync_skill_name)
    agent = BrainSyncAgent(skill_text=skill_text, allow_first_external_sync=settings.allow_first_external_sync)
    results = []

    for name, vault_path in vault_paths.items():
        if request.vault and name != request.vault:
            continue
        manifest = load_manifest(vault_path)
        if manifest is None:
            errors.append(f"{name}: missing manifest")
            continue
        registry_config = registry.get("vaults", {}).get(name, {})
        approved = settings.approval_matches(request.approval_token)
        result = agent.run_vault(name, vault_path, registry_config, manifest, request.dry_run, approved)
        results.append(result)
        if result.pushed:
            summary = f"Synced {len(result.articles_to_sync)} articles from {name} to git"
            append_vault_log(vault_path, "SYNC", summary, request.dry_run)

    if request.vault and not any(result.vault == request.vault for result in results) and not errors:
        errors.append(f"Unknown vault: {request.vault}")

    pushed = sum(1 for result in results if result.pushed)
    append_global_log(settings.brain_home, "SYNC", f"Processed {len(results)} vaults ({pushed} git pushes)", request.dry_run)
    sync_log_to_telemetry_quietly(settings)
    return SyncRunResult(
        run_id=run_id,
        started_at=started,
        finished_at=datetime.now(),
        dry_run=request.dry_run,
        results=results,
        errors=errors,
    )
