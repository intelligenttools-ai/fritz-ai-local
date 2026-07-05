from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".forgejo" / "workflows" / "sync-github.yml"


def _sensitive_filename_guard_condition() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r'if (?P<condition>find "\$work_dir" -path "\$work_dir/\.git" '
        r"-prune -o -type f .*?\| grep -q \.); then",
        workflow,
        flags=re.DOTALL,
    )
    assert match, "sync-github filename guard condition not found"
    return textwrap.dedent(match.group("condition"))


def _guard_matches(work_dir: Path) -> bool:
    script = f"""
    set -euo pipefail
    work_dir="$1"
    if {_sensitive_filename_guard_condition()}; then
      exit 42
    fi
    """
    proc = subprocess.run(
        ["bash", "-c", textwrap.dedent(script), "bash", str(work_dir)],
        check=False,
    )
    assert proc.returncode in {0, 42}
    return proc.returncode == 42


def _tracked_file(work_dir: Path, relative_path: str) -> None:
    path = work_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# test fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work_dir), "add", relative_path], check=True)


def test_sync_github_filename_guard_allows_tracked_token_named_python_files(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "public"
    work_dir.mkdir()
    subprocess.run(["git", "-C", str(work_dir), "init", "-q"], check=True)

    _tracked_file(work_dir, "migrations/004-wire-mcp-token-env.py")
    _tracked_file(work_dir, "tests/test_mcp_token_wiring.py")
    _tracked_file(work_dir, "services/secret_rotation.py")

    assert not _guard_matches(work_dir)


def test_sync_github_filename_guard_still_blocks_sensitive_names(tmp_path: Path) -> None:
    blocked_names = [
        "foo.token",
        "secrets.yaml",
        ".env",
        "x.pem",
        "id_rsa",
    ]

    for name in blocked_names:
        work_dir = tmp_path / name.replace("/", "_").replace(".", "_")
        work_dir.mkdir()
        subprocess.run(["git", "-C", str(work_dir), "init", "-q"], check=True)
        _tracked_file(work_dir, name)

        assert _guard_matches(work_dir), name


def test_sync_github_filename_guard_blocks_untracked_token_named_python_files(
    tmp_path: Path,
) -> None:
    blocked_names = [
        "tests/untracked_token_case.py",
        "services/secret_rotation.py",
    ]

    for name in blocked_names:
        work_dir = tmp_path / name.replace("/", "_").replace(".", "_")
        work_dir.mkdir()
        subprocess.run(["git", "-C", str(work_dir), "init", "-q"], check=True)
        path = work_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# untracked fixture\n", encoding="utf-8")

        assert _guard_matches(work_dir), name
