"""Lint workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .agents.lint_agent import BrainLintAgent
from .config import Settings
from .logs import append_global_log
from .manifests import load_manifest
from .models import LintRunRequest, LintRunResult
from .paths import PathMapper
from .registry import load_registry, registered_vault_paths
from .skill_loader import load_skill


async def run_lint(settings: Settings, request: LintRunRequest) -> LintRunResult:
    started = datetime.now()
    errors: list[str] = []
    mapper = PathMapper(settings.path_map)
    registry = load_registry(settings.brain_home)
    vault_paths = registered_vault_paths(registry, mapper)
    agent = BrainLintAgent(skill_text=load_skill(settings.skills_dir, settings.lint_skill_name))
    findings = []

    for name, vault_path in vault_paths.items():
        if request.vault and name != request.vault:
            continue
        registry_config = registry.get("vaults", {}).get(name, {})
        findings.extend(agent.lint_vault(name, vault_path, registry_config, load_manifest(vault_path)))

    if request.vault and request.vault not in vault_paths:
        errors.append(f"Unknown vault: {request.vault}")
    append_global_log(settings.brain_home, "LINT", f"Processed {len(vault_paths)} vaults ({len(findings)} findings)", request.dry_run)
    return LintRunResult(
        run_id=str(uuid4()),
        started_at=started,
        finished_at=datetime.now(),
        dry_run=request.dry_run,
        findings=findings,
        errors=errors,
    )
