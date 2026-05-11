"""Compile workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic_ai.usage import UsageLimits

from .agents.compile_agent import CompileDeps, build_compile_agent
from .captures import list_daily_captures
from .config import Settings
from .indexes import update_directory_index
from .knowledge import apply_article_write
from .logs import append_global_log
from .manifests import load_manifest, resolve_manifest_path
from .models import AppliedArticleWrite, CompileRunRequest, CompileRunResult
from .paths import PathMapper
from .registry import load_registry, registered_vault_paths
from .security import PolicyError, validate_article_write
from .skill_loader import load_skill


async def run_compile(settings: Settings, request: CompileRunRequest) -> CompileRunResult:
    started = datetime.now()
    run_id = str(uuid4())
    errors: list[str] = []
    applied: list[AppliedArticleWrite] = []

    mapper = PathMapper(settings.path_map)
    registry = load_registry(settings.brain_home)
    vault_paths = registered_vault_paths(registry, mapper)
    manifests = {
        name: manifest
        for name, path in vault_paths.items()
        if (manifest := load_manifest(path)) is not None
    }
    capture_limit = request.max_captures or settings.compile_max_captures
    capture_paths = list_daily_captures(settings.brain_home, capture_limit)
    article_paths: dict[str, list[str]] = {}
    for name, manifest in manifests.items():
        knowledge_root = resolve_manifest_path(vault_paths[name], manifest, "knowledge")
        if knowledge_root and knowledge_root.exists():
            article_paths[name] = sorted(
                str(path.relative_to(knowledge_root))
                for path in knowledge_root.glob("**/*.md")
                if ".brain" not in path.parts
            )

    skill_text = load_skill(settings.skills_dir, settings.compile_skill_name)
    agent = build_compile_agent(settings, skill_text)
    deps = CompileDeps(
        capture_paths=capture_paths,
        vault_names=sorted(manifests),
        article_paths=article_paths,
        capture_max_chars=settings.capture_max_chars,
    )
    prompt = f"""
Run one MVP compile pass.

Call load_compile_context exactly once. Then return final structured output.
Do not invent vault names or source paths.

Available vaults:
{deps.vault_names}
""".strip()

    result = await agent.run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=3))
    output = result.output

    if not request.dry_run and len(output.proposals) > settings.large_batch_threshold:
        if not settings.approval_matches(request.approval_token):
            errors.append(
                f"Large compile batch requires approval: {len(output.proposals)} proposals exceeds threshold {settings.large_batch_threshold}"
            )
            output.proposals.clear()

    for proposal in output.proposals:
        try:
            target = validate_article_write(proposal, vault_paths, manifests, settings.brain_home)
            apply_article_write(target, proposal, request.dry_run)
            update_directory_index(target, proposal.title, proposal.summary, request.dry_run)
            applied.append(
                AppliedArticleWrite(
                    vault=proposal.vault,
                    path=str(target),
                    operation=proposal.operation,
                    title=proposal.title,
                )
            )
        except (OSError, PolicyError, ValueError) as exc:
            errors.append(f"{proposal.vault}/{proposal.relative_path}: {exc}")

    append_global_log(
        settings.brain_home,
        "COMPILE",
        f"Processed {len(capture_paths)} captures -> {len(applied)} proposals applied ({len(errors)} errors)",
        request.dry_run,
    )

    return CompileRunResult(
        run_id=run_id,
        started_at=started,
        finished_at=datetime.now(),
        dry_run=request.dry_run,
        captures_considered=len(capture_paths),
        proposals=output.proposals,
        applied=applied,
        skipped=output.skipped,
        errors=errors,
    )
