"""Tests for WI12: mirror agent summarization, live-fetch, and run_mirror."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fritz_local_brain import mirror
from fritz_local_brain.config import Settings
from fritz_local_brain.live_fetch import live_fetch
from fritz_local_brain.mirror import run_mirror
from fritz_local_brain.models import MirrorSummary
from fritz_local_brain.registry import ExternalTarget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    brain_home = tmp_path / "brain"
    (brain_home / "capture" / "inbox").mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
    )


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    assert lines[0].rstrip() == "---", f"Expected frontmatter in {path}"
    closing = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            closing = i
            break
    assert closing is not None
    return yaml.safe_load("".join(lines[1:closing])) or {}


def _read_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) == 3 else text


class FakeMirrorAgent:
    """Stands in for ``build_mirror_agent``'s return value."""

    def __init__(self, summary: MirrorSummary) -> None:
        self.summary = summary
        self.calls: list[object] = []

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.calls.append(deps)
        return SimpleNamespace(output=self.summary)


# ===========================================================================
# 1. full-summary target -> summary body + mode: full
# ===========================================================================


def test_run_mirror_full_summary_writes_summary_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    (vault / "doc.md").write_text("# Doc\n\n" + "ORIGINAL " * 50, encoding="utf-8")

    summary = MirrorSummary(
        title="Summarized Doc",
        summary="A faithful concise summary.",
        key_points=["point one", "point two"],
    )
    fake = FakeMirrorAgent(summary)
    monkeypatch.setattr(mirror, "build_mirror_agent", lambda s: fake)

    target = ExternalTarget(
        name="ext", kind="local-vault", connection=str(vault), mirror_mode="full-summary"
    )
    results = asyncio.run(
        run_mirror(settings, targets=[target], mirrored_at="2026-01-01T00:00:00")
    )

    assert len(results) == 1
    assert results[0].entries_mirrored == 1
    written = Path(results[0].written_paths[0])
    assert written.exists()

    fm = _read_frontmatter(written)
    assert fm["mode"] == "full"
    assert fm["pointer"] == "ext:doc.md"
    assert fm["source"] == "ext (local-vault)"
    assert fm["mirrored_at"] == "2026-01-01T00:00:00"
    assert fm["title"] == "Summarized Doc"

    body = _read_body(written)
    assert "A faithful concise summary." in body
    assert "point one" in body
    # The original raw content must NOT be the body verbatim.
    assert "ORIGINAL ORIGINAL" not in body
    # The fake agent was actually invoked.
    assert len(fake.calls) == 1


def test_run_mirror_uses_injected_summarizer(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nbody a", encoding="utf-8")

    seen: list[tuple[str, str, str]] = []

    async def fake_summarize(pointer: str, title: str, content: str) -> MirrorSummary:
        seen.append((pointer, title, content))
        return MirrorSummary(title=title, summary="injected summary")

    target = ExternalTarget(
        name="ext", kind="local-vault", connection=str(vault), mirror_mode="full-summary"
    )
    results = asyncio.run(
        run_mirror(settings, targets=[target], mirrored_at="T", summarize=fake_summarize)
    )

    assert seen and seen[0][0] == "ext:a.md"
    body = _read_body(Path(results[0].written_paths[0]))
    assert "injected summary" in body


# ===========================================================================
# 2. index-only target -> minimal body, no full content
# ===========================================================================


def test_run_mirror_index_only_writes_minimal_capture(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    secret_body = "SUPER_SECRET_FULL_CONTENT_TOKEN"
    (vault / "note.md").write_text(f"# Note\n\n{secret_body}", encoding="utf-8")

    target = ExternalTarget(
        name="ext", kind="local-vault", connection=str(vault), mirror_mode="index-only"
    )
    results = asyncio.run(run_mirror(settings, targets=[target], mirrored_at="T"))

    written = Path(results[0].written_paths[0])
    fm = _read_frontmatter(written)
    assert fm["mode"] == "index-only"
    assert fm["pointer"] == "ext:note.md"

    body = _read_body(written)
    # The full external content must NOT be stored.
    assert secret_body not in body
    assert "live-fetch" in body.lower()


def test_run_mirror_index_only_does_not_build_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody", encoding="utf-8")

    def _boom(_s):
        raise AssertionError("build_mirror_agent must not be called for index-only")

    monkeypatch.setattr(mirror, "build_mirror_agent", _boom)
    target = ExternalTarget(
        name="ext", kind="local-vault", connection=str(vault), mirror_mode="index-only"
    )
    results = asyncio.run(run_mirror(settings, targets=[target], mirrored_at="T"))
    assert results[0].entries_mirrored == 1


def test_run_mirror_dry_run_writes_nothing(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody", encoding="utf-8")

    target = ExternalTarget(
        name="ext", kind="local-vault", connection=str(vault), mirror_mode="index-only"
    )
    results = asyncio.run(run_mirror(settings, targets=[target], mirrored_at="T", dry_run=True))
    assert results[0].dry_run is True
    assert not Path(results[0].written_paths[0]).exists()


# ===========================================================================
# 3. live_fetch
# ===========================================================================


def _write_registry_with_target(brain_home: Path, name: str, connection: str) -> None:
    registry = {
        "external_targets": {
            name: {"kind": "local-vault", "connection": connection, "mirror_mode": "index-only"}
        }
    }
    brain_home.mkdir(parents=True, exist_ok=True)
    (brain_home / "registry.yaml").write_text(yaml.safe_dump(registry), encoding="utf-8")


def test_live_fetch_returns_live_content(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    (vault / "sub").mkdir(parents=True)
    (vault / "sub" / "live.md").write_text("# Live\n\nFRESH_CONTENT", encoding="utf-8")
    _write_registry_with_target(settings.brain_home, "ext", str(vault))

    content = live_fetch(settings, "ext:sub/live.md")
    assert content is not None
    assert "FRESH_CONTENT" in content


def test_live_fetch_unknown_target_returns_none(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    _write_registry_with_target(settings.brain_home, "ext", str(vault))

    assert live_fetch(settings, "nope:file.md") is None


def test_live_fetch_missing_pointer_file_returns_none(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    _write_registry_with_target(settings.brain_home, "ext", str(vault))

    assert live_fetch(settings, "ext:does-not-exist.md") is None


def test_live_fetch_malformed_pointer_returns_none(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    assert live_fetch(settings, "no-colon-here") is None


def test_live_fetch_path_escape_returns_none(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    # A secret file OUTSIDE the connection dir.
    (tmp_path / "secret.md").write_text("TOP_SECRET", encoding="utf-8")
    _write_registry_with_target(settings.brain_home, "ext", str(vault))

    assert live_fetch(settings, "ext:../secret.md") is None


def test_live_fetch_symlink_returns_none(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    vault = tmp_path / "ext"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE", encoding="utf-8")
    (vault / "link.md").symlink_to(outside)
    _write_registry_with_target(settings.brain_home, "ext", str(vault))

    assert live_fetch(settings, "ext:link.md") is None
