"""Compile workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from pydantic_ai.usage import UsageLimits

from .agents.compile_agent import CompileDeps, build_compile_agent
from .captures import archive_processed_inbox_captures, capture_hash, list_all_captures, mark_captures_processed
from .config import Settings
from .indexes import update_directory_index
from .knowledge import apply_article_write
from .logs import append_global_log
from .manifests import load_manifest, resolve_manifest_path
from .models import AppliedArticleWrite, ArticleWriteProposal, CompileRunRequest, CompileRunResult
from .paths import PathMapper
from .registry import load_registry, registered_vault_paths
from .security import PolicyError, validate_article_write
from .skill_loader import load_skill


def _resolve_capture_source(brain_home: Path, source: str) -> Path:
    if source.startswith("~/.brain/"):
        source_path = brain_home / source.removeprefix("~/.brain/")
    else:
        source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = brain_home / source_path
    return source_path.resolve()


def _skipped_capture_paths(brain_home: Path, allowed_sources: set[Path], skipped: list[str]) -> set[Path]:
    accounted: set[Path] = set()
    for item in skipped:
        source, separator, _reason = item.partition(":")
        if not separator:
            continue
        source_path = _resolve_capture_source(brain_home, source.strip())
        if source_path in allowed_sources:
            accounted.add(source_path)
    return accounted


def _repair_single_capture_sources(
    brain_home: Path,
    batch_paths: list[Path],
    allowed_sources: set[Path],
    proposal: ArticleWriteProposal,
) -> tuple[ArticleWriteProposal, str | None]:
    """Repair model-mangled source names when a batch contained one capture.

    The model occasionally rewrites long capture file names while still clearly
    producing an article from the only capture it was given. For one-capture
    batches, replacing missing/empty source paths with that exact batch path is
    safer than blocking the drain forever; multi-capture batches still require
    exact source attribution.
    """
    if len(batch_paths) != 1:
        return proposal, None
    capture_root = (brain_home / "capture").resolve()
    if not proposal.sources:
        return proposal, None
    actual_path = batch_paths[0].resolve()
    resolved_sources: list[Path] = []
    for source in proposal.sources:
        try:
            source_path = _resolve_capture_source(brain_home, source)
        except (OSError, RuntimeError, ValueError):
            return proposal, None
        if not source_path.is_relative_to(capture_root):
            return proposal, None
        resolved_sources.append(source_path)
    if resolved_sources and all(source_path in allowed_sources and source_path.exists() for source_path in resolved_sources):
        return proposal, None
    if not all(_looks_like_mangled_single_capture_source(source_path, actual_path) for source_path in resolved_sources):
        return proposal, None
    repaired_source = str(actual_path)
    frontmatter = dict(proposal.frontmatter)
    if "sources" in frontmatter:
        frontmatter["sources"] = [repaired_source]
    return proposal.model_copy(update={"sources": [repaired_source], "frontmatter": frontmatter}), (
        f"Repaired compile proposal source path for single-capture batch: {proposal.relative_path}"
    )


def _looks_like_mangled_single_capture_source(source_path: Path, actual_path: Path) -> bool:
    if source_path.exists():
        return False
    if source_path.parent != actual_path.parent:
        return False
    if source_path.suffix != actual_path.suffix or actual_path.suffix != ".md":
        return False
    similarity = SequenceMatcher(None, source_path.stem, actual_path.stem).ratio()
    return similarity >= 0.78


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
    capture_limit = request.max_captures if request.max_captures is not None else settings.compile_max_captures
    capture_discovery = list_all_captures(settings.brain_home, capture_limit)
    capture_paths = capture_discovery.paths
    capture_hashes = {path.resolve(): capture_hash(path) for path in capture_paths}
    allowed_sources = {path.resolve() for path in capture_paths}
    skill_text = load_skill(settings.skills_dir, settings.compile_skill_name)
    all_proposals = []
    all_skipped: list[str] = []
    nonfatal_warnings: list[str] = []
    simulated_article_paths: dict[str, set[str]] = {name: set() for name in manifests}
    batch_size = settings.compile_max_captures or len(capture_paths) or 1

    for batch_start in range(0, len(capture_paths), batch_size):
        batch_paths = capture_paths[batch_start : batch_start + batch_size]
        article_paths: dict[str, list[str]] = {}
        for name, manifest in manifests.items():
            knowledge_root = resolve_manifest_path(vault_paths[name], manifest, "knowledge")
            if knowledge_root and knowledge_root.exists():
                article_paths[name] = sorted(
                    {
                        str(path.relative_to(knowledge_root))
                        for path in knowledge_root.glob("**/*.md")
                        if ".brain" not in path.parts
                    }
                    | simulated_article_paths.get(name, set())
                )

        agent = build_compile_agent(settings, skill_text)
        deps = CompileDeps(
            capture_paths=batch_paths,
            vault_names=sorted(manifests),
            article_paths=article_paths,
            capture_max_chars=settings.capture_max_chars,
        )
        prompt = f"""
Run one chronological compile batch.

Call load_compile_context exactly once. Then return final structured output.
Do not invent vault names or source paths. Later batches may update knowledge created by earlier batches.

Available vaults:
{deps.vault_names}
""".strip()

        result = await agent.run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=3))
        output = result.output
        repaired_proposals = []
        for proposal in output.proposals:
            repaired_proposal, repair_warning = _repair_single_capture_sources(
                settings.brain_home, batch_paths, allowed_sources, proposal
            )
            repaired_proposals.append(repaired_proposal)
            if repair_warning:
                nonfatal_warnings.append(repair_warning)
        all_proposals.extend(repaired_proposals)
        all_skipped.extend(output.skipped)
        for proposal in repaired_proposals:
            simulated_article_paths.setdefault(proposal.vault, set()).add(proposal.relative_path)

    proposals_to_apply = list(all_proposals)
    if not request.dry_run and len(proposals_to_apply) > settings.large_batch_threshold:
        if not settings.approval_matches(request.approval_token):
            errors.append(
                f"Large compile run requires approval: {len(proposals_to_apply)} proposals exceeds threshold {settings.large_batch_threshold}"
            )
            proposals_to_apply.clear()

    known_existing_targets: set[Path] = set()
    for name, manifest in manifests.items():
        knowledge_root = resolve_manifest_path(vault_paths[name], manifest, "knowledge")
        if knowledge_root and knowledge_root.exists():
            known_existing_targets.update(
                path.resolve() for path in knowledge_root.glob("**/*.md") if ".brain" not in path.parts
            )

    validated_targets = []
    for proposal in proposals_to_apply:
        try:
            target = validate_article_write(
                proposal, vault_paths, manifests, settings.brain_home, allowed_sources, known_existing_targets
            )
            validated_targets.append((proposal, target))
            if proposal.operation == "create":
                known_existing_targets.add(target)
        except (PolicyError, ValueError) as exc:
            errors.append(f"{proposal.vault}/{proposal.relative_path}: {exc}")

    processed_capture_paths: set[Path] = set()
    if not errors:
        for proposal, target in validated_targets:
            try:
                apply_article_write(target, proposal, request.dry_run)
                update_directory_index(target, proposal.title, proposal.summary, request.dry_run)
                if not request.dry_run:
                    applied.append(
                        AppliedArticleWrite(
                            vault=proposal.vault,
                            path=str(target),
                            operation=proposal.operation,
                            title=proposal.title,
                        )
                    )
                    for source in proposal.sources:
                        source_path = _resolve_capture_source(settings.brain_home, source)
                        if source_path in allowed_sources:
                            processed_capture_paths.add(source_path)
            except OSError as exc:
                errors.append(f"{proposal.vault}/{proposal.relative_path}: {exc}")
                break

    if not request.dry_run and not errors:
        processed_capture_paths.update(_skipped_capture_paths(settings.brain_home, allowed_sources, all_skipped))
        sorted_processed_paths = sorted(processed_capture_paths)
        mark_captures_processed(settings.brain_home, sorted_processed_paths, capture_hashes)
        try:
            archive_processed_inbox_captures(settings.brain_home, sorted_processed_paths, capture_hashes)
        except Exception as exc:  # noqa: BLE001 - inbox archive cleanup must not fail applied compile work.
            nonfatal_warnings.append(f"Processed capture archive cleanup failed after compile apply: {exc}")

    for warning in nonfatal_warnings:
        append_global_log(settings.brain_home, "COMPILE", warning, request.dry_run)

    append_global_log(
        settings.brain_home,
        "COMPILE",
        "Processed "
        f"{len(capture_paths)} captures "
        f"(inbox={capture_discovery.by_source.get('inbox', 0)}, "
        f"daily={capture_discovery.by_source.get('daily', 0)}, "
        f"sessions={capture_discovery.by_source.get('sessions', 0)}) "
        f"-> {len(applied)} proposals applied ({len(errors)} errors)",
        request.dry_run,
    )

    return CompileRunResult(
        run_id=run_id,
        started_at=started,
        finished_at=datetime.now(),
        dry_run=request.dry_run,
        captures_considered=len(capture_paths),
        captures_by_source=capture_discovery.by_source,
        proposals=all_proposals,
        applied=applied,
        skipped=all_skipped,
        errors=errors,
    )
