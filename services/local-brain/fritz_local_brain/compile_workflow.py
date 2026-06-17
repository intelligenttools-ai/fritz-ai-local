"""Compile workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from pydantic_ai.usage import UsageLimits

from .agents.compile_agent import CompileDeps, build_compile_agent
from .agents.reconciliation_agent import ReconciliationDeps, build_reconciliation_agent
from .captures import _load_processed_captures, archive_processed_inbox_captures, capture_hash, list_all_captures, mark_captures_processed, read_capture
from .config import Settings
from .correlation import find_related_articles
from .indexes import backfill_indexes, update_directory_index, update_indexes_for_article
from .knowledge import ARCHIVE_STATUSES, _current_status, apply_article_write, apply_reconciliation_verdict, ensure_store_root
from .logs import append_global_log, append_reconciliation_undo
from .manifests import load_manifest, resolve_manifest_path
from .models import AppliedArticleWrite, ArticleWriteProposal, CompileRunRequest, CompileRunResult, ReconciliationOutcome
from .paths import PathMapper
from .registry import RegistryError, load_registry, registered_vault_paths
from .security import PolicyError, validate_article_write, validate_store_article_write
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
    reconciliations: list[ReconciliationOutcome] = []

    mapper = PathMapper(settings.path_map)
    try:
        registry = load_registry(settings.brain_home)
        vault_paths = registered_vault_paths(registry, mapper)
    except RegistryError:
        vault_paths = {}
    manifests = {
        name: manifest
        for name, path in vault_paths.items()
        if (manifest := load_manifest(path)) is not None
    }
    store_mode = not manifests
    capture_limit = request.max_captures if request.max_captures is not None else settings.compile_max_captures
    capture_discovery = list_all_captures(settings.brain_home, capture_limit)
    capture_paths = capture_discovery.paths
    capture_hashes = {path.resolve(): capture_hash(path) for path in capture_paths}
    allowed_sources = {path.resolve() for path in capture_paths}
    skill_text = load_skill(settings.skills_dir, settings.compile_skill_name)
    all_proposals = []
    all_skipped: list[str] = []
    nonfatal_warnings: list[str] = []
    simulated_article_paths: dict[str, set[str]] = {name: set() for name in manifests} if not store_mode else {"brain": set()}
    batch_size = settings.compile_max_captures or len(capture_paths) or 1

    # In store mode, resolve the store root once outside the loop.
    brain_store_root = ensure_store_root(settings) if store_mode else None

    for batch_start in range(0, len(capture_paths), batch_size):
        batch_paths = capture_paths[batch_start : batch_start + batch_size]
        article_paths: dict[str, list[str]] = {}
        if store_mode:
            assert brain_store_root is not None
            if brain_store_root.exists():
                article_paths["brain"] = sorted(
                    {
                        str(path.relative_to(brain_store_root))
                        for path in brain_store_root.glob("**/*.md")
                        if path.name != "index.md"
                    }
                    | simulated_article_paths.get("brain", set())
                )
        else:
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

        if store_mode:
            query_text = "\n\n".join(
                read_capture(path, settings.capture_max_chars) for path in batch_paths
            )
            related = await find_related_articles(
                settings,
                query_text,
                store_root=brain_store_root,
                top_k=settings.correlation_top_k,
                char_budget=settings.correlation_max_chars,
            )
        else:
            related = []

        agent = build_compile_agent(settings, skill_text)
        deps = CompileDeps(
            capture_paths=batch_paths,
            vault_names=["brain"] if store_mode else sorted(manifests),
            article_paths=article_paths,
            capture_max_chars=settings.capture_max_chars,
            related_articles=related,
        )
        if store_mode:
            prompt = f"""
Run one chronological compile batch.

Call load_compile_context exactly once. Then return final structured output.
Do not invent vault names or source paths. Later batches may update knowledge created by earlier batches.

Destination: brain knowledge store (~/.brain/knowledge).
Set vault to "brain" for all proposals.
Set relative_path to <scope>/<section>/<slug>.md where scope is "common" or a project slug, and section is one of: decisions, lessons, runbooks, context.
{deps.vault_names}
""".strip()
        else:
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
    if store_mode:
        assert brain_store_root is not None
        if brain_store_root.exists():
            known_existing_targets.update(
                path.resolve() for path in brain_store_root.glob("**/*.md") if path.name != "index.md"
            )
    else:
        for name, manifest in manifests.items():
            knowledge_root = resolve_manifest_path(vault_paths[name], manifest, "knowledge")
            if knowledge_root and knowledge_root.exists():
                known_existing_targets.update(
                    path.resolve() for path in knowledge_root.glob("**/*.md") if ".brain" not in path.parts
                )

    processed_capture_record = _load_processed_captures(settings.brain_home)
    processed_sources: set[Path] = {
        _resolve_capture_source(settings.brain_home, key) for key in processed_capture_record
    }

    validated_targets = []
    for proposal in proposals_to_apply:
        try:
            if store_mode:
                assert brain_store_root is not None
                target = validate_store_article_write(
                    proposal, brain_store_root, settings.brain_home, allowed_sources, known_existing_targets,
                    processed_sources=processed_sources,
                )
            else:
                target = validate_article_write(
                    proposal, vault_paths, manifests, settings.brain_home, allowed_sources, known_existing_targets,
                    processed_sources=processed_sources,
                )
            validated_targets.append((proposal, target))
            if proposal.operation == "create":
                known_existing_targets.add(target)
        except (PolicyError, ValueError) as exc:
            errors.append(f"{proposal.vault}/{proposal.relative_path}: {exc}")

    processed_capture_paths: set[Path] = set()
    applied_store_targets: list[Path] = []
    if not errors:
        for proposal, target in validated_targets:
            try:
                apply_article_write(target, proposal, request.dry_run)
                if store_mode:
                    assert brain_store_root is not None
                    update_indexes_for_article(brain_store_root, target, proposal.title, proposal.summary, request.dry_run)
                else:
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
                    if store_mode:
                        applied_store_targets.append(target)
                    for source in proposal.sources:
                        source_path = _resolve_capture_source(settings.brain_home, source)
                        if source_path in allowed_sources:
                            processed_capture_paths.add(source_path)
            except OSError as exc:
                errors.append(f"{proposal.vault}/{proposal.relative_path}: {exc}")
                break

    if not request.dry_run and not errors:
        processed_capture_paths.update(_skipped_capture_paths(settings.brain_home, allowed_sources, all_skipped))
        # Guarantee every considered capture reaches a terminal state: any capture
        # in the batch that the agent neither produced a proposal for nor listed in
        # `skipped` would otherwise stay pending and be re-read on every run,
        # causing the backlog to never drain (issue #135).  Mark them processed and
        # archive them now.  The existing hash-check inside mark_captures_processed
        # / archive_processed_inbox_captures ensures captures whose content changed
        # during the run are excluded and remain pending for the next run.
        auto_skipped = allowed_sources - processed_capture_paths
        if auto_skipped:
            append_global_log(
                settings.brain_home,
                "COMPILE",
                f"auto-skipped {len(auto_skipped)} captures with no agent proposal/skip — archived to inbox/archive",
                request.dry_run,
            )
            processed_capture_paths.update(auto_skipped)
        sorted_processed_paths = sorted(processed_capture_paths)
        mark_captures_processed(settings.brain_home, sorted_processed_paths, capture_hashes)
        try:
            archive_processed_inbox_captures(settings.brain_home, sorted_processed_paths, capture_hashes)
        except Exception as exc:  # noqa: BLE001 - inbox archive cleanup must not fail applied compile work.
            nonfatal_warnings.append(f"Processed capture archive cleanup failed after compile apply: {exc}")

    if (
        store_mode
        and not request.dry_run
        and not errors
        and settings.reconciliation_enabled
        and settings.correlation_top_k > 0
        and applied_store_targets
    ):
        assert brain_store_root is not None
        reconciliations.extend(
            await _reconcile_applied_articles(settings, brain_store_root, applied_store_targets, request)
        )
        if reconciliations:
            applied_count = sum(1 for o in reconciliations if o.applied)
            proposed_count = sum(1 for o in reconciliations if o.disposition == "proposed")
            escalated_count = sum(1 for o in reconciliations if o.disposition == "escalated")
            verdict_counts: dict[str, int] = {}
            for outcome in reconciliations:
                verdict_counts[outcome.verdict] = verdict_counts.get(outcome.verdict, 0) + 1
            summary = ", ".join(f"{verdict}={count}" for verdict, count in sorted(verdict_counts.items()))
            disposition_note = f"applied={applied_count}"
            if proposed_count:
                disposition_note += f", proposed={proposed_count}"
            if escalated_count:
                disposition_note += f", escalated={escalated_count}"
            append_global_log(
                settings.brain_home,
                "RECONCILE",
                f"Reconciled {len(reconciliations)} pairs across {len(applied_store_targets)} new articles ({summary}; {disposition_note})",
                request.dry_run,
            )
            # Rebuild active + archive indexes when reconciliation moved any
            # article into an archive status (contradicts_supersedes applied).
            archived_by_reconciliation = any(
                o.applied and o.verdict == "contradicts_supersedes"
                for o in reconciliations
            )
            if archived_by_reconciliation:
                assert brain_store_root is not None
                backfill_indexes(brain_store_root, dry_run=False)

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
        reconciliations=reconciliations,
    )


async def _reconcile_applied_articles(
    settings: Settings,
    store_root: Path,
    applied_targets: list[Path],
    request: CompileRunRequest,
) -> list[ReconciliationOutcome]:
    """Run the reconciliation agent for each (new article, related-old article) pair.

    Two-phase approach:
    - Phase A: compute verdicts only (no writes).
    - Phase B: gate on autonomy/approval/bulk-threshold, then apply.

    Autonomy rules
    ~~~~~~~~~~~~~~
    ``propose`` mode:
        Apply ONLY if ``settings.approval_matches(request.approval_token)``.
        Otherwise all verdicts are emitted with ``applied=False, disposition="proposed"``.

    ``apply`` mode (default):
        If ``contradicts_supersedes`` count > ``settings.bulk_supersession_threshold``
        AND NOT approved → ESCALATE supersessions (``disposition="escalated"``, not applied)
        but DO apply all non-supersession verdicts.
        If approved OR count within threshold → apply everything.

    After applying a ``contradicts_supersedes`` (or ``corroborates``) verdict an undo
    record is written via ``append_reconciliation_undo``.
    """

    # ---- Phase A: collect (new_target, old_path, verdict) without writing ----

    pending: list[tuple] = []

    agent = build_reconciliation_agent(settings)

    for new_target in applied_targets:
        try:
            new_content = new_target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new_rel = str(new_target.resolve().relative_to(store_root.resolve()))
        new_title = _store_article_title(new_rel, new_content)

        related = await find_related_articles(
            settings,
            new_content,
            store_root=store_root,
            top_k=settings.correlation_top_k,
            char_budget=settings.correlation_max_chars,
        )
        for entry in related:
            old_rel = entry["path"]
            if old_rel == new_rel:
                continue
            old_path = (store_root / old_rel).resolve()
            deps = ReconciliationDeps(
                new_path=new_rel,
                new_title=new_title,
                new_content=new_content,
                old_path=old_rel,
                old_title=entry.get("title", old_rel),
                old_content=entry.get("content", ""),
            )
            try:
                result = await agent.run(
                    _reconciliation_prompt(new_rel, old_rel),
                    deps=deps,
                    usage_limits=UsageLimits(request_limit=3),
                )
            except Exception as exc:  # noqa: BLE001 — one bad pair must not abort the sweep
                append_global_log(
                    settings.brain_home,
                    "RECONCILE",
                    f"Skipping pair ({new_rel!r}, {old_rel!r}): {exc}",
                    request.dry_run,
                )
                continue
            pending.append((new_target, old_path, result.output))

    # ---- Phase B: gate + apply ----

    autonomy = settings.reconciliation_autonomy
    approved = settings.approval_matches(request.approval_token)
    supersession_count = sum(1 for _, _, v in pending if v.verdict == "contradicts_supersedes")

    # Determine which verdicts to block.
    block_supersessions = False
    global_block = False

    if autonomy == "propose":
        if not approved:
            global_block = True
    else:  # "apply" mode
        if supersession_count > settings.bulk_supersession_threshold and not approved:
            block_supersessions = True

    outcomes: list[ReconciliationOutcome] = []

    for new_target, old_path, verdict in pending:
        new_rel = str(new_target.resolve().relative_to(store_root.resolve()))
        old_rel = str(old_path.resolve().relative_to(store_root.resolve()))

        is_supersession = verdict.verdict == "contradicts_supersedes"

        if global_block:
            outcomes.append(
                ReconciliationOutcome(
                    new_path=new_rel,
                    old_path=old_rel,
                    verdict=verdict.verdict,
                    actions=[],
                    reasoning=verdict.reasoning,
                    applied=False,
                    prior_status=_current_status(old_path),
                    disposition="proposed",
                )
            )
            continue

        if block_supersessions and is_supersession:
            outcomes.append(
                ReconciliationOutcome(
                    new_path=new_rel,
                    old_path=old_rel,
                    verdict=verdict.verdict,
                    actions=[],
                    reasoning=verdict.reasoning,
                    applied=False,
                    prior_status=_current_status(old_path),
                    disposition="escalated",
                )
            )
            continue

        # Apply the verdict.
        outcome = apply_reconciliation_verdict(
            verdict,
            new_path=new_target,
            old_path=old_path,
            store_root=store_root,
            dry_run=request.dry_run,
        )
        outcomes.append(outcome)

        # Write reversible undo log for status-mutating verdicts.
        if verdict.verdict in {"contradicts_supersedes", "corroborates"}:
            links_added: dict[str, list[str]] = {}
            if verdict.verdict == "contradicts_supersedes":
                links_added = {"superseded_by": [new_rel], "supersedes": [old_rel]}
            elif verdict.verdict == "corroborates":
                links_added = {"corroborated_by": [new_rel]}
            append_reconciliation_undo(
                settings.brain_home,
                {
                    "ts": datetime.now().isoformat(),
                    "verdict": verdict.verdict,
                    "new_path": new_rel,
                    "old_path": old_rel,
                    "old_prior_status": outcome.prior_status,
                    "links_added": links_added,
                },
                dry_run=request.dry_run,
            )

    return outcomes


def _reconciliation_prompt(new_rel: str, old_rel: str) -> str:
    return f"""
Reconcile one pair of knowledge articles.

Call load_reconciliation_context exactly once, then return the final structured verdict.

NEW article: {new_rel}
OLD (related existing) article: {old_rel}
""".strip()


def _store_article_title(rel_path: str, content: str) -> str:
    from .embeddings import _title_for

    return _title_for(rel_path, content)
