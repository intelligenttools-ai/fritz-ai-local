"""Brain vault lint agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..manifests import resolve_manifest_path
from ..models import LintFinding


@dataclass
class BrainLintAgent:
    """Policy-bound deterministic lint executor."""

    skill_text: str

    def lint_vault(self, vault: str, vault_path: Path, registry_config: dict[str, Any], manifest: dict[str, Any] | None) -> list[LintFinding]:
        findings: list[LintFinding] = []
        if not vault_path.exists():
            findings.append(_finding(vault, "error", vault_path, "Registered vault path does not exist"))
            return findings
        if manifest is None:
            findings.append(_finding(vault, "error", vault_path / ".brain" / "manifest.yaml", "Vault manifest is missing"))
            return findings

        knowledge_root = resolve_manifest_path(vault_path, manifest, "knowledge")
        if knowledge_root is None:
            findings.append(_finding(vault, "error", vault_path, "Manifest knowledge path is missing or escapes the vault"))
        elif not knowledge_root.exists():
            findings.append(_finding(vault, "error", knowledge_root, "Manifest knowledge path does not exist"))

        sync_target = str(registry_config.get("sync") or "local").strip().lower()
        if sync_target not in {"none", "local", "git"}:
            findings.append(_finding(vault, "warning", vault_path, f"Sync target is unsupported by service MVP: {sync_target}"))
        return findings


def _finding(vault: str, severity: str, path: Path, message: str) -> LintFinding:
    return LintFinding(vault=vault, severity=severity, path=str(path), message=message)
