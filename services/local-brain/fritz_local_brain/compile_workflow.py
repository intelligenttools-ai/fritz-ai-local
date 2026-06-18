"""Compile workflow orchestration."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from pydantic_ai.usage import UsageLimits

from .agents.compile_agent import CompileDeps, build_compile_agent
from .agents.reconciliation_agent import ReconciliationDeps, build_reconciliation_agent
from .captures import (
    _load_processed_captures,
    archive_processed_inbox_captures,
    capture_hash,
    clear_capture_attempts,
    increment_capture_attempts,
    list_all_captures,
    mark_captures_processed,
    quarantine_captures,
    read_capture,
)
from .config import Settings
from .correlation import find_related_articles
from .llm import AGENT_REQUEST_LIMIT
from .indexes import backfill_indexes, update_directory_index, update_indexes_for_article
from .knowledge import ARCHIVE_STATUSES, _current_status, apply_article_write, apply_reconciliation_verdict, ensure_store_root
from .logs import append_global_log, append_reconciliation_undo
from .manifests import load_manifest, resolve_manifest_path
from .models import AppliedArticleWrite, ArticleWriteProposal, CompileRunRequest, CompileRunResult, ReconciliationOutcome
from .paths import PathMapper
from .registry import RegistryError, load_registry, registered_vault_paths
from .security import PolicyError, validate_article_write, validate_store_article_write
from .skill_loader import load_skill

# Number of consecutive compile runs a capture may go unaccounted-for (no
# proposal and no explicit skip) before it is quarantined.  Bounds retries so
# the backlog cannot grow forever (issue #135) while never silently discarding
# data (issue #150).
COMPILE_MAX_CAPTURE_ATTEMPTS = 3

# Absolute lifetime ceiling on attempts for a single capture path, independent of
# content changes.  The hash-bound budget (above) resets when a capture's content
# changes — which is correct for an edited/corrected capture, but means a capture
# whose content mutates on EVERY run would reset to 1 forever and never quarantine,
# reopening the "backlog cannot grow forever" guarantee (#135).  This bound fires
# on the lifetime ``total`` (never reset) so even content-mutating captures are
# eventually quarantined.
COMPILE_MAX_CAPTURE_ATTEMPTS_ABSOLUTE = 10


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


def _repair_single_capture_source(
    brain_home: Path,
    capture_path: Path,
    proposal: ArticleWriteProposal,
) -> tuple[ArticleWriteProposal, str | None]:
    """Repair a model-mangled source name for the single capture of this run.

    The model occasionally rewrites a long capture file name while still clearly
    producing an article from the only capture it was given. Because each run now
    processes exactly one capture (#153), replacing a missing/empty source path
    with that capture's exact path is safe whenever the proposal's source(s) look
    like a mangled version of it — and is safer than blocking the drain forever.
    """
    if not proposal.sources:
        return proposal, None
    capture_root = (brain_home / "capture").resolve()
    actual_path = capture_path.resolve()
    resolved_sources: list[Path] = []
    for source in proposal.sources:
        try:
            source_path = _resolve_capture_source(brain_home, source)
        except (OSError, RuntimeError, ValueError):
            return proposal, None
        if not source_path.is_relative_to(capture_root):
            return proposal, None
        resolved_sources.append(source_path)
    if all(source_path == actual_path and source_path.exists() for source_path in resolved_sources):
        return proposal, None
    if not all(_looks_like_mangled_single_capture_source(source_path, actual_path) for source_path in resolved_sources):
        return proposal, None
    repaired_source = str(actual_path)
    frontmatter = dict(proposal.frontmatter)
    if "sources" in frontmatter:
        frontmatter["sources"] = [repaired_source]
    return proposal.model_copy(update={"sources": [repaired_source], "frontmatter": frontmatter}), (
        f"Repaired compile proposal source path for single-capture run: {proposal.relative_path}"
    )


def _looks_like_mangled_single_capture_source(source_path: Path, actual_path: Path) -> bool:
    if source_path == actual_path and source_path.exists():
        return False
    if source_path.parent != actual_path.parent:
        return False
    if source_path.suffix != actual_path.suffix or actual_path.suffix != ".md":
        return False
    similarity = SequenceMatcher(None, source_path.stem, actual_path.stem).ratio()
    return similarity >= 0.78


def _apply_capture_proposals(
    *,
    settings: Settings,
    request: CompileRunRequest,
    store_mode: bool,
    brain_store_root: Path | None,
    vault_paths: dict[str, Path],
    manifests: dict[str, dict],
    capture_proposals: list[ArticleWriteProposal],
    capture_source: Path,
    single_allowed: set[Path],
    processed_sources: set[Path],
    simulated_article_paths: dict[str, set[str]],
    simulated_target_paths: set[Path],
    applied: list[AppliedArticleWrite],
    applied_store_targets: list[Path],
    processed_capture_paths: set[Path],
    errors: list[str],
) -> tuple[bool, bool]:
    """Validate + apply one capture's proposal(s).  Returns ``(applied_ok, failed)``.

    ``applied_ok`` is True only when a proposal that CITES ``capture_source``
    validated+applied (#153 Fix 1): a proposal that applies but cites only a
    prior-processed source must NOT make THIS capture "accounted", or the capture
    would be archived without being captured into any article (#149 silent loss).
    """
    capture_applied_ok = False
    capture_failed = False

    # Re-scan existing targets each capture so a duplicate-create against an article
    # applied earlier this run is detected (apply-before-next).  In dry-run nothing is
    # written to disk, so also seed with the targets simulated earlier this run
    # (#153 Fix 3) — otherwise two creates of the same target both pass the preview
    # while a real apply rejects the second.
    known_existing_targets: set[Path] = set(simulated_target_paths)
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

    for proposal in capture_proposals:
        try:
            if store_mode:
                assert brain_store_root is not None
                target = validate_store_article_write(
                    proposal, brain_store_root, settings.brain_home, single_allowed, known_existing_targets,
                    processed_sources=processed_sources,
                )
            else:
                target = validate_article_write(
                    proposal, vault_paths, manifests, settings.brain_home, single_allowed, known_existing_targets,
                    processed_sources=processed_sources,
                )
            if proposal.operation == "create":
                known_existing_targets.add(target)
            apply_article_write(target, proposal, request.dry_run)
            if store_mode:
                assert brain_store_root is not None
                update_indexes_for_article(brain_store_root, target, proposal.title, proposal.summary, request.dry_run)
            else:
                update_directory_index(target, proposal.title, proposal.summary, request.dry_run)
            simulated_article_paths.setdefault(proposal.vault, set()).add(proposal.relative_path)
            simulated_target_paths.add(target)
            # #153 Fix 1: this capture is only "accounted" when the applied proposal
            # cites THIS capture's source (single_allowed == {capture_source}).  A
            # proposal citing only a prior-processed source applies but must NOT mark
            # the current capture done — it falls to the #150 retry path.
            cites_capture = any(
                _resolve_capture_source(settings.brain_home, source) in single_allowed
                for source in proposal.sources
            )
            if cites_capture:
                capture_applied_ok = True
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
                if cites_capture:
                    processed_capture_paths.add(capture_source)
        except (PolicyError, ValueError, OSError) as exc:
            # A single bad proposal must NOT abort the others (#153): record the
            # error and route THIS capture to the retry path.
            errors.append(f"{proposal.vault}/{proposal.relative_path}: {exc}")
            capture_failed = True

    return capture_applied_ok, capture_failed


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
    all_proposals: list[ArticleWriteProposal] = []
    all_skipped: list[str] = []
    nonfatal_warnings: list[str] = []
    simulated_article_paths: dict[str, set[str]] = {name: set() for name in manifests} if not store_mode else {"brain": set()}
    # Resolved target Paths applied/simulated so far this run.  In dry-run nothing is
    # written to disk, so this is how a duplicate-create against an earlier same-run
    # target is still detected in the preview (#153 Fix 3).
    simulated_target_paths: set[Path] = set()

    # --- Approval gate (#153): gate up-front on how many captures this run will
    # process.  In the per-capture model "number of proposals" is no longer known
    # before the loop runs, so we gate on the capture count instead: if it exceeds
    # the threshold without a matching approval token, block the WHOLE run and
    # apply nothing (the captures stay pending for a later approved run). ---
    approval_blocked = (
        not request.dry_run
        and len(capture_paths) > settings.large_batch_threshold
        and not settings.approval_matches(request.approval_token)
    )
    if approval_blocked:
        errors.append(
            f"Large compile run requires approval: {len(capture_paths)} captures exceeds threshold {settings.large_batch_threshold}"
        )

    # In store mode, resolve the store root once outside the loop — but only when the
    # run will actually proceed (#153 Fix 4): a blocked run must not create the store
    # dir on disk.
    brain_store_root = ensure_store_root(settings) if store_mode and not approval_blocked else None

    vault_names = ["brain"] if store_mode else sorted(manifests)
    # Capture sources already recorded as processed by prior runs — used so an
    # update proposal may re-cite an already-archived capture (#123).
    processed_capture_record = _load_processed_captures(settings.brain_home)
    processed_sources: set[Path] = {
        _resolve_capture_source(settings.brain_home, key) for key in processed_capture_record
    }

    # Track captures that reach a terminal state this run (applied or skipped) so
    # they are marked processed/archived, and those that don't so they go to the
    # #150 retry/quarantine path — both accumulated across the per-capture loop.
    processed_capture_paths: set[Path] = set()
    applied_store_targets: list[Path] = []

    # Process EXACTLY ONE capture per agent.run (#153).  Each iteration recomputes
    # article_paths so it reflects everything applied so far (apply-before-next),
    # runs the agent for that single capture, then validates/applies/accounts for
    # it immediately so the next capture can choose update-over-create.
    for capture_path in [] if approval_blocked else capture_paths:
        capture_source = capture_path.resolve()
        single_allowed = {capture_source}

        # Recompute the live + simulated article paths so this capture sees every
        # article applied by earlier captures in this run.
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
            related = await find_related_articles(
                settings,
                read_capture(capture_path, settings.capture_max_chars),
                store_root=brain_store_root,
                top_k=settings.correlation_top_k,
                char_budget=settings.correlation_max_chars,
            )
        else:
            related = []

        agent = build_compile_agent(settings, skill_text)
        deps = CompileDeps(
            capture_paths=[capture_path],
            vault_names=vault_names,
            article_paths=article_paths,
            capture_max_chars=settings.capture_max_chars,
            related_articles=related,
        )
        if store_mode:
            prompt = f"""
Compile exactly one capture.

Call load_compile_context exactly once. Then return final structured output.
Do not invent vault names or source paths. You may update knowledge created by earlier captures in this run.

Destination: brain knowledge store (~/.brain/knowledge).
Set vault to "brain" for all proposals.
Set relative_path to <scope>/<section>/<slug>.md where scope is "common" or a project slug, and section is one of: decisions, lessons, runbooks, context.
{deps.vault_names}
""".strip()
        else:
            prompt = f"""
Compile exactly one capture.

Call load_compile_context exactly once. Then return final structured output.
Do not invent vault names or source paths. You may update knowledge created by earlier captures in this run.

Available vaults:
{deps.vault_names}
""".strip()

        # Per-capture state, declared before the (failure-prone) agent.run so the
        # #150 accounting below always has them defined (#153 Fix 2).
        capture_applied_ok = False
        capture_failed = False
        capture_skipped: list[str] = []

        # ---- Run the agent + validate/apply for THIS capture, isolated so an
        # exception on one capture (unstable LLM endpoint: transport error,
        # UsageLimitExceeded, etc.) does NOT abort the whole run (#153 Fix 2). The
        # failing capture is surfaced and routed to the #150 retry path; later
        # captures still run and earlier captures' post-loop mark/archive still
        # happens. ----
        try:
            result = await agent.run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=AGENT_REQUEST_LIMIT))
            output = result.output

            # Repair a model-mangled source name for this one capture, then validate +
            # apply its proposal(s) (1:1 coverage, #153).  Proposals are NOT pre-filtered
            # by source: validation requires every cited source to be in single_allowed,
            # so a missing/hallucinated/stray source is surfaced as an error and routes
            # this capture to the retry path — never silently dropped.
            capture_proposals = [
                _repair_single_capture_source(settings.brain_home, capture_path, proposal)
                for proposal in output.proposals
            ]
            capture_proposals_repaired: list[ArticleWriteProposal] = []
            for repaired, warning in capture_proposals:
                capture_proposals_repaired.append(repaired)
                if warning:
                    nonfatal_warnings.append(warning)
            capture_proposals = capture_proposals_repaired
            # Keep only skips that account for THIS capture; a skip citing some other
            # path must not mark this capture done.
            capture_skipped = [
                item
                for item in output.skipped
                if _skipped_capture_paths(settings.brain_home, single_allowed, [item])
            ]
            all_proposals.extend(capture_proposals)
            all_skipped.extend(capture_skipped)

            capture_applied_ok, capture_failed = _apply_capture_proposals(
                settings=settings,
                request=request,
                store_mode=store_mode,
                brain_store_root=brain_store_root,
                vault_paths=vault_paths,
                manifests=manifests,
                capture_proposals=capture_proposals,
                capture_source=capture_source,
                single_allowed=single_allowed,
                processed_sources=processed_sources,
                simulated_article_paths=simulated_article_paths,
                simulated_target_paths=simulated_target_paths,
                applied=applied,
                applied_store_targets=applied_store_targets,
                processed_capture_paths=processed_capture_paths,
                errors=errors,
            )
        except Exception as exc:  # noqa: BLE001 — one capture's failure must not abort the run (#153)
            errors.append(f"{capture_path.name}: agent run failed: {exc}")
            capture_failed = True

        # ---- Per-capture #150 accounting (1:1) ----
        # Applied → processed; explicitly skipped (and nothing failed) → processed +
        # archived; uncovered OR a failed/invalid proposal → increment this
        # capture's attempt counter and leave it pending / quarantine per #150.
        if not request.dry_run and not approval_blocked:
            skipped_here = _skipped_capture_paths(settings.brain_home, single_allowed, capture_skipped)
            accounted = capture_applied_ok or (bool(skipped_here) and not capture_failed)
            if accounted:
                processed_capture_paths.update(skipped_here)
                processed_capture_paths.add(capture_source)
                # Reached a terminal state — drop any stale attempt counter.
                clear_capture_attempts(settings.brain_home, {capture_source})
            else:
                attempt_counts = increment_capture_attempts(settings.brain_home, {capture_source})
                counts = attempt_counts.get(capture_source)
                if counts is not None and (
                    counts.count >= COMPILE_MAX_CAPTURE_ATTEMPTS
                    or counts.total >= COMPILE_MAX_CAPTURE_ATTEMPTS_ABSOLUTE
                ):
                    quarantined = quarantine_captures(settings.brain_home, [capture_path], capture_hashes)
                    if quarantined:
                        append_global_log(
                            settings.brain_home,
                            "COMPILE",
                            f"quarantined {len(quarantined)} captures with no agent proposal/skip after "
                            f"{COMPILE_MAX_CAPTURE_ATTEMPTS} attempts -> capture/quarantine: "
                            + ", ".join(sorted(path.name for path in quarantined)),
                            request.dry_run,
                        )
                        # The file is physically moved out of inbox, so discovery
                        # stops finding it — drop its now-stale attempt counter.
                        clear_capture_attempts(settings.brain_home, {capture_source})

    # Mark + archive all processed captures once, after the loop.
    if not request.dry_run and processed_capture_paths:
        sorted_processed_paths = sorted(processed_capture_paths)
        mark_captures_processed(settings.brain_home, sorted_processed_paths, capture_hashes)
        try:
            archive_processed_inbox_captures(settings.brain_home, sorted_processed_paths, capture_hashes)
        except Exception as exc:  # noqa: BLE001 - inbox archive cleanup must not fail applied compile work.
            nonfatal_warnings.append(f"Processed capture archive cleanup failed after compile apply: {exc}")

    # Reconciliation runs over the articles that WERE applied this run.  It is no
    # longer gated on ``not errors`` (#153 error isolation): one bad capture no
    # longer suppresses reconciliation of the good ones, and ``applied_store_targets``
    # already contains only successfully-applied articles.
    if (
        store_mode
        and not request.dry_run
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
                    usage_limits=UsageLimits(request_limit=AGENT_REQUEST_LIMIT),
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
