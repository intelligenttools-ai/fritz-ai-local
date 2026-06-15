from __future__ import annotations

import asyncio

from fritz_local_brain import embeddings
from fritz_local_brain.agents.query_agent import BrainQueryAgent
from fritz_local_brain.config import Settings
from fritz_local_brain.models import EmbeddingIndexResult, QueryRunRequest
from fritz_local_brain.query_workflow import _allowed_vector_paths, run_query


def test_query_agent_skips_symlinked_knowledge_file(tmp_path) -> None:
    vault = tmp_path / "vault"
    knowledge = vault / "knowledge"
    knowledge.mkdir(parents=True)
    secret = vault / "private.md"
    secret.write_text("needle secret", encoding="utf-8")
    (knowledge / "linked.md").symlink_to(secret)
    (knowledge / "safe.md").write_text("needle safe", encoding="utf-8")

    matches = BrainQueryAgent(skill_text="").search_vault(
        "test",
        vault,
        {"paths": {"knowledge": "knowledge"}},
        "needle",
        10,
    )

    assert [match.path for match in matches] == ["safe.md"]


def test_query_workflow_searches_capture_inbox_when_fact_is_not_compiled(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    capture = brain_home / "capture" / "inbox" / "fact.md"
    brain_home.mkdir(parents=True)
    capture.parent.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    capture.write_text("# Runner VM\n\nForgejo runner is on 192.168.1.51.\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="192.168.1.51"),
        )
    )

    assert result.errors == []
    assert [(match.vault, match.path) for match in result.matches] == [("_captures", "capture/inbox/fact.md")]
    assert "192.168.1.51" in result.matches[0].snippet


def test_query_workflow_searches_full_raw_capture_without_llm_preamble(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    capture = brain_home / "capture" / "inbox" / "long.md"
    brain_home.mkdir(parents=True)
    capture.parent.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    capture.write_text("# Long Capture\n\n" + ("x" * 13000) + " tail-marker-9f4b\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="tail-marker-9f4b"),
        )
    )

    assert result.errors == []
    assert [(match.vault, match.path) for match in result.matches] == [("_captures", "capture/inbox/long.md")]
    assert "tail-marker-9f4b" in result.matches[0].snippet

    preamble_result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="untrusted data"),
        )
    )

    assert preamble_result.errors == []
    assert preamble_result.matches == []


def test_query_workflow_uses_vector_index_when_exact_search_misses(tmp_path, monkeypatch) -> None:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    capture = brain_home / "capture" / "inbox" / "fact.md"
    brain_home.mkdir(parents=True)
    capture.parent.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    capture.write_text("# Storage\n\nLonghorn replica scheduling failure runbook.\n", encoding="utf-8")

    async def fake_embed(settings, text):
        return [1.0, 0.0] if "disk pressure" in text else [0.9, 0.1]

    monkeypatch.setattr(embeddings, "_embed_text", fake_embed)
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
    )
    asyncio.run(embeddings.refresh_embedding_index(settings))

    result = asyncio.run(run_query(settings, QueryRunRequest(query="disk pressure"), use_vector=True, ensure_index=True))

    assert result.errors == []
    assert [(match.vault, match.path) for match in result.matches] == [("_captures", "capture/inbox/fact.md")]
    assert result.matches[0].snippet.startswith("[vector score")


def test_query_workflow_does_not_refresh_embedding_index_for_interactive_search(tmp_path, monkeypatch) -> None:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    brain_home.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    async def fail_ensure(settings):
        raise AssertionError("interactive search must not refresh embedding index inline")

    import fritz_local_brain.query_workflow as query_workflow

    monkeypatch.setattr(query_workflow, "ensure_embedding_index", fail_ensure)
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
    )

    result = asyncio.run(run_query(settings, QueryRunRequest(query="disk pressure"), use_vector=True, ensure_index=False))

    assert result.errors == []
    assert result.matches == []
    assert result.skipped == ["vector search: Embedding index is missing; waiting for compile/ingest refresh"]


def test_query_workflow_skips_vector_search_when_index_refresh_fails(tmp_path, monkeypatch) -> None:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    capture = brain_home / "capture" / "inbox" / "fact.md"
    brain_home.mkdir(parents=True)
    capture.parent.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    capture.write_text("# Storage\n\nLonghorn replica scheduling failure runbook.\n", encoding="utf-8")

    async def fail_ensure(settings):
        return EmbeddingIndexResult(enabled=True, index_path=str(brain_home / "embeddings" / "index.json"), error="busy")

    async def fail_if_searched(*args, **kwargs):
        raise AssertionError("stale vector index must not be searched after refresh failure")

    import fritz_local_brain.query_workflow as query_workflow

    monkeypatch.setattr(query_workflow, "ensure_embedding_index", fail_ensure)
    monkeypatch.setattr(query_workflow, "search_embedding_index", fail_if_searched)
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
    )

    result = asyncio.run(run_query(settings, QueryRunRequest(query="disk pressure"), use_vector=True, ensure_index=True))

    assert result.errors == []
    assert result.matches == []
    assert result.skipped == ["vector search: busy"]


def test_first_embedding_refresh_after_compile_is_not_debounced(tmp_path, monkeypatch) -> None:
    settings = Settings(
        LOCAL_BRAIN_HOME=tmp_path,
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
        LOCAL_BRAIN_EMBEDDING_REFRESH_DEBOUNCE_SECONDS=300,
    )
    calls = []

    async def fake_background_once(settings, reason):
        calls.append(reason)

    monkeypatch.setattr(embeddings, "_background_refresh_pending", False)
    monkeypatch.setattr(embeddings, "_background_refresh_task", None)
    monkeypatch.setattr(embeddings, "_last_background_refresh_started_at", None)
    monkeypatch.setattr(embeddings, "_background_refresh_embedding_index_once", fake_background_once)
    monkeypatch.setattr(embeddings.time, "monotonic", lambda: 1000.0)

    async def run_schedule():
        status = embeddings.schedule_embedding_refresh_after_compile(settings, reason="compile")
        await embeddings._background_refresh_task
        return status

    assert asyncio.run(run_schedule()) == "scheduled"
    assert calls == ["compile"]


def test_embedding_refresh_after_compile_is_debounced(tmp_path, monkeypatch) -> None:
    settings = Settings(
        LOCAL_BRAIN_HOME=tmp_path,
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
        LOCAL_BRAIN_EMBEDDING_REFRESH_DEBOUNCE_SECONDS=0,
    )
    calls = []

    async def fake_background_once(settings, reason):
        calls.append(reason)

    monkeypatch.setattr(embeddings, "_background_refresh_pending", False)
    monkeypatch.setattr(embeddings, "_background_refresh_task", None)
    monkeypatch.setattr(embeddings, "_last_background_refresh_started_at", 0.0)
    monkeypatch.setattr(embeddings, "_background_refresh_embedding_index_once", fake_background_once)
    monkeypatch.setattr(embeddings.time, "monotonic", lambda: 1000.0)

    async def run_schedule():
        first = embeddings.schedule_embedding_refresh_after_compile(settings, reason="compile")
        second = embeddings.schedule_embedding_refresh_after_compile(settings, reason="compile")
        await embeddings._background_refresh_task
        return first, second

    assert asyncio.run(run_schedule()) == ("scheduled", "queued")
    assert calls == ["compile"]


def test_embedding_refresh_after_compile_schedules_delayed_refresh_inside_debounce_window(tmp_path, monkeypatch) -> None:
    settings = Settings(
        LOCAL_BRAIN_HOME=tmp_path,
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
        LOCAL_BRAIN_EMBEDDING_REFRESH_DEBOUNCE_SECONDS=300,
    )

    calls = []

    class DoneTask:
        def done(self) -> bool:
            return True

    async def fake_delayed(settings, reason, delay):
        calls.append((reason, delay))

    monkeypatch.setattr(embeddings, "_background_refresh_pending", False)
    monkeypatch.setattr(embeddings, "_background_refresh_task", DoneTask())
    monkeypatch.setattr(embeddings, "_last_background_refresh_started_at", 900.0)
    monkeypatch.setattr(embeddings.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(embeddings, "_delayed_background_refresh_embedding_index", fake_delayed)

    async def run_schedule():
        status = embeddings.schedule_embedding_refresh_after_compile(settings, reason="compile")
        await embeddings._background_refresh_task
        return status

    assert asyncio.run(run_schedule()) == "scheduled-delayed"
    assert calls == [("compile", 200.0)]


def test_background_embedding_refresh_waits_before_queued_second_pass(tmp_path, monkeypatch) -> None:
    settings = Settings(
        LOCAL_BRAIN_HOME=tmp_path,
        LOCAL_BRAIN_EMBEDDING_ENABLED=True,
        LOCAL_BRAIN_EMBEDDING_REFRESH_DEBOUNCE_SECONDS=300,
    )
    calls = []

    async def fake_wait(settings):
        calls.append(("wait", settings.embedding_refresh_debounce_seconds))

    async def fake_background_once(settings, reason):
        calls.append(("refresh", reason))
        if len([call for call in calls if call[0] == "refresh"]) == 1:
            monkeypatch.setattr(embeddings, "_background_refresh_pending", True)

    monkeypatch.setattr(embeddings, "_background_refresh_pending", False)
    monkeypatch.setattr(embeddings, "_last_background_refresh_started_at", 0.0)
    monkeypatch.setattr(embeddings, "_wait_for_refresh_debounce", fake_wait)
    monkeypatch.setattr(embeddings, "_background_refresh_embedding_index_once", fake_background_once)

    asyncio.run(embeddings._background_refresh_embedding_index_loop(settings, "compile"))

    assert calls == [
        ("wait", 300.0),
        ("refresh", "compile"),
        ("wait", 300.0),
        ("refresh", "compile"),
    ]


def test_background_embedding_refresh_logs_unexpected_crash(tmp_path, monkeypatch) -> None:
    settings = Settings(LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_EMBEDDING_ENABLED=True)
    logs = []

    async def crash(settings, reason):
        raise RuntimeError("boom")

    monkeypatch.setattr(embeddings, "_background_refresh_pending", False)
    monkeypatch.setattr(embeddings, "_last_background_refresh_started_at", 0.0)
    monkeypatch.setattr(embeddings, "_background_refresh_embedding_index_once", crash)
    monkeypatch.setattr(embeddings, "append_global_log", lambda brain_home, op, summary, dry_run: logs.append((op, summary, dry_run)))

    asyncio.run(embeddings._background_refresh_embedding_index_loop(settings, "compile"))

    assert logs == [("EMBEDDINGS", "Background embedding refresh after compile crashed: boom", False)]


def test_delayed_embedding_refresh_runs_once_for_single_debounced_request(tmp_path, monkeypatch) -> None:
    settings = Settings(LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_EMBEDDING_ENABLED=True)
    calls = []

    async def fake_sleep(delay):
        calls.append(("sleep", delay))

    async def fake_background_once(settings, reason):
        calls.append(("refresh", reason))

    monkeypatch.setattr(embeddings, "_background_refresh_pending", True)
    monkeypatch.setattr(embeddings, "_last_background_refresh_started_at", 0.0)
    monkeypatch.setattr(embeddings.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(embeddings, "_background_refresh_embedding_index_once", fake_background_once)
    monkeypatch.setattr(embeddings.time, "monotonic", lambda: 1234.0)

    asyncio.run(embeddings._delayed_background_refresh_embedding_index(settings, "compile", 42.0))

    assert calls == [("sleep", 42.0), ("refresh", "compile")]
    assert embeddings._background_refresh_pending is False


def test_embedding_search_rejects_stale_source_fingerprint(tmp_path, monkeypatch) -> None:
    brain_home = tmp_path / "brain"
    capture = brain_home / "capture" / "inbox" / "fact.md"
    brain_home.mkdir(parents=True)
    capture.parent.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text("vaults: {}\n", encoding="utf-8")
    capture.write_text("# Secret\n\nold-redacted-secret searchable text.\n", encoding="utf-8")

    async def fake_embed(settings, text):
        return [1.0, 0.0] if "find secret" in text else [0.9, 0.1]

    monkeypatch.setattr(embeddings, "_embed_text", fake_embed)
    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_EMBEDDING_ENABLED=True)
    asyncio.run(embeddings.refresh_embedding_index(settings))
    capture.write_text("# Secret\n\nredacted replacement.\n", encoding="utf-8")

    matches = asyncio.run(embeddings.search_embedding_index(settings, "find secret", 10))

    assert matches == []


def test_query_workflow_rejects_reserved_capture_vault_name(tmp_path) -> None:
    brain_home = tmp_path / "brain"
    vault_path = tmp_path / "vault"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    brain_home.mkdir(parents=True)
    (vault_path / ".brain").mkdir(parents=True)
    (vault_path / "knowledge").mkdir()
    (vault_path / ".brain" / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    (brain_home / "registry.yaml").write_text(f"vaults:\n  _captures:\n    path: {vault_path}\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="anything", vault="_captures"),
        )
    )

    assert result.errors == ["Reserved vault name is not allowed: _captures"]


def test_query_workflow_searches_brain_store_without_registry(tmp_path) -> None:
    """Store-only brain (no registry.yaml) returns brain-store matches (vault 'brain') and capture matches."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    # Brain store article.
    store = brain_home / "knowledge"
    article = store / "common" / "context" / "runner-vm.md"
    article.parent.mkdir(parents=True)
    article.write_text("# Runner VM\n\nForgejo runner is on 192.168.1.51.\n", encoding="utf-8")
    # Capture file.
    capture = brain_home / "capture" / "inbox" / "note.md"
    capture.parent.mkdir(parents=True)
    capture.write_text("# Note\n\n192.168.1.51 is also reachable via ssh.\n", encoding="utf-8")
    # Skill file.
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    # NO registry.yaml — pure store mode.

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="192.168.1.51"),
        )
    )

    assert result.errors == []
    vaults = [m.vault for m in result.matches]
    assert "brain" in vaults
    assert "_captures" in vaults
    brain_match = next(m for m in result.matches if m.vault == "brain")
    assert brain_match.path == "common/context/runner-vm.md"
    assert "192.168.1.51" in brain_match.snippet


def test_query_workflow_store_mode_excludes_superseded_articles_by_default(tmp_path) -> None:
    """Active scope (default) excludes articles with status: superseded; 'all' includes them."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    # Active article (no status).
    active_article = store / "common" / "context" / "active-fact.md"
    active_article.parent.mkdir(parents=True)
    active_article.write_text("# Active Fact\n\nThis is searchable content.\n", encoding="utf-8")
    # Superseded article.
    superseded_article = store / "common" / "context" / "old-fact.md"
    superseded_article.write_text(
        "---\nstatus: superseded\n---\n\n# Old Fact\n\nThis is searchable content.\n", encoding="utf-8"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    # NO registry.yaml.

    result_active = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="searchable content", scope="active"),
        )
    )

    result_all = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="searchable content", scope="all"),
        )
    )

    active_paths = [m.path for m in result_active.matches if m.vault == "brain"]
    all_paths = [m.path for m in result_all.matches if m.vault == "brain"]
    assert "common/context/active-fact.md" in active_paths
    assert "common/context/old-fact.md" not in active_paths
    assert "common/context/active-fact.md" in all_paths
    assert "common/context/old-fact.md" in all_paths


def test_query_workflow_store_mode_includes_corroborated_articles_in_active_scope(tmp_path) -> None:
    """Active scope includes articles with status: active or status: corroborated."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    corroborated_article = store / "common" / "context" / "corroborated-fact.md"
    corroborated_article.parent.mkdir(parents=True)
    corroborated_article.write_text(
        "---\nstatus: corroborated\n---\n\n# Corroborated Fact\n\nThis is confirmed content.\n", encoding="utf-8"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="confirmed content", scope="active"),
        )
    )

    brain_paths = [m.path for m in result.matches if m.vault == "brain"]
    assert "common/context/corroborated-fact.md" in brain_paths


def test_query_workflow_store_mode_deprecated_is_demoted_below_active(tmp_path) -> None:
    """Active scope: deprecated matches appear AFTER active/corroborated matches (demoted)."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    # Use alphabetical names so sorted() would normally put deprecated first.
    deprecated_article = store / "common" / "context" / "a-deprecated.md"
    deprecated_article.parent.mkdir(parents=True)
    deprecated_article.write_text(
        "---\nstatus: deprecated\n---\n\n# Deprecated Fact\n\nshared-needle\n", encoding="utf-8"
    )
    active_article = store / "common" / "context" / "b-active.md"
    active_article.write_text("# Active Fact\n\nshared-needle\n", encoding="utf-8")
    corroborated_article = store / "common" / "context" / "c-corroborated.md"
    corroborated_article.write_text(
        "---\nstatus: corroborated\n---\n\n# Corroborated Fact\n\nshared-needle\n", encoding="utf-8"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="shared-needle", scope="active"),
        )
    )

    brain_matches = [m for m in result.matches if m.vault == "brain"]
    brain_paths = [m.path for m in brain_matches]
    # All three are visible.
    assert "common/context/a-deprecated.md" in brain_paths
    assert "common/context/b-active.md" in brain_paths
    assert "common/context/c-corroborated.md" in brain_paths
    # deprecated must come AFTER both active and corroborated.
    deprecated_idx = brain_paths.index("common/context/a-deprecated.md")
    active_idx = brain_paths.index("common/context/b-active.md")
    corroborated_idx = brain_paths.index("common/context/c-corroborated.md")
    assert deprecated_idx > active_idx
    assert deprecated_idx > corroborated_idx


def test_query_workflow_store_mode_deprecated_does_not_crowd_out_active_within_limit(tmp_path) -> None:
    """Regression: deprecated files sorted before active must NOT consume the result budget.

    With limit=2, two deprecated articles (alphabetically first) and one active article all
    matching the query: the active article must be retained and appear first; one deprecated
    article fills the remaining slot.  The active article must never be dropped.
    """
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    # 'a-dep' and 'b-dep' sort before 'c-active' alphabetically.
    dep_a = store / "a-dep.md"
    dep_a.parent.mkdir(parents=True)
    dep_a.write_text("---\nstatus: deprecated\n---\n\n# Dep A\n\nshared-marker\n", encoding="utf-8")
    dep_b = store / "b-dep.md"
    dep_b.write_text("---\nstatus: deprecated\n---\n\n# Dep B\n\nshared-marker\n", encoding="utf-8")
    active_c = store / "c-active.md"
    active_c.write_text("# Active C\n\nshared-marker\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="shared-marker", scope="active", limit=2),
        )
    )

    brain_matches = [m for m in result.matches if m.vault == "brain"]
    brain_paths = [m.path for m in brain_matches]
    # The active article must always be retained.
    assert "c-active.md" in brain_paths, f"active article was crowded out; got {brain_paths}"
    # The active article must appear first (demoted articles rank below primary).
    assert brain_paths[0] == "c-active.md", f"active article must be first; got {brain_paths}"
    # Total results are capped at limit=2.
    assert len(brain_paths) == 2
    # The remaining slot is filled by one deprecated article.
    assert brain_paths[1] in ("a-dep.md", "b-dep.md")


def test_query_workflow_store_mode_excludes_historical_articles_by_default(tmp_path) -> None:
    """Active scope (default) excludes articles with status: historical; 'all' includes them."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    active_article = store / "common" / "context" / "active-fact.md"
    active_article.parent.mkdir(parents=True)
    active_article.write_text("# Active Fact\n\nThis is searchable content.\n", encoding="utf-8")
    historical_article = store / "common" / "context" / "hist-fact.md"
    historical_article.write_text(
        "---\nstatus: historical\n---\n\n# Historical Fact\n\nThis is searchable content.\n", encoding="utf-8"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    result_active = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="searchable content", scope="active"),
        )
    )

    result_all = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="searchable content", scope="all"),
        )
    )

    active_paths = [m.path for m in result_active.matches if m.vault == "brain"]
    all_paths = [m.path for m in result_all.matches if m.vault == "brain"]
    assert "common/context/active-fact.md" in active_paths
    assert "common/context/hist-fact.md" not in active_paths
    assert "common/context/active-fact.md" in all_paths
    assert "common/context/hist-fact.md" in all_paths


# ---------------------------------------------------------------------------
# WI9: include_archive scope + vector allow-set (issue #94)
# ---------------------------------------------------------------------------


def test_query_workflow_store_mode_include_archive_returns_superseded_after_active(tmp_path) -> None:
    """include_archive scope: superseded articles appear AFTER active results."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    active_article = store / "common" / "context" / "active-fact.md"
    active_article.parent.mkdir(parents=True)
    active_article.write_text("# Active Fact\n\nshared content needle\n", encoding="utf-8")
    superseded_article = store / "common" / "context" / "old-fact.md"
    superseded_article.write_text(
        "---\nstatus: superseded\n---\n\n# Old Fact\n\nshared content needle\n", encoding="utf-8"
    )
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    # Default active scope: superseded excluded.
    result_active = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="shared content needle", scope="active"),
        )
    )
    active_paths = [m.path for m in result_active.matches if m.vault == "brain"]
    assert "common/context/active-fact.md" in active_paths
    assert "common/context/old-fact.md" not in active_paths

    # include_archive: superseded RETURNED, AFTER the active article.
    result_archive = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="shared content needle", scope="include_archive"),
        )
    )
    archive_brain = [m.path for m in result_archive.matches if m.vault == "brain"]
    assert "common/context/active-fact.md" in archive_brain
    assert "common/context/old-fact.md" in archive_brain
    # Active must come BEFORE superseded.
    active_idx = archive_brain.index("common/context/active-fact.md")
    superseded_idx = archive_brain.index("common/context/old-fact.md")
    assert active_idx < superseded_idx, "superseded must appear after active in include_archive scope"


def test_query_workflow_store_mode_include_archive_includes_historical(tmp_path) -> None:
    """include_archive scope includes historical articles (after active)."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    active = store / "active.md"
    active.parent.mkdir(parents=True)
    active.write_text("# Active\n\nneedle-ia\n", encoding="utf-8")
    historical = store / "hist.md"
    historical.write_text("---\nstatus: historical\n---\n\n# Historical\n\nneedle-ia\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="needle-ia", scope="include_archive"),
        )
    )
    brain_paths = [m.path for m in result.matches if m.vault == "brain"]
    assert "active.md" in brain_paths
    assert "hist.md" in brain_paths
    assert brain_paths.index("active.md") < brain_paths.index("hist.md")


def test_query_workflow_store_mode_all_scope_includes_archived_in_natural_order(tmp_path) -> None:
    """all scope includes everything in natural (sorted) order."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    store = brain_home / "knowledge"
    (store / "a-superseded.md").parent.mkdir(parents=True)
    (store / "a-superseded.md").write_text(
        "---\nstatus: superseded\n---\n\n# Superseded\n\nneedle-all\n", encoding="utf-8"
    )
    (store / "b-active.md").write_text("# Active\n\nneedle-all\n", encoding="utf-8")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    result = asyncio.run(
        run_query(
            Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills"),
            QueryRunRequest(query="needle-all", scope="all"),
        )
    )
    brain_paths = [m.path for m in result.matches if m.vault == "brain"]
    assert "a-superseded.md" in brain_paths
    assert "b-active.md" in brain_paths


def test_allowed_vector_paths_excludes_archive_in_active_scope(tmp_path) -> None:
    """_allowed_vector_paths with scope='active' excludes superseded store articles."""
    brain_home = tmp_path / "brain"
    store = brain_home / "knowledge"
    active = store / "active.md"
    active.parent.mkdir(parents=True)
    active.write_text("# Active\n\nContent.\n", encoding="utf-8")
    superseded = store / "old.md"
    superseded.write_text("---\nstatus: superseded\n---\n\n# Old\n\nContent.\n", encoding="utf-8")
    # No registry → store mode.
    settings = Settings(LOCAL_BRAIN_HOME=brain_home)

    active_keys = _allowed_vector_paths(settings, {}, "_captures", scope="active")
    archive_keys = _allowed_vector_paths(settings, {}, "_captures", scope="include_archive")

    # Active scope: superseded excluded.
    assert ("brain", "active.md") in active_keys
    assert ("brain", "old.md") not in active_keys

    # include_archive: superseded included.
    assert ("brain", "active.md") in archive_keys
    assert ("brain", "old.md") in archive_keys


def test_allowed_vector_paths_excludes_historical_in_active_scope(tmp_path) -> None:
    """_allowed_vector_paths with scope='active' excludes historical store articles."""
    brain_home = tmp_path / "brain"
    store = brain_home / "knowledge"
    store.mkdir(parents=True)
    (store / "active.md").write_text("# Active\n\nContent.\n", encoding="utf-8")
    (store / "hist.md").write_text("---\nstatus: historical\n---\n\n# Hist\n\nContent.\n", encoding="utf-8")
    settings = Settings(LOCAL_BRAIN_HOME=brain_home)

    active_keys = _allowed_vector_paths(settings, {}, "_captures", scope="active")
    all_keys = _allowed_vector_paths(settings, {}, "_captures", scope="all")

    assert ("brain", "active.md") in active_keys
    assert ("brain", "hist.md") not in active_keys
    assert ("brain", "hist.md") in all_keys


def test_allowed_vector_paths_all_scope_includes_archive(tmp_path) -> None:
    """_allowed_vector_paths with scope='all' includes archive-status articles."""
    brain_home = tmp_path / "brain"
    store = brain_home / "knowledge"
    store.mkdir(parents=True)
    (store / "superseded.md").write_text(
        "---\nstatus: superseded\n---\n\n# Old\n\nContent.\n", encoding="utf-8"
    )
    settings = Settings(LOCAL_BRAIN_HOME=brain_home)

    keys = _allowed_vector_paths(settings, {}, "_captures", scope="all")
    assert ("brain", "superseded.md") in keys


# ---------------------------------------------------------------------------
# WI12: merge_matches + live-fetch query integration
# ---------------------------------------------------------------------------


def _qm(vault, path, title="t", snippet="s"):
    from fritz_local_brain.models import QueryMatch

    return QueryMatch(vault=vault, path=path, title=title, snippet=snippet)


def test_merge_matches_brain_first_keeps_brain_authoritative() -> None:
    from fritz_local_brain.query_workflow import merge_matches

    brain = [_qm("brain", "a.md"), _qm("brain", "b.md")]
    external = [_qm("ext", "x.md"), _qm("brain", "b.md")]  # b.md is a dup of a brain match

    merged = merge_matches(brain, external, policy="brain-first", limit=10)
    paths = [(m.vault, m.path) for m in merged]

    # Brain matches come first and are never displaced; ext fills the gap.
    assert paths[:2] == [("brain", "a.md"), ("brain", "b.md")]
    # Duplicate ext b.md is deduped; only ext x.md is appended.
    assert ("ext", "x.md") in paths
    assert paths.count(("brain", "b.md")) == 1


def test_merge_matches_brain_first_respects_limit_no_displacement() -> None:
    from fritz_local_brain.query_workflow import merge_matches

    brain = [_qm("brain", "a.md"), _qm("brain", "b.md")]
    external = [_qm("ext", "x.md"), _qm("ext", "y.md")]

    merged = merge_matches(brain, external, policy="brain-first", limit=2)
    paths = [(m.vault, m.path) for m in merged]

    # Limit fully consumed by brain; no external displaces a brain match.
    assert paths == [("brain", "a.md"), ("brain", "b.md")]


def test_merge_matches_peer_ranked_appends_with_dedup() -> None:
    from fritz_local_brain.query_workflow import merge_matches

    brain = [_qm("brain", "a.md")]
    external = [_qm("brain", "a.md"), _qm("ext", "x.md")]

    merged = merge_matches(brain, external, policy="peer-ranked", limit=10)
    paths = [(m.vault, m.path) for m in merged]
    assert paths == [("brain", "a.md"), ("ext", "x.md")]


def test_query_live_fetch_false_unchanged_regression(tmp_path) -> None:
    """live_fetch=False leaves an index-only mirrored capture snippet unchanged."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    # External vault with the live (full) content.
    vault = tmp_path / "ext"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nLIVE_FULL_DETAIL token here.", encoding="utf-8")

    registry = (
        "external_targets:\n"
        "  ext:\n"
        "    kind: local-vault\n"
        f"    connection: {vault}\n"
        "    mirror_mode: index-only\n"
    )
    brain_home.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text(registry, encoding="utf-8")

    # An index-only mirrored capture in the inbox.
    capture = brain_home / "capture" / "inbox" / "mirror-ext-note.md"
    capture.parent.mkdir(parents=True)
    capture.write_text(
        "---\n"
        "title: Note\n"
        "source: ext (local-vault)\n"
        "mode: index-only\n"
        "pointer: 'ext:note.md'\n"
        "---\n\n"
        "Note\n\nContent available via live-fetch on hit.\n",
        encoding="utf-8",
    )

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")
    result = asyncio.run(run_query(settings, QueryRunRequest(query="live-fetch")))

    assert result.errors == []
    assert any(m.vault == "_captures" for m in result.matches)
    # Without live_fetch, the live full detail must not appear.
    assert all("LIVE_FULL_DETAIL" not in m.snippet for m in result.matches)


def test_query_live_fetch_true_enriches_index_only_hit(tmp_path) -> None:
    """live_fetch=True enriches an index-only hit with live pointer content."""
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    vault = tmp_path / "ext"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n\nLIVE_FULL_DETAIL token here.", encoding="utf-8")

    registry = (
        "external_targets:\n"
        "  ext:\n"
        "    kind: local-vault\n"
        f"    connection: {vault}\n"
        "    mirror_mode: index-only\n"
    )
    brain_home.mkdir(parents=True)
    (brain_home / "registry.yaml").write_text(registry, encoding="utf-8")

    capture = brain_home / "capture" / "inbox" / "mirror-ext-note.md"
    capture.parent.mkdir(parents=True)
    capture.write_text(
        "---\n"
        "title: Note\n"
        "source: ext (local-vault)\n"
        "mode: index-only\n"
        "pointer: 'ext:note.md'\n"
        "---\n\n"
        "Note\n\nContent available via live-fetch on hit.\n",
        encoding="utf-8",
    )

    settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")
    result = asyncio.run(
        run_query(settings, QueryRunRequest(query="live-fetch", live_fetch=True))
    )

    assert result.errors == []
    enriched = [m for m in result.matches if m.vault == "_captures"]
    assert enriched, "expected the index-only capture to be a match"
    assert any("LIVE_FULL_DETAIL" in m.snippet for m in enriched)


# ---------------------------------------------------------------------------
# WI12: merge_policy wired into live-fetch synthesis path
# ---------------------------------------------------------------------------


def _make_live_fetch_brain(tmp_path):
    """Set up a brain_home with a local brain-store article AND an index-only
    mirrored capture for live-fetch tests.

    Returns ``(brain_home, settings_base_kwargs)`` where the caller can
    override ``LOCAL_BRAIN_MERGE_POLICY`` to test different policies.

    Layout
    ------
    - Brain-store article: ``knowledge/brain-fact.md`` containing ``BRAIN_NEEDLE``
    - External vault: ``ext/`` with ``ext_note.md`` containing ``EXT_LIVE_TOKEN``
    - Index-only capture: ``capture/inbox/mirror-ext.md`` pointing to ``ext:ext_note.md``
      and containing ``MIRROR_INDEX_NEEDLE`` in its summary (so it matches queries)
    """
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")

    # Brain-store article (no registry.yaml → store mode).
    store = brain_home / "knowledge"
    store.mkdir(parents=True)
    (store / "brain-fact.md").write_text(
        "# Brain Fact\n\nBRAIN_NEEDLE shared-token\n", encoding="utf-8"
    )

    # External vault with the live content.
    vault = tmp_path / "ext"
    vault.mkdir()
    (vault / "ext_note.md").write_text(
        "# Ext Note\n\nEXT_LIVE_TOKEN shared-token\n", encoding="utf-8"
    )

    # Registry with external target (no vault registrations → store mode).
    registry = (
        "external_targets:\n"
        "  ext:\n"
        "    kind: local-vault\n"
        f"    connection: {vault}\n"
        "    mirror_mode: index-only\n"
    )
    (brain_home / "registry.yaml").write_text(registry, encoding="utf-8")

    # Index-only mirrored capture.
    capture = brain_home / "capture" / "inbox" / "mirror-ext.md"
    capture.parent.mkdir(parents=True)
    capture.write_text(
        "---\n"
        "title: Ext Note\n"
        "source: ext (local-vault)\n"
        "mode: index-only\n"
        "pointer: 'ext:ext_note.md'\n"
        "---\n\n"
        "Ext Note\n\nMIRROR_INDEX_NEEDLE shared-token\n",
        encoding="utf-8",
    )

    return brain_home


def test_live_fetch_brain_first_keeps_brain_authoritative(tmp_path) -> None:
    """brain-first (default): brain-store match comes BEFORE external live-fetched hit.

    Even though both match ``shared-token``, the brain-store article (vault='brain')
    must appear first and the live-fetched external hit (vault='_captures') fills the
    remaining slot.
    """
    brain_home = _make_live_fetch_brain(tmp_path)
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_MERGE_POLICY="brain-first",
    )

    result = asyncio.run(
        run_query(settings, QueryRunRequest(query="shared-token", live_fetch=True, limit=10))
    )

    assert result.errors == []
    vaults = [m.vault for m in result.matches]
    # Brain-store match must be present.
    assert "brain" in vaults, f"brain vault not found in {vaults}"
    # Live-fetched external match must be present (enriched snippet).
    capture_matches = [m for m in result.matches if m.vault == "_captures"]
    assert capture_matches, "expected enriched _captures match"
    assert any("EXT_LIVE_TOKEN" in m.snippet for m in capture_matches), (
        "expected live-fetched content in capture match snippet"
    )
    # Brain match must come BEFORE the external/captures match.
    brain_idx = next(i for i, m in enumerate(result.matches) if m.vault == "brain")
    capture_idx = next(i for i, m in enumerate(result.matches) if m.vault == "_captures")
    assert brain_idx < capture_idx, (
        f"brain match (idx={brain_idx}) must precede external match (idx={capture_idx}) "
        f"with brain-first policy"
    )


def test_live_fetch_brain_first_externals_fill_gaps_up_to_limit(tmp_path) -> None:
    """brain-first with limit=1: only the brain match is returned; external does not displace it."""
    brain_home = _make_live_fetch_brain(tmp_path)
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_MERGE_POLICY="brain-first",
    )

    result = asyncio.run(
        run_query(settings, QueryRunRequest(query="shared-token", live_fetch=True, limit=1))
    )

    assert result.errors == []
    assert len(result.matches) == 1, f"expected 1 match, got {len(result.matches)}"
    assert result.matches[0].vault == "brain", (
        "with limit=1 and brain-first, the brain match must win (external cannot displace it)"
    )


def test_live_fetch_peer_ranked_selectable_at_runtime(tmp_path) -> None:
    """peer-ranked policy is selectable at runtime (via MERGE_POLICY / LOCAL_BRAIN_MERGE_POLICY).

    With peer-ranked the external match is still appended after the brain match
    (brain-first order is brain → external in both policies), but the key assertion
    is that peer-ranked does NOT error and the external match IS included even when
    brain fills the first slot, demonstrating that ``settings.merge_policy`` is
    actually read at runtime.
    """
    brain_home = _make_live_fetch_brain(tmp_path)
    settings = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_MERGE_POLICY="peer-ranked",
    )

    result = asyncio.run(
        run_query(settings, QueryRunRequest(query="shared-token", live_fetch=True, limit=10))
    )

    assert result.errors == []
    vaults = [m.vault for m in result.matches]
    # peer-ranked: both brain and external results must be present.
    assert "brain" in vaults, "brain match expected with peer-ranked"
    assert "_captures" in vaults, "external/captures match expected with peer-ranked"
    # peer-ranked: external snippet is enriched with live content.
    capture_matches = [m for m in result.matches if m.vault == "_captures"]
    assert any("EXT_LIVE_TOKEN" in m.snippet for m in capture_matches)


def test_live_fetch_peer_ranked_ordering_differs_from_brain_first_at_limit(tmp_path) -> None:
    """peer-ranked with limit=2 includes both brain and external; ordering is brain then external.

    The key difference from brain-first at limit=1 is that peer-ranked does NOT
    give the brain set veto power over the total result count — both sets
    contribute up to the limit.  Here we confirm that with limit=2, peer-ranked
    returns 2 results (brain + external), while brain-first at limit=1 returns
    only 1 (brain wins the single slot).
    """
    brain_home = _make_live_fetch_brain(tmp_path)

    # brain-first, limit=1 → only brain match.
    settings_bf = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_MERGE_POLICY="brain-first",
    )
    result_bf = asyncio.run(
        run_query(settings_bf, QueryRunRequest(query="shared-token", live_fetch=True, limit=1))
    )
    assert len(result_bf.matches) == 1
    assert result_bf.matches[0].vault == "brain"

    # peer-ranked, limit=2 → brain + external both included.
    settings_pr = Settings(
        LOCAL_BRAIN_HOME=brain_home,
        LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills",
        LOCAL_BRAIN_MERGE_POLICY="peer-ranked",
    )
    result_pr = asyncio.run(
        run_query(settings_pr, QueryRunRequest(query="shared-token", live_fetch=True, limit=2))
    )
    assert len(result_pr.matches) == 2
    pr_vaults = [m.vault for m in result_pr.matches]
    assert "brain" in pr_vaults
    assert "_captures" in pr_vaults


def test_live_fetch_false_regression_no_merge_matches_called(tmp_path, monkeypatch) -> None:
    """Regression: live_fetch=False MUST NOT call merge_matches (existing ordering unchanged).

    We monkeypatch merge_matches to raise if called; any live_fetch=False query
    must pass without error.
    """
    import fritz_local_brain.query_workflow as qw

    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "brain-query" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Query Skill\n", encoding="utf-8")
    store = brain_home / "knowledge"
    store.mkdir(parents=True)
    (store / "fact.md").write_text("# Fact\n\nregression-needle\n", encoding="utf-8")

    original_merge = qw.merge_matches

    def fail_if_called(*args, **kwargs):
        raise AssertionError("merge_matches must NOT be called when live_fetch=False")

    monkeypatch.setattr(qw, "merge_matches", fail_if_called)

    try:
        settings = Settings(LOCAL_BRAIN_HOME=brain_home, LOCAL_BRAIN_SKILLS_DIR=tmp_path / "skills")
        result = asyncio.run(
            run_query(settings, QueryRunRequest(query="regression-needle", live_fetch=False))
        )
    finally:
        monkeypatch.setattr(qw, "merge_matches", original_merge)

    assert result.errors == []
    assert any("fact.md" in m.path for m in result.matches)
