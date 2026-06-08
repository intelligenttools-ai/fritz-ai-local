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
    skill_path = tmp_path / "skills" / "fritz:brain-query" / "SKILL.md"
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
    skill_path = tmp_path / "skills" / "fritz:brain-query" / "SKILL.md"
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
    skill_path = tmp_path / "skills" / "fritz:brain-query" / "SKILL.md"
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


def test_query_workflow_skips_vector_search_when_index_refresh_fails(tmp_path, monkeypatch) -> None:
    brain_home = tmp_path / "brain"
    skill_path = tmp_path / "skills" / "fritz:brain-query" / "SKILL.md"
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
    skill_path = tmp_path / "skills" / "fritz:brain-query" / "SKILL.md"
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
