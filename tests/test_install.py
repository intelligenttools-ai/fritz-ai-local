"""Tests for the unified bootstrap/installer (issue #62).

``scripts/install.py`` bootstraps the brain and wires an agent, modeled on the
Pi binding's ``/fritz init|status|repair-hooks|smoke-test`` bootstrap
(``bindings/pi/index.ts``). These tests exercise the ported behavior:

  - ``install`` creates missing brain dirs, symlinks the repo's Python hooks
    into ``<brain>/hooks/`` (resolving back to the real repo hooks), and
    installs the agent's skill variants;
  - pre-existing captures (inbox, log.md) are preserved byte-for-byte;
  - a re-run reports "already current" with no changes (idempotent);
  - claude/codex emit ``fritz:`` skills, pi emits ``fritz-``;
  - ``status`` reports per-platform health and mode;
  - ``repair`` recreates a deleted hook symlink;
  - ``smoke-test`` passes after install;
  - ``--dry-run`` writes nothing.

GUARDRAIL: every test points ``$BRAIN_HOME`` and ``--skills-dir`` at tmp dirs.
The live ``~/.brain`` / ``~/.agents`` / ``~/.claude`` / ``~/.codex`` and the
real ``$HOME`` are never written.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PY = REPO_ROOT / "scripts" / "install.py"


def _load_install():
    spec = importlib.util.spec_from_file_location("_install_under_test", INSTALL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def install_mod():
    return _load_install()


@pytest.fixture()
def synthetic_brain(tmp_path, monkeypatch):
    """A live-style brain COPY in tmp with pre-existing captures + log.md.

    Returns the brain root. Points $BRAIN_HOME at it and $FRITZ_REPO_PATH at the
    real repo so hook symlinks resolve back to the real hook files.
    """
    brain = tmp_path / "home" / ".brain"
    inbox = brain / "capture" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "existing-fact.md").write_text(
        "---\ntype: capture\n---\n# Existing\n\nDo not touch me.\n", encoding="utf-8"
    )
    (brain / "log.md").write_text("# Brain Operations Log\nseed line\n", encoding="utf-8")

    monkeypatch.setenv("BRAIN_HOME", str(brain))
    monkeypatch.setenv("FRITZ_REPO_PATH", str(REPO_ROOT))
    monkeypatch.delenv("FRITZ_SKILLS_DIR", raising=False)
    return brain


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(install_mod, argv):
    return install_mod.main(argv)


# --- install --agent pi -----------------------------------------------------


def test_install_pi_creates_dirs_links_hooks_and_skills(
    install_mod, synthetic_brain, tmp_path, capsys
):
    skills = tmp_path / "pi-skills"
    rc = _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    assert rc == 0

    # daily/ created if missing; auto/ too.
    assert (synthetic_brain / "capture" / "daily").is_dir()
    assert (synthetic_brain / "capture" / "auto").is_dir()

    # Hooks symlinked into <brain>/hooks/ and resolve back to the real repo hooks.
    for hook in install_mod.REQUIRED_HOOKS:
        dest = synthetic_brain / "hooks" / hook
        assert dest.is_symlink(), f"{hook} should be a symlink"
        assert dest.resolve() == (REPO_ROOT / "hooks" / hook).resolve()

    # pi skills generated as fritz-brain-* in the skills dir.
    assert (skills / "fritz-brain-query" / "SKILL.md").exists()
    assert (skills / "fritz-brain-setup" / "SKILL.md").exists()
    assert not (skills / "fritz:brain-query").exists()


def test_install_preserves_existing_captures_byte_for_byte(
    install_mod, synthetic_brain, tmp_path
):
    inbox_file = synthetic_brain / "capture" / "inbox" / "existing-fact.md"
    log_file = synthetic_brain / "log.md"
    before_inbox = _digest(inbox_file)
    before_log = _digest(log_file)

    skills = tmp_path / "pi-skills"
    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])

    assert _digest(inbox_file) == before_inbox
    assert _digest(log_file) == before_log


def test_install_is_idempotent_reports_already_current(
    install_mod, synthetic_brain, tmp_path, capsys
):
    skills = tmp_path / "pi-skills"
    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    capsys.readouterr()

    # Snapshot the whole brain + skills tree, then re-run.
    def snapshot(root: Path):
        return {
            str(p): (_digest(p) if p.is_file() and not p.is_symlink() else "<dir/link>")
            for p in sorted(root.rglob("*"))
        }

    before = {**snapshot(synthetic_brain), **snapshot(skills)}
    rc = _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    after = {**snapshot(synthetic_brain), **snapshot(skills)}
    out = capsys.readouterr().out

    assert rc == 0
    assert "already current" in out
    assert before == after, "re-run must change nothing"


# --- install --agent claude / codex (colon prefix) --------------------------


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_install_claude_codex_use_colon_prefix(
    install_mod, synthetic_brain, tmp_path, agent
):
    skills = tmp_path / f"{agent}-skills"
    rc = _run(install_mod, ["install", "--agent", agent, "--skills-dir", str(skills)])
    assert rc == 0
    assert (skills / "fritz:brain-query" / "SKILL.md").exists()
    assert not (skills / "fritz-brain-query").exists()


# --- status -----------------------------------------------------------------


def test_status_reports_minimal_before_and_full_after(
    install_mod, synthetic_brain, tmp_path, capsys
):
    skills = tmp_path / "pi-skills"

    # Before install: hooks not wired -> minimal-capture, skills missing.
    rc = _run(install_mod, ["status", "--agent", "pi", "--skills-dir", str(skills)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mode: minimal-capture" in out
    assert "hooks wired: False" in out

    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    capsys.readouterr()

    # After install: full mode, hooks wired, skills installed.
    _run(install_mod, ["status", "--agent", "pi", "--skills-dir", str(skills)])
    out = capsys.readouterr().out
    assert "mode: full" in out
    assert "hooks wired: True" in out
    assert "skills (pi): installed" in out


def test_status_json(install_mod, synthetic_brain, tmp_path, capsys):
    skills = tmp_path / "pi-skills"
    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    capsys.readouterr()
    rc = _run(
        install_mod, ["status", "--agent", "pi", "--skills-dir", str(skills), "--json"]
    )
    import json

    report = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert report["mode"] == "full"
    assert report["hooks_wired"] is True
    assert report["skills_installed"] is True


# --- repair -----------------------------------------------------------------


def test_repair_recreates_deleted_hook_symlink(
    install_mod, synthetic_brain, tmp_path, capsys
):
    skills = tmp_path / "pi-skills"
    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    capsys.readouterr()

    victim = synthetic_brain / "hooks" / "brain_common.py"
    victim.unlink()
    assert install_mod.path_state(victim) == "missing"

    rc = _run(install_mod, ["repair", "--agent", "pi"])
    out = capsys.readouterr().out
    assert rc == 0
    assert victim.is_symlink()
    assert victim.resolve() == (REPO_ROOT / "hooks" / "brain_common.py").resolve()
    assert "brain_common.py" in out


def test_repair_keeps_existing_non_symlink_hook(
    install_mod, synthetic_brain, tmp_path, capsys
):
    skills = tmp_path / "pi-skills"
    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    capsys.readouterr()

    # Replace a symlink with a real file; repair must KEEP it.
    real = synthetic_brain / "hooks" / "brain_security.py"
    real.unlink()
    real.write_text("# local override\n", encoding="utf-8")

    _run(install_mod, ["repair", "--agent", "pi"])
    out = capsys.readouterr().out
    assert not real.is_symlink()
    assert real.read_text(encoding="utf-8") == "# local override\n"
    assert "kept existing non-symlink hook" in out


# --- smoke-test -------------------------------------------------------------


def test_smoke_test_passes_after_install(
    install_mod, synthetic_brain, tmp_path, capsys
):
    skills = tmp_path / "pi-skills"
    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    capsys.readouterr()

    rc = _run(install_mod, ["smoke-test", "--agent", "pi", "--cwd", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ALL PASS" in out
    assert "PASS hook brain_session_start.py" in out


def test_smoke_test_fails_without_hooks(install_mod, synthetic_brain, tmp_path, capsys):
    rc = _run(install_mod, ["smoke-test", "--agent", "pi", "--cwd", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out


# --- --dry-run --------------------------------------------------------------


def test_install_dry_run_writes_nothing(install_mod, synthetic_brain, tmp_path, capsys):
    skills = tmp_path / "pi-skills"

    def snapshot():
        items = {}
        for root in (synthetic_brain, skills):
            if root.exists():
                for p in sorted(root.rglob("*")):
                    items[str(p)] = (
                        _digest(p)
                        if p.is_file() and not p.is_symlink()
                        else "<dir/link>"
                    )
        return items

    before = snapshot()
    rc = _run(
        install_mod,
        ["install", "--agent", "pi", "--skills-dir", str(skills), "--dry-run"],
    )
    out = capsys.readouterr().out
    after = snapshot()

    assert rc == 0
    assert before == after, "dry-run must not write anything"
    assert not skills.exists(), "dry-run must not create the skills dir"
    # daily/auto were missing in the synthetic brain -> must NOT be created.
    assert not (synthetic_brain / "capture" / "daily").exists()
    assert "[DRY RUN]" in out
    assert "would create dir" in out


def test_repair_dry_run_writes_nothing(install_mod, synthetic_brain, tmp_path, capsys):
    skills = tmp_path / "pi-skills"
    _run(install_mod, ["install", "--agent", "pi", "--skills-dir", str(skills)])
    capsys.readouterr()

    victim = synthetic_brain / "hooks" / "brain_capture.py"
    victim.unlink()

    rc = _run(install_mod, ["repair", "--agent", "pi", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert not victim.exists(), "dry-run repair must not recreate the symlink"
    assert "[DRY RUN]" in out


# --- helpers / unit ---------------------------------------------------------


def test_path_state_ok_missing_broken(install_mod, tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("x", encoding="utf-8")
    assert install_mod.path_state(real) == "ok"

    link = tmp_path / "link"
    link.symlink_to(real)
    assert install_mod.path_state(link) == "ok"

    missing = tmp_path / "nope"
    assert install_mod.path_state(missing) == "missing"

    broken = tmp_path / "broken"
    broken.symlink_to(tmp_path / "does-not-exist")
    assert install_mod.path_state(broken) == "broken-symlink"


def test_skills_dir_override_precedence(install_mod, tmp_path, monkeypatch):
    # Explicit override wins over env and default.
    monkeypatch.setenv("FRITZ_SKILLS_DIR", str(tmp_path / "from-env"))
    assert install_mod.skills_dir_for("pi", str(tmp_path / "from-flag")) == (
        tmp_path / "from-flag"
    )
    # Env wins over default when no flag.
    assert install_mod.skills_dir_for("pi", None) == (tmp_path / "from-env")
    # Default per-agent when neither set.
    monkeypatch.delenv("FRITZ_SKILLS_DIR", raising=False)
    assert install_mod.skills_dir_for("claude", None) == install_mod.AGENT_SKILLS_DIR[
        "claude"
    ]


def test_brain_home_env_override(install_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path / "b"))
    assert install_mod.brain_home() == tmp_path / "b"
    monkeypatch.delenv("BRAIN_HOME", raising=False)
    assert install_mod.brain_home() == Path.home() / ".brain"
