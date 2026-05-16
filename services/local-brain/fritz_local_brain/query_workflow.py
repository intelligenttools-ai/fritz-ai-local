"""Read-only query workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .agents.query_agent import BrainQueryAgent
from .config import Settings
from .manifests import load_manifest
from .models import QueryRunRequest, QueryRunResult
from .paths import PathMapper
from .registry import load_registry, registered_vault_paths
from .skill_loader import load_skill


async def run_query(settings: Settings, request: QueryRunRequest) -> QueryRunResult:
    started = datetime.now()
    errors: list[str] = []
    skipped: list[str] = []
    mapper = PathMapper(settings.path_map)
    registry = load_registry(settings.brain_home)
    vault_paths = registered_vault_paths(registry, mapper)
    agent = BrainQueryAgent(skill_text=load_skill(settings.skills_dir, settings.query_skill_name))
    matches = []

    for name, vault_path in vault_paths.items():
        if request.vault and name != request.vault:
            continue
        manifest = load_manifest(vault_path)
        if manifest is None:
            skipped.append(f"{name}: missing manifest")
            continue
        remaining = request.limit - len(matches)
        if remaining <= 0:
            break
        matches.extend(agent.search_vault(name, vault_path, manifest, request.query, remaining))

    if request.vault and request.vault not in vault_paths:
        errors.append(f"Unknown vault: {request.vault}")
    return QueryRunResult(
        run_id=str(uuid4()),
        started_at=started,
        finished_at=datetime.now(),
        query=request.query,
        matches=matches,
        skipped=skipped,
        errors=errors,
    )
