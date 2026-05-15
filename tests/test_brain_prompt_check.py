import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_prompt_check  # noqa: E402
import brain_common  # noqa: E402


def _run_prompt_hook(monkeypatch, capsys, tmp_path, prompt: str) -> str:
    capture_dir = tmp_path / "capture" / "daily"
    capture_dir.mkdir(parents=True)
    (capture_dir / "today.md").write_text("capture")

    hook_input = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(ROOT),
        "user_prompt": prompt,
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
    monkeypatch.setattr(brain_prompt_check, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_prompt_check, "load_registry", lambda: {"vaults": {"test": {"path": str(tmp_path)}}})
    monkeypatch.setattr(brain_prompt_check, "resolve_project_vault", lambda cwd: (None, None, None, None))
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_available", lambda: True)
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_instructions", lambda: "SERVICE QUERY INSTRUCTIONS")

    with pytest.raises(SystemExit):
        brain_prompt_check.main()

    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


def test_generic_setup_prompt_does_not_inject_service_query(monkeypatch, capsys, tmp_path):
    context = _run_prompt_hook(monkeypatch, capsys, tmp_path, "Set up Hermes agents and tools on Mac Mini")

    assert "SERVICE QUERY INSTRUCTIONS" not in context
    assert "service-backed query" not in context


def test_generic_query_prompt_does_not_inject_service_query(monkeypatch, capsys, tmp_path):
    context = _run_prompt_hook(monkeypatch, capsys, tmp_path, "What is the Hermes agent setup?")

    assert "SERVICE QUERY INSTRUCTIONS" not in context
    assert "service-backed query" not in context


def test_brain_setup_prompt_still_injects_service_query(monkeypatch, capsys, tmp_path):
    context = _run_prompt_hook(monkeypatch, capsys, tmp_path, "Set up Local Brain query support")

    assert "SERVICE QUERY INSTRUCTIONS" in context


def test_knowledge_search_skips_symlinked_markdown(tmp_path):
    vault = tmp_path / "vault"
    knowledge = vault / "knowledge"
    knowledge.mkdir(parents=True)
    safe = knowledge / "safe.md"
    safe.write_text("# Secret Pattern\n", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret Outside\n", encoding="utf-8")
    linked = knowledge / "linked.md"
    linked.symlink_to(secret)

    context = brain_prompt_check.search_knowledge_files(
        vault,
        {"paths": {"knowledge": "knowledge"}},
        ["secret"],
        None,
        4000,
    )

    assert str(safe) in context
    assert str(linked) not in context


def test_knowledge_search_skips_symlinked_feedback(tmp_path):
    vault = tmp_path / "vault"
    knowledge = vault / "knowledge"
    feedback = vault / "projects" / "demo" / "feedback"
    knowledge.mkdir(parents=True)
    feedback.mkdir(parents=True)
    (knowledge / "safe.md").write_text("# Agent Pattern\n", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret Outside\n", encoding="utf-8")
    linked = feedback / "linked.md"
    linked.symlink_to(secret)

    context = brain_prompt_check.search_knowledge_files(
        vault,
        {"paths": {"knowledge": "knowledge"}, "projects": {"demo": "projects/demo"}},
        ["agent"],
        "demo",
        4000,
    )

    assert str(linked) not in context


def test_service_instructions_use_http_not_host_cli(monkeypatch):
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "MCP" in instructions
    assert "curl -fsS" in instructions
    assert "fritz-local-brain-cli" not in instructions


def test_service_token_can_come_from_registry_when_env_missing(monkeypatch):
    monkeypatch.delenv("LOCAL_BRAIN_API_TOKEN", raising=False)
    monkeypatch.setattr(
        brain_common,
        "get_local_brain_service_config",
        lambda: {"api_token_env": "LOCAL_BRAIN_API_TOKEN", "api_token": "registry-token"},
    )

    assert brain_common.get_local_brain_api_token() == "registry-token"


def test_service_registry_token_overrides_stale_env(monkeypatch):
    monkeypatch.setenv("LOCAL_BRAIN_API_TOKEN", "stale-env-token")
    monkeypatch.setattr(
        brain_common,
        "get_local_brain_service_config",
        lambda: {"api_token_env": "LOCAL_BRAIN_API_TOKEN", "api_token": "registry-token"},
    )

    assert brain_common.get_local_brain_api_token() == "registry-token"


def test_service_instructions_use_registry_token_command_without_leaking_token(monkeypatch):
    monkeypatch.delenv("LOCAL_BRAIN_API_TOKEN", raising=False)
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(
        brain_common,
        "get_local_brain_service_config",
        lambda: {"api_token_env": "LOCAL_BRAIN_API_TOKEN", "api_token": "registry-token"},
    )

    instructions = brain_common.local_brain_service_instructions()

    assert "authorization: Bearer $(python3 -c" in instructions
    assert "registry-token" not in instructions


def test_rejects_shell_metacharacters_in_service_url(monkeypatch):
    monkeypatch.setattr(
        brain_common,
        "get_local_brain_service_config",
        lambda: {"base_url": "http://localhost:8765;touch", "allow_remote": False},
    )

    assert brain_common._validated_local_brain_base_url() is None
