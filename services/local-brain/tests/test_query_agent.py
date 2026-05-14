from __future__ import annotations

from fritz_local_brain.agents.query_agent import BrainQueryAgent


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
