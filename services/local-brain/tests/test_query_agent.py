from __future__ import annotations

import asyncio

from fritz_local_brain import embeddings
from fritz_local_brain.agents.query_agent import BrainQueryAgent
from fritz_local_brain.config import Settings
from fritz_local_brain.models import EmbeddingIndexResult, QueryRunRequest
from fritz_local_brain.query_workflow import run_query


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
    deprecated_article = store / "common" / "context" / "old-deprecated.md"
    deprecated_article.write_text(
        "---\nstatus: deprecated\n---\n\n# Deprecated Fact\n\nThis is confirmed content.\n", encoding="utf-8"
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
    assert "common/context/old-deprecated.md" not in brain_paths
