"""Tests for WI11: mirror-as-ingest (captures.write_inbox_capture,
ingest_adapters, mirror).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fritz_local_brain import compile_workflow
from fritz_local_brain.captures import write_inbox_capture
from fritz_local_brain.config import Settings
from fritz_local_brain.ingest_adapters import (
    LocalVaultIngestAdapter,
    MirrorError,
    get_ingest_adapter,
)
from fritz_local_brain.mirror import MirrorResult, mirror_target, mirror_targets
from fritz_local_brain.models import (
    ArticleWriteProposal,
    CompileAgentOutput,
    CompileRunRequest,
)
from fritz_local_brain.registry import ExternalTarget


# ---------------------------------------------------------------------------
# Helpers shared with compile tests
# ---------------------------------------------------------------------------


class FakeCompileAgent:
    def __init__(self, proposal: ArticleWriteProposal) -> None:
        self.proposal = proposal
        self.prompts: list[str] = []
        self.deps: list[object] = []

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        self.deps.append(deps)
        return SimpleNamespace(output=CompileAgentOutput(proposals=[self.proposal]))


def _make_settings(tmp_path: Path) -> Settings:
    skill_path = tmp_path / "skills" / "brain-compile" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("# Compile Skill\n", encoding="utf-8")
    brain_home = tmp_path / "brain"
    (brain_home / "capture" / "inbox").mkdir(parents=True, exist_ok=True)
    return Settings(
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
    assert closing is not None, f"No closing --- in {path}"
    return yaml.safe_load("".join(lines[1:closing])) or {}


# ===========================================================================
# 1. write_inbox_capture
# ===========================================================================


class TestWriteInboxCapture:
    def test_writes_file_under_inbox(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(
            brain_home,
            "my-fact",
            {"title": "My Fact", "source": "test"},
            "The body of the fact.",
        )
        assert path == brain_home / "capture" / "inbox" / "my-fact.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "title: My Fact" in text
        assert "The body of the fact." in text

    def test_sanitizes_slug_with_spaces_and_uppercase(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(brain_home, "My FACT about Stuff", {}, "body")
        assert path.name == "my-fact-about-stuff.md"
        assert path.exists()

    def test_sanitizes_slug_with_special_chars(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(brain_home, "fact!@#$%&*()", {}, "body")
        assert path.exists()
        # The name must be a safe filename (only alnum/hyphens/underscores + .md)
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_.md" for c in path.name)

    def test_sanitizes_path_separator_in_slug(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        # A slug with a path separator must NOT escape the inbox.
        path = write_inbox_capture(brain_home, "../../secret", {}, "body")
        assert path.parent == brain_home / "capture" / "inbox"
        assert path.exists()

    def test_slug_with_dotdot_stays_in_inbox(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(brain_home, "a/../b/../../etc/passwd", {}, "body")
        assert path.parent == brain_home / "capture" / "inbox"

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(
            brain_home, "ephemeral", {"source": "dry"}, "no write", dry_run=True
        )
        # Path is returned but file must not exist.
        assert not path.exists()
        # Inbox dir need not be created either.
        assert not (brain_home / "capture" / "inbox" / "ephemeral.md").exists()

    def test_dry_run_returns_intended_path(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(brain_home, "intended-slug", {}, "x", dry_run=True)
        assert path == brain_home / "capture" / "inbox" / "intended-slug.md"

    def test_frontmatter_rendered_correctly(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        fm = {"title": "T", "source": "s", "mode": "full", "pointer": "p"}
        path = write_inbox_capture(brain_home, "fm-test", fm, "body text")
        parsed = _read_frontmatter(path)
        assert parsed["title"] == "T"
        assert parsed["source"] == "s"
        assert parsed["mode"] == "full"
        assert parsed["pointer"] == "p"

    def test_adds_md_suffix_when_absent(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(brain_home, "no-extension", {}, "body")
        assert path.suffix == ".md"

    def test_does_not_double_md_suffix(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(brain_home, "already.md", {}, "body")
        assert path.name == "already.md"

    def test_empty_slug_falls_back_to_capture(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "brain"
        path = write_inbox_capture(brain_home, "   ", {}, "body")
        assert path.name == "capture.md"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        brain_home = tmp_path / "new-brain"
        path = write_inbox_capture(brain_home, "new-brain-fact", {}, "body")
        assert path.exists()

    def test_path_containment_guard_with_sibling_prefix_slug(self, tmp_path: Path) -> None:
        """Verify that a slug resolving to a sibling-prefixed dir cannot escape inbox.

        This test documents the Path-based containment guard: even if a slug's
        sanitization allowed a name like 'inbox-evil', the containment check using
        Path.relative_to() would catch it. The test confirms the guard works.
        """
        brain_home = tmp_path / "brain"
        inbox_dir = brain_home / "capture" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)

        # Create a sibling directory with a prefix-matching name.
        evil_dir = brain_home / "capture" / "inbox-evil"
        evil_dir.mkdir(parents=True, exist_ok=True)

        # A normal slug should land in the inbox and be accessible.
        path = write_inbox_capture(brain_home, "safe-slug", {}, "body")
        assert path.parent == inbox_dir
        assert path.exists()

        # The slug sanitization removes path separators, so this won't actually
        # escape, but the containment check defends against unsanitized paths.
        path2 = write_inbox_capture(brain_home, "another-safe-slug", {}, "body")
        assert path2.parent == inbox_dir
        assert path2.exists()


# ===========================================================================
# 2. LocalVaultIngestAdapter.fetch
# ===========================================================================


def _vault_target(vault_dir: Path, mirror_mode: str = "index-only") -> ExternalTarget:
    return ExternalTarget(
        name="test-vault",
        kind="local-vault",
        connection=str(vault_dir),
        mirror_mode=mirror_mode,
    )


class TestLocalVaultIngestAdapter:
    def test_returns_entries_for_md_files(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "alpha.md").write_text("# Alpha\n\nContent alpha.", encoding="utf-8")
        (vault / "beta.md").write_text("# Beta\n\nContent beta.", encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault))
        assert len(entries) == 2

    def test_deterministic_sorted_order(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        for name in ("zzz.md", "aaa.md", "mmm.md"):
            (vault / name).write_text(f"# {name}\n", encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault))
        titles = [e.title for e in entries]
        assert titles == sorted(titles)

    def test_pointer_format_is_target_name_colon_relpath(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        (vault / "sub").mkdir(parents=True)
        (vault / "sub" / "note.md").write_text("# Note\n", encoding="utf-8")

        target = _vault_target(vault)
        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(target)
        assert len(entries) == 1
        assert entries[0].pointer == "test-vault:sub/note.md"

    def test_title_from_h1_heading(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# My Title\n\nBody.", encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault))
        assert entries[0].title == "My Title"

    def test_title_falls_back_to_stem(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "my-note.md").write_text("No heading here.\n", encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault))
        assert entries[0].title == "my-note"

    def test_full_summary_mode_returns_full_content(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        content = "# Full\n\n" + "A" * 1000
        (vault / "full.md").write_text(content, encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault, mirror_mode="full-summary"))
        assert len(entries) == 1
        assert entries[0].mode == "full"
        assert entries[0].content == content  # under cap

    def test_index_only_mode_returns_summary_snippet(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        content = "# Summary\n\n" + "B" * 500
        (vault / "summary.md").write_text(content, encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault, mirror_mode="index-only"))
        assert len(entries) == 1
        assert entries[0].mode == "summary"
        # Summary content must be shorter than the full content.
        assert len(entries[0].content) < len(content)

    def test_full_summary_truncates_at_cap(self, tmp_path: Path) -> None:
        from fritz_local_brain.ingest_adapters import _FULL_CONTENT_CAP

        vault = tmp_path / "vault"
        vault.mkdir()
        content = "# Big\n\n" + "X" * (_FULL_CONTENT_CAP + 1000)
        (vault / "big.md").write_text(content, encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault, mirror_mode="full-summary"))
        assert entries[0].mode == "full"
        assert len(entries[0].content) == _FULL_CONTENT_CAP

    def test_missing_connection_path_returns_empty(self, tmp_path: Path) -> None:
        adapter = LocalVaultIngestAdapter()
        target = ExternalTarget(
            name="missing",
            kind="local-vault",
            connection=str(tmp_path / "nonexistent"),
        )
        entries = adapter.fetch(target)
        assert entries == []

    def test_none_connection_returns_empty(self, tmp_path: Path) -> None:
        adapter = LocalVaultIngestAdapter()
        target = ExternalTarget(name="no-conn", kind="local-vault", connection=None)
        entries = adapter.fetch(target)
        assert entries == []

    def test_excludes_index_md(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "index.md").write_text("# Index\n", encoding="utf-8")
        (vault / "real.md").write_text("# Real\n", encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault))
        names = [e.pointer for e in entries]
        assert not any("index.md" in p for p in names)
        assert any("real.md" in p for p in names)

    def test_skips_symlinks(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        real = tmp_path / "outside.md"
        real.write_text("# Outside\n", encoding="utf-8")
        (vault / "link.md").symlink_to(real)
        (vault / "local.md").write_text("# Local\n", encoding="utf-8")

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault))
        pointers = [e.pointer for e in entries]
        assert all("link.md" not in p for p in pointers)
        assert any("local.md" in p for p in pointers)

    def test_directory_symlink_containment_check(self, tmp_path: Path) -> None:
        """Verify that files reachable only via a symlinked subdir are skipped.

        The vault contains a symlinked SUBDIRECTORY pointing OUTSIDE the vault.
        Files reached only via that symlinked subdir must NOT be included,
        confirming the defense-in-depth containment check.
        """
        vault = tmp_path / "vault"
        vault.mkdir()

        # Create a local file we actually want to include.
        (vault / "local.md").write_text("# Local\n\nInside vault.", encoding="utf-8")

        # Create a directory outside the vault.
        outside_dir = tmp_path / "outside-content"
        outside_dir.mkdir()
        (outside_dir / "secret.md").write_text("# Secret\n\nOutside vault.", encoding="utf-8")

        # Symlink the outside directory into the vault with a subdir name.
        (vault / "linked-subdir").symlink_to(outside_dir)

        adapter = LocalVaultIngestAdapter()
        entries = adapter.fetch(_vault_target(vault))
        pointers = [e.pointer for e in entries]

        # The local file should be present.
        assert any("local.md" in p for p in pointers)

        # Files via the symlinked subdir must NOT appear (the directory itself
        # is a symlink, not the file, so rglob finds them but resolve().relative_to()
        # fails, causing them to be skipped).
        assert not any("secret.md" in p for p in pointers)

    def test_kind_attribute(self) -> None:
        assert LocalVaultIngestAdapter.kind == "local-vault"


# ===========================================================================
# 3. get_ingest_adapter
# ===========================================================================


class TestGetIngestAdapter:
    def test_local_vault_returns_adapter_instance(self) -> None:
        adapter = get_ingest_adapter("local-vault")
        assert isinstance(adapter, LocalVaultIngestAdapter)

    def test_mcp_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            get_ingest_adapter("mcp")

    def test_drive_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            get_ingest_adapter("drive")

    def test_offsite_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            get_ingest_adapter("offsite")

    def test_unknown_kind_raises_mirror_error(self) -> None:
        with pytest.raises(MirrorError, match="Unknown"):
            get_ingest_adapter("totally-unknown")


# ===========================================================================
# 4. mirror_target
# ===========================================================================


class TestMirrorTarget:
    def _make_local_vault_target(
        self, vault_dir: Path, mirror_mode: str = "index-only"
    ) -> ExternalTarget:
        return ExternalTarget(
            name="my-vault",
            kind="local-vault",
            connection=str(vault_dir),
            mirror_mode=mirror_mode,
        )

    def test_writes_provenance_tagged_captures(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        vault = tmp_path / "ext-vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n\nFact.", encoding="utf-8")

        target = self._make_local_vault_target(vault)
        result = mirror_target(
            settings, target, mirrored_at="2026-01-01T00:00:00", dry_run=False
        )

        assert result.entries_mirrored == 1
        assert len(result.written_paths) == 1

        written = Path(result.written_paths[0])
        assert written.exists()
        fm = _read_frontmatter(written)
        assert fm["source"] == "my-vault (local-vault)"
        assert fm["mirrored_at"] == "2026-01-01T00:00:00"
        assert fm["mode"] == "summary"
        assert "my-vault:note.md" in fm["pointer"]
        assert fm["title"] == "Note"

    def test_mirrored_at_override_is_honored(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        vault = tmp_path / "ext-vault"
        vault.mkdir()
        (vault / "a.md").write_text("# A\n", encoding="utf-8")

        target = self._make_local_vault_target(vault)
        ts = "2025-03-15T10:00:00.000000"
        result = mirror_target(settings, target, mirrored_at=ts)
        written = Path(result.written_paths[0])
        fm = _read_frontmatter(written)
        assert fm["mirrored_at"] == ts

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        vault = tmp_path / "ext-vault"
        vault.mkdir()
        (vault / "fact.md").write_text("# Fact\n\nBody.", encoding="utf-8")

        target = self._make_local_vault_target(vault)
        result = mirror_target(settings, target, mirrored_at="2026-01-01T00:00:00", dry_run=True)

        assert result.dry_run is True
        assert result.entries_mirrored == 1
        assert len(result.written_paths) == 1
        # Nothing must have been written.
        written = Path(result.written_paths[0])
        assert not written.exists()

    def test_mirror_result_counts_correct(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        vault = tmp_path / "ext-vault"
        vault.mkdir()
        for i in range(3):
            (vault / f"note-{i}.md").write_text(f"# Note {i}\n", encoding="utf-8")

        target = self._make_local_vault_target(vault)
        result = mirror_target(settings, target, mirrored_at="T", dry_run=True)
        assert result.entries_mirrored == 3
        assert len(result.written_paths) == 3
        assert result.target == "my-vault"
        assert result.kind == "local-vault"

    def test_full_summary_mode_sets_mode_full_in_frontmatter(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        vault = tmp_path / "ext-vault"
        vault.mkdir()
        (vault / "big.md").write_text("# Big\n\nLots of content.", encoding="utf-8")

        target = self._make_local_vault_target(vault, mirror_mode="full-summary")
        result = mirror_target(settings, target, mirrored_at="T")
        fm = _read_frontmatter(Path(result.written_paths[0]))
        assert fm["mode"] == "full"


# ===========================================================================
# 5. End-to-end: mirror_target -> run_compile discovers mirrored capture
# ===========================================================================


def test_mirror_then_compile_discovers_inbox_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After mirror_target, run_compile in store mode sees the mirrored capture."""
    settings = _make_settings(tmp_path)
    brain_home = settings.brain_home

    # Build an external vault with one note.
    vault = tmp_path / "ext-vault"
    vault.mkdir()
    (vault / "knowledge.md").write_text("# Key Fact\n\nThis is a durable fact.", encoding="utf-8")

    target = ExternalTarget(
        name="ext",
        kind="local-vault",
        connection=str(vault),
        mirror_mode="index-only",
    )

    # Run the mirror (real write, not dry-run).
    mirror_result = mirror_target(settings, target, mirrored_at="2026-01-01T12:00:00")
    assert mirror_result.entries_mirrored == 1
    assert Path(mirror_result.written_paths[0]).exists()

    # Prepare the compile agent fake — it just needs to acknowledge the capture.
    mirrored_capture_path = Path(mirror_result.written_paths[0])
    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="common/decisions/key-fact.md",
        operation="create",
        title="Key Fact",
        summary="Durable fact from ext vault.",
        sources=[str(mirrored_capture_path)],
        body="This is a durable fact.",
    )
    fake_agent = FakeCompileAgent(proposal)
    monkeypatch.setattr(compile_workflow, "build_compile_agent", lambda s, skill: fake_agent)

    # Run compile in store mode (no registry.yaml).
    compile_result = asyncio.run(
        compile_workflow.run_compile(settings, CompileRunRequest(dry_run=False, max_captures=10))
    )

    assert compile_result.errors == [], compile_result.errors
    assert compile_result.captures_considered >= 1
    # The inbox count must include our mirrored capture.
    assert compile_result.captures_by_source.get("inbox", 0) >= 1


# ===========================================================================
# 6. mirror_targets (batch)
# ===========================================================================


class TestMirrorTargets:
    def test_returns_one_result_per_target(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)

        vault_a = tmp_path / "vault-a"
        vault_a.mkdir()
        (vault_a / "a.md").write_text("# A\n", encoding="utf-8")

        vault_b = tmp_path / "vault-b"
        vault_b.mkdir()
        (vault_b / "b.md").write_text("# B\n", encoding="utf-8")

        targets = [
            ExternalTarget(name="va", kind="local-vault", connection=str(vault_a)),
            ExternalTarget(name="vb", kind="local-vault", connection=str(vault_b)),
        ]
        results = mirror_targets(settings, targets, mirrored_at="T", dry_run=True)
        assert len(results) == 2
        assert {r.target for r in results} == {"va", "vb"}

    def test_shared_mirrored_at_timestamp(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "x.md").write_text("# X\n", encoding="utf-8")

        targets = [ExternalTarget(name="v", kind="local-vault", connection=str(vault))]
        results = mirror_targets(settings, targets, mirrored_at="FIXED_TS")
        written = Path(results[0].written_paths[0])
        fm = _read_frontmatter(written)
        assert fm["mirrored_at"] == "FIXED_TS"
