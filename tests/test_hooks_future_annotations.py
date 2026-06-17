"""Regression test for issue #138.

Asserts that each shared hook that uses PEP 604 union syntax declares
``from __future__ import annotations`` so that the hooks load on Python 3.9.

The check is done by AST-parsing each file and inspecting the first statement
— no 3.9 interpreter is required on the box.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"

AFFECTED_HOOKS = [
    "brain_autocapture.py",
    "brain_common.py",
    "brain_prompt_check.py",
    "brain_save_fact.py",
    "brain_session_start.py",
    "setup_hyphenated_skills.py",
]


def _has_future_annotations(path: Path) -> bool:
    """Return True if the module declares ``from __future__ import annotations``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__" and any(
                alias.name == "annotations" for alias in node.names
            ):
                return True
    return False


@pytest.mark.parametrize("hook_name", AFFECTED_HOOKS)
def test_future_annotations_present(hook_name: str) -> None:
    path = HOOKS_DIR / hook_name
    assert path.exists(), f"hook not found: {path}"
    assert _has_future_annotations(path), (
        f"{hook_name} is missing 'from __future__ import annotations' "
        f"— required for Python 3.9 compatibility (issue #138)"
    )
