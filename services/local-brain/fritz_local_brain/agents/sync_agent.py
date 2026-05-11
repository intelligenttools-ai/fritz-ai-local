"""Conservative Brain sync agent."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..manifests import resolve_manifest_path
from ..models import SyncVaultResult
from ..security import is_excluded


SUPPORTED_TARGETS = {"none", "local", "git"}


@dataclass
class BrainSyncAgent:
    """Policy-bound sync executor.

    The loaded skill text is retained as trusted policy context. The initial
    implementation keeps execution deterministic and only permits local no-op
    targets or explicit git pushes.
    """

    skill_text: str
    allow_first_external_sync: bool

    def run_vault(
        self,
        vault: str,
        vault_path: Path,
        registry_config: dict[str, Any],
        manifest: dict[str, Any],
        dry_run: bool,
    ) -> SyncVaultResult:
        target = str(registry_config.get("sync") or "local").strip().lower()
        result = SyncVaultResult(vault=vault, target=target, first_sync=False)

        if target not in SUPPORTED_TARGETS:
            result.skipped.append(f"Unsupported sync target for service MVP: {target}")
            return result
        if target == "none":
            result.skipped.append("Vault sync is disabled")
            return result
        if target == "local":
            result.skipped.append("Vault is local-only; no external sync needed")
            return result

        knowledge_root = resolve_manifest_path(vault_path, manifest, "knowledge")
        if knowledge_root is None or not knowledge_root.exists():
            result.errors.append("Vault has no readable knowledge path")
            return result

        last_sync = _last_sync_at(vault_path)
        result.first_sync = last_sync is None
        articles = _articles_to_sync(knowledge_root, vault_path, manifest, last_sync)
        result.articles_considered = len(articles)
        result.articles_to_sync = [str(path.relative_to(knowledge_root)) for path in articles]

        if not articles:
            result.skipped.append("No knowledge articles changed since last sync")
            return result
        if result.first_sync and not dry_run and not self.allow_first_external_sync:
            result.errors.append("First external sync is blocked; set ALLOW_FIRST_EXTERNAL_SYNC=true to permit it")
            return result

        if dry_run:
            result.skipped.append("Dry run only; git push was not executed")
            return result

        _git_push(vault_path, result)
        return result


def _last_sync_at(vault_path: Path) -> datetime | None:
    candidates = [vault_path / ".brain" / "log.md"]
    seen: list[datetime] = []
    for log_path in candidates:
        if not log_path.exists():
            continue
        for line in log_path.read_text(encoding="utf-8").splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 2 or parts[1] != "SYNC":
                continue
            try:
                seen.append(datetime.strptime(parts[0], "%Y-%m-%d %H:%M"))
            except ValueError:
                continue
    if not seen:
        return None
    return max(seen)


def _articles_to_sync(
    knowledge_root: Path,
    vault_path: Path,
    manifest: dict[str, Any],
    last_sync: datetime | None,
) -> list[Path]:
    articles: list[Path] = []
    for path in sorted(knowledge_root.glob("**/*.md")):
        if is_excluded(path, vault_path, manifest):
            continue
        if last_sync is None or datetime.fromtimestamp(path.stat().st_mtime) > last_sync:
            articles.append(path)
    return articles


def _git_push(vault_path: Path, result: SyncVaultResult) -> None:
    if not (vault_path / ".git").exists():
        result.errors.append("Vault is not a git repository")
        return

    status = subprocess.run(
        ["git", "-C", str(vault_path), "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        result.errors.append(status.stderr.strip() or "git status failed")
        return
    if status.stdout.strip():
        result.errors.append("Git sync requires committed vault changes before push")
        return

    push = subprocess.run(
        ["git", "-C", str(vault_path), "push"],
        check=False,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        result.errors.append(push.stderr.strip() or "git push failed")
        return
    result.pushed = True
