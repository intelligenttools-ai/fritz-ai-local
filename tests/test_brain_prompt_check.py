import io
import json
import socket
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_prompt_check  # noqa: E402
import brain_common  # noqa: E402
import brain_capture  # noqa: E402
from adapters.base import CaptureEntry  # noqa: E402


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


def test_light_context_from_registry_injects_matching_article_path(monkeypatch, capsys, tmp_path):
    vault = tmp_path / "vault"
    knowledge = vault / "knowledge"
    manifest_dir = vault / ".brain"
    knowledge.mkdir(parents=True)
    manifest_dir.mkdir()
    article = knowledge / "frobnicator-routing.md"
    article.write_text("# Frobnicator Routing\n\nUse the blue route.", encoding="utf-8")
    (manifest_dir / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\n", encoding="utf-8")

    monkeypatch.setattr(
        brain_common,
        "load_registry",
        lambda: {"settings": {"context_injection": "light", "max_injection_chars": 4000}},
    )
    monkeypatch.setattr(brain_prompt_check, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_prompt_check, "resolve_project_vault", lambda cwd: ("test", {"path": str(vault)}, vault, None))
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_configured", lambda: True)
    monkeypatch.setattr(brain_prompt_check, "local_brain_setup_suggestions_enabled", lambda: False)

    hook_input = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(vault),
        "user_prompt": "What is the frobnicator routing decision?",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit):
        brain_prompt_check.main()

    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert str(article) in context


def test_claude_code_prompt_field_light_context_injects_matching_article_path(monkeypatch, capsys, tmp_path):
    vault = tmp_path / "vault"
    knowledge = vault / "knowledge"
    manifest_dir = vault / ".brain"
    knowledge.mkdir(parents=True)
    manifest_dir.mkdir()
    article = knowledge / "frobnicator-routing.md"
    article.write_text("# Frobnicator Routing\n\nUse the blue route.", encoding="utf-8")
    (manifest_dir / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\n", encoding="utf-8")

    monkeypatch.setattr(
        brain_common,
        "load_registry",
        lambda: {"settings": {"context_injection": "light", "max_injection_chars": 4000}},
    )
    monkeypatch.setattr(brain_prompt_check, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_prompt_check, "resolve_project_vault", lambda cwd: ("test", {"path": str(vault)}, vault, None))
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_configured", lambda: True)
    monkeypatch.setattr(brain_prompt_check, "local_brain_setup_suggestions_enabled", lambda: False)

    hook_input = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(vault),
        "prompt": "What is the frobnicator routing decision?",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit):
        brain_prompt_check.main()

    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert str(article) in context
    assert "BRAIN SAVE:" in context


def test_claude_code_prompt_field_off_level_emits_save_policy(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(brain_prompt_check, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_prompt_check, "load_registry", lambda: {"settings": {"context_injection": "off"}, "vaults": {}})
    monkeypatch.setattr(brain_prompt_check, "resolve_project_vault", lambda cwd: (None, None, None, None))
    monkeypatch.setattr(brain_prompt_check, "get_context_injection_level", lambda fritz_local: "off")

    hook_input = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(tmp_path),
        "prompt": "What is the frobnicator routing decision?",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit):
        brain_prompt_check.main()

    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("BRAIN SAVE:")


@pytest.mark.parametrize(
    ("hook_input", "expected"),
    [
        ({"prompt": "claude prompt", "user_prompt": "legacy prompt"}, "claude prompt"),
        ({"user_prompt": "legacy user_prompt fallback"}, "legacy user_prompt fallback"),
        ({"message": {"content": "pi message fallback"}}, "pi message fallback"),
    ],
)
def test_legacy_prompt_input_shapes_are_pinned(hook_input, expected):
    assert brain_prompt_check._extract_prompt(hook_input) == expected


def test_hermes_binding_has_no_prompt_check_payload_shape_to_support():
    hermes_hooks = (ROOT / "bindings" / "hermes" / "hermes-hooks.yaml").read_text(encoding="utf-8")

    assert "hermes_brain_context.py" in hermes_hooks
    assert "brain_prompt_check.py" not in hermes_hooks


def test_light_context_search_is_bounded_without_project_dirs(tmp_path):
    vault = tmp_path / "vault"
    knowledge = vault / "knowledge"
    knowledge.mkdir(parents=True)
    article = knowledge / "frobnicator-routing.md"
    article.write_text("# Frobnicator Routing\n", encoding="utf-8")
    max_chars = 96

    context = brain_prompt_check.search_knowledge_files(
        vault,
        {"paths": {"knowledge": "knowledge"}},
        ["frobnicator"],
        None,
        max_chars,
    )

    assert len(context) <= max_chars
    if context:
        assert "Read these files before responding." in context
    path_lines = [line for line in context.splitlines() if line.startswith("- ")]
    assert path_lines in ([], [f"- {article}"])


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


def test_service_instructions_prefer_semantic_search_for_brain_check(monkeypatch):
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "brain_search" in instructions
    assert "/v1/search/run" in instructions
    assert "Exact query compatibility only" in instructions
    assert instructions.index("/v1/search/run") < instructions.index("/v1/query/run")


def _clear_claude_markers(monkeypatch):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)


def test_resolve_client_agent_uses_fritz_agent_env(monkeypatch):
    monkeypatch.setenv("FRITZ_AGENT", "pi")
    assert brain_common._resolve_client_agent() == "pi"


def test_resolve_client_agent_defaults_to_unknown(monkeypatch):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    _clear_claude_markers(monkeypatch)
    assert brain_common._resolve_client_agent() == "unknown"


def test_resolve_client_agent_whitespace_falls_back_to_unknown(monkeypatch):
    monkeypatch.setenv("FRITZ_AGENT", "   ")
    _clear_claude_markers(monkeypatch)
    assert brain_common._resolve_client_agent() == "unknown"


def test_resolve_client_agent_detects_claude_from_claudecode_env(monkeypatch):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    assert brain_common._resolve_client_agent() == "claude"


def test_resolve_client_agent_detects_claude_from_entrypoint_env(monkeypatch):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    assert brain_common._resolve_client_agent() == "claude"


def test_resolve_client_agent_detects_claude_from_plugin_root_env(monkeypatch, tmp_path):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    assert brain_common._resolve_client_agent() == "claude"


def test_resolve_client_agent_detects_claude_from_empty_claudecode_env(monkeypatch):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.setenv("CLAUDECODE", "")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    assert brain_common._resolve_client_agent() == "claude"


def test_resolve_client_agent_fritz_agent_wins_over_claude_markers(monkeypatch):
    monkeypatch.setenv("FRITZ_AGENT", "pi")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert brain_common._resolve_client_agent() == "pi"


def test_service_instructions_include_x_brain_agent_in_search_and_query(monkeypatch):
    monkeypatch.setenv("FRITZ_AGENT", "pi")
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "X-Brain-Agent: pi" in instructions
    # present in both the search and query snippets
    search_line = next(line for line in instructions.splitlines() if "/v1/search/run" in line)
    query_line = next(line for line in instructions.splitlines() if "/v1/query/run" in line)
    assert "X-Brain-Agent" in search_line
    assert "X-Brain-Agent" in query_line


def test_service_instructions_codex_use_http_not_native_mcp(monkeypatch):
    _clear_claude_markers(monkeypatch)
    monkeypatch.setenv("FRITZ_AGENT", "codex")
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "Brain MCP tools are not registered natively by this binding" in instructions
    assert "Agent integration order: use registered MCP tools first" not in instructions
    assert "Query guidance: keep HTTP search/query bodies SHORT and CONCEPTUAL" in instructions
    assert "/v1/search/run" in instructions
    assert "/v1/query/run" in instructions
    assert "X-Brain-Agent: codex" in instructions
    assert "Do not also run `/fritz:brain-query`" in instructions


_NON_CLAUDE_INSTRUCTIONS_SNAPSHOT = (
    "## Local Brain Service Active\n\n"
    "The Dockerized Local Brain service is reachable at `http://127.0.0.1:8765`. "
    "For supported workflows, use this service layer first instead of duplicating the old local slash-skill workflow.\n\n"
    "Agent integration order: use registered MCP tools first when available and authorized (`brain_search`, `brain_query`, `brain_compile`, `brain_sync`, `brain_lint`), "
    "then HTTP calls from the host. The optional CLI is for installed local packages only; do not assume it is on the host PATH.\n\n"
    "Query guidance: keep `brain_search`/`brain_query` queries SHORT and CONCEPTUAL (2-6 terms), "
    "never verbatim log/keyword dumps, IP addresses, or hostnames. "
    "Good: `proxmox cloudinit template debian`. "
    "Bad: `racktaq Proxmox Debian 13 cloudinit template gateway VM WireGuard macOS client 192.168.1.53`.\n\n"
    "Supported service-backed workflows:\n"
    "- Search/brain check, semantic when embeddings are enabled: `curl -fsS -X POST http://127.0.0.1:8765/v1/search/run -H 'content-type: application/json' -H 'X-Brain-Agent: unknown' -d '{\"query\":\"<query>\"}'`\n"
    "- Exact query compatibility only, not the default brain check: `curl -fsS -X POST http://127.0.0.1:8765/v1/query/run -H 'content-type: application/json' -H 'X-Brain-Agent: unknown' -d '{\"query\":\"<query>\"}'`\n"
    "- Compile: `curl -fsS -X POST http://127.0.0.1:8765/v1/compile/run -H 'content-type: application/json' -H 'X-Brain-Agent: unknown' -d '{\"dry_run\":true}'`\n"
    "- Sync: `curl -fsS -X POST http://127.0.0.1:8765/v1/sync/run -H 'content-type: application/json' -H 'X-Brain-Agent: unknown' -d '{\"dry_run\":true}'`\n"
    "- Lint: `curl -fsS -X POST http://127.0.0.1:8765/v1/lint/run -H 'content-type: application/json' -d '{}'`\n"
    "- Embeddings: `curl -fsS http://127.0.0.1:8765/v1/embeddings/status` and `curl -fsS -X POST http://127.0.0.1:8765/v1/embeddings/probe -H 'content-type: application/json' -d '{\"dry_run\":true}'`\n\n"
    "Do not also run `/fritz:brain-query`, `/fritz:brain-compile`, `/fritz:brain-sync`, or `/fritz:brain-lint` "
    "for the same work unless the service is unavailable or the human explicitly requests the non-service path. "
    "Use the existing local skills only for workflows the service does not provide, such as setup, ingest, update, and writing the handover document itself."
)


def test_service_instructions_snapshot_unchanged_for_non_claude_runtime(monkeypatch):
    """Pins the rendered instructions when the agent resolves to 'unknown' (#238):
    a regression in the template for non-Claude runtimes must fail this test."""
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    _clear_claude_markers(monkeypatch)
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert instructions == _NON_CLAUDE_INSTRUCTIONS_SNAPSHOT


def test_service_instructions_render_claude_label_when_claudecode_set(monkeypatch):
    monkeypatch.delenv("FRITZ_AGENT", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(brain_common, "get_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)

    instructions = brain_common.local_brain_service_instructions()

    assert "X-Brain-Agent: claude" in instructions
    assert "X-Brain-Agent: unknown" not in instructions


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


def test_auto_compile_posts_to_service_when_enabled(monkeypatch, tmp_path):
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"captures_considered": 1}'

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, req.get_method(), req.data, timeout, dict(req.header_items())))
        return Response()

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: "secret")
    monkeypatch.setattr(brain_common.request, "urlopen", fake_urlopen)
    (tmp_path / ".compile-needed").write_text("{}", encoding="utf-8")
    (tmp_path / ".compile-failed").write_text("{}", encoding="utf-8")

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "compiled"
    assert len(calls) == 1
    assert calls[0][0] == "http://127.0.0.1:8765/v1/compile/run"
    assert calls[0][1] == "POST"
    assert json.loads(calls[0][2].decode("utf-8")) == {"dry_run": False}
    assert (tmp_path / ".compile-needed").exists() is False
    assert (tmp_path / ".compile-failed").exists() is False


def test_auto_compile_service_timeout_marks_pending_without_fallback(monkeypatch, tmp_path):
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"scheduled"}'

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if req.full_url.endswith("/v1/compile/run"):
            raise socket.timeout("compile still running")
        return Response()

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)
    monkeypatch.setattr(brain_common.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(brain_common, "_run_in_process_compile", lambda: (_ for _ in ()).throw(AssertionError("fallback not expected")))

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "pending"
    assert calls == [
        "http://127.0.0.1:8765/v1/compile/run",
        "http://127.0.0.1:8765/v1/embeddings/index/schedule",
    ]
    marker = json.loads((tmp_path / ".compile-needed").read_text(encoding="utf-8"))
    assert marker["processing_active"] is True
    assert "already running" in marker["reason"]


def test_in_process_embedding_refresh_gate_attempts_non_forced_refresh_for_successful_compile():
    class Settings:
        embedding_enabled = True
        embedding_refresh_after_compile = True

    class Result:
        errors = []
        applied = [object()]
        skipped = []

    assert brain_common._should_refresh_embeddings_after_in_process_compile(Settings(), Result()) is True

    class Disabled(Settings):
        embedding_refresh_after_compile = False

    assert brain_common._should_refresh_embeddings_after_in_process_compile(Disabled(), Result()) is False

    class Empty(Result):
        applied = []
        skipped = []

    assert brain_common._should_refresh_embeddings_after_in_process_compile(Settings(), Empty()) is False


def test_auto_compile_urllib_wrapped_timeout_marks_pending_without_fallback(monkeypatch, tmp_path):
    def fake_urlopen(req, timeout):
        raise brain_common.error.URLError(socket.timeout("compile still running"))

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)
    monkeypatch.setattr(brain_common.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(brain_common, "_run_in_process_compile", lambda: (_ for _ in ()).throw(AssertionError("fallback not expected")))

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "pending"
    marker = json.loads((tmp_path / ".compile-needed").read_text(encoding="utf-8"))
    assert marker["processing_active"] is True
    assert "already running" in marker["reason"]


def test_auto_compile_keeps_marker_when_service_compile_is_partial(monkeypatch, tmp_path):
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"captures_considered": 1}'

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        return Response()

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)
    monkeypatch.setattr(brain_common.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(brain_common, "_pending_capture_count", lambda: 1)
    inbox = tmp_path / "capture" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "one.md").write_text("one", encoding="utf-8")
    (inbox / "two.md").write_text("two", encoding="utf-8")

    result = brain_common.auto_compile_after_capture(inbox / "two.md")

    assert result.status == "pending"
    marker = json.loads((tmp_path / ".compile-needed").read_text(encoding="utf-8"))
    assert marker["processing_active"] is True
    assert "1 captures remain" in marker["reason"]


def test_auto_compile_keeps_marker_when_service_compile_leaves_capture_pending(monkeypatch, tmp_path):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"captures_considered": 1}'

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)
    monkeypatch.setattr(brain_common.request, "urlopen", lambda req, timeout: Response())
    monkeypatch.setattr(brain_common, "_pending_capture_count", lambda: 1)

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "pending"
    assert (tmp_path / ".compile-needed").exists()


def test_auto_compile_service_errors_write_failure_marker(monkeypatch, tmp_path):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"errors": ["policy failed"]}'

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)
    monkeypatch.setattr(brain_common.request, "urlopen", lambda req, timeout: Response())
    monkeypatch.setattr(
        brain_common,
        "_run_in_process_compile",
        lambda: (_ for _ in ()).throw(AssertionError("fallback not expected")),
    )

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "failed"
    assert "policy failed" in result.message
    assert "policy failed" in (tmp_path / ".compile-failed").read_text(encoding="utf-8")


def test_auto_compile_compile_already_running_is_idempotent(monkeypatch, tmp_path):
    def fake_urlopen(req, timeout):
        raise brain_common.error.HTTPError(req.full_url, 409, "Compile already running", hdrs=None, fp=None)

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(brain_common, "get_local_brain_api_token", lambda: None)
    monkeypatch.setattr(brain_common.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(brain_common, "_run_in_process_compile", lambda: (_ for _ in ()).throw(AssertionError("fallback not expected")))

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "pending"
    marker = json.loads((tmp_path / ".compile-needed").read_text(encoding="utf-8"))
    assert marker["processing_active"] is True
    assert "already running" in marker["reason"]


def test_auto_compile_writes_failure_marker_when_no_processor(monkeypatch, tmp_path):
    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: None)
    monkeypatch.setattr(brain_common, "_run_in_process_compile", lambda: (_ for _ in ()).throw(RuntimeError("missing model")))

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "failed"
    assert "missing model" in result.message
    assert "missing model" in (tmp_path / ".compile-failed").read_text(encoding="utf-8")
    assert json.loads((tmp_path / ".compile-needed").read_text(encoding="utf-8"))["topics"] == 1


def test_auto_compile_in_process_compile_already_running_is_idempotent(monkeypatch, tmp_path):
    class OperationAlreadyRunning(RuntimeError):
        pass

    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: True)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {"auto_compile_on_ingest": True})
    monkeypatch.setattr(brain_common, "_validated_local_brain_base_url", lambda: None)
    monkeypatch.setattr(
        brain_common,
        "_run_in_process_compile",
        lambda: (_ for _ in ()).throw(OperationAlreadyRunning("Compile already running")),
    )

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "pending"
    assert "already running" in result.message
    assert (tmp_path / ".compile-failed").exists() is False
    marker = json.loads((tmp_path / ".compile-needed").read_text(encoding="utf-8"))
    assert marker["processing_active"] is True


def test_auto_compile_disabled_records_processing_inactive(monkeypatch, tmp_path):
    monkeypatch.setattr(brain_common, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_common, "local_brain_service_enabled", lambda: False)
    monkeypatch.setattr(brain_common, "get_local_brain_service_config", lambda: {})

    result = brain_common.auto_compile_after_capture(tmp_path / "capture" / "daily" / "today.md")

    assert result.status == "disabled"
    assert "not active" in result.message
    assert json.loads((tmp_path / ".compile-needed").read_text(encoding="utf-8"))["processing_active"] is False


def test_is_trivial_ack_tokens_match_whole_word_only():
    """Ack tokens must NOT suppress substantive prompts that merely start with
    the same letters.  Before the fix, startswith("go") suppressed "google…",
    startswith("no") suppressed "normally…", and startswith("ok") suppressed
    "okay so…".
    """
    # Substantive prompts that share a prefix with an ack token — NOT trivial
    assert not brain_prompt_check._is_trivial("google the postgres connection string location")
    assert not brain_prompt_check._is_trivial("going to store the API token in 1Password")
    assert not brain_prompt_check._is_trivial("normally we skip this step")
    assert not brain_prompt_check._is_trivial("okay so the token is in 1Password")

    # Bare ack tokens — trivial
    assert brain_prompt_check._is_trivial("go")
    assert brain_prompt_check._is_trivial("ok")
    assert brain_prompt_check._is_trivial("no")
    assert brain_prompt_check._is_trivial("yes")
    assert brain_prompt_check._is_trivial("continue")
    assert brain_prompt_check._is_trivial("commit")
    assert brain_prompt_check._is_trivial("push")
    assert brain_prompt_check._is_trivial("merge")

    # Ack token + trailing words — trivial
    assert brain_prompt_check._is_trivial("go ahead")
    assert brain_prompt_check._is_trivial("ok do it")
    assert brain_prompt_check._is_trivial("no thanks")
    assert brain_prompt_check._is_trivial("merge it")
    assert brain_prompt_check._is_trivial("yes please")


def test_google_prompt_produces_save_policy_output(monkeypatch, capsys, tmp_path):
    """'google the postgres connection string location' starts with 'go' but is
    NOT an ack — it must reach the save-policy injection path.
    Before the fix, startswith("go") suppressed it.
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT / "bindings" / "claude"))
    context = _run_prompt_hook(
        monkeypatch, capsys, tmp_path,
        "google the postgres connection string location",
    )
    # Runs in-process with the Claude plugin marker, so the save-policy skill
    # reference is the sanitized plugin-qualified name (#239).
    assert "/fritz-brain:brain-save" in context, (
        "'google…' was wrongly suppressed as trivial; save policy not injected"
    )


def test_going_prompt_produces_save_policy_output(monkeypatch, capsys, tmp_path):
    """'going to store the API token in 1Password' starts with 'go' but is NOT
    an ack — it must reach the save-policy injection path.
    Before the fix, startswith("go") suppressed it.
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT / "bindings" / "claude"))
    context = _run_prompt_hook(
        monkeypatch, capsys, tmp_path,
        "going to store the API token in 1Password",
    )
    # Runs in-process with the Claude plugin marker, so the save-policy skill
    # reference is the sanitized plugin-qualified name (#239).
    assert "/fritz-brain:brain-save" in context, (
        "'going…' was wrongly suppressed as trivial; save policy not injected"
    )


def test_brain_capture_only_captures_never_compiles(monkeypatch, tmp_path):
    """#167 Fix B — capture only captures; it never hand-compiles (the scheduler
    owns compile, #162/v1.3.54). The auto_compile_after_capture call was removed,
    so the module must not even reference it; capture still exits 0 and writes the
    daily rollup."""
    # The removed dependency is no longer imported into the capture module.
    assert not hasattr(brain_capture, "auto_compile_after_capture")

    monkeypatch.setattr(brain_capture, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_capture, "CAPTURE_DIR", tmp_path / "capture" / "daily")
    monkeypatch.setattr(
        brain_capture,
        "read_hook_input",
        lambda: {"transcript_path": str(tmp_path / "transcript.jsonl"), "hook_event_name": "Stop", "cwd": str(ROOT)},
    )
    monkeypatch.setattr(
        brain_capture,
        "parse_transcript",
        lambda hook_input, transcript_path: CaptureEntry(agent="test", cwd=str(ROOT), topics=["topic"]),
    )

    with pytest.raises(SystemExit) as exc:
        brain_capture.main()

    assert exc.value.code == 0
    daily = tmp_path / "capture" / "daily"
    assert daily.exists() and list(daily.glob("*.md")), "daily rollup must still be written"


def _run_level_hook(monkeypatch, capsys, tmp_path, vault, level, prompt, article_count=1):
    knowledge = vault / "knowledge"
    manifest_dir = vault / ".brain"
    knowledge.mkdir(parents=True)
    manifest_dir.mkdir()
    for i in range(article_count):
        article = knowledge / f"frobnicator-routing-{i}.md"
        article.write_text(f"# Frobnicator Routing {i}\n\nUse the blue route.", encoding="utf-8")
    (manifest_dir / "manifest.yaml").write_text("paths:\n  knowledge: knowledge\n", encoding="utf-8")

    monkeypatch.setattr(
        brain_common,
        "load_registry",
        lambda: {"settings": {"context_injection": level, "max_injection_chars": 20000}},
    )
    monkeypatch.setattr(brain_prompt_check, "BRAIN_HOME", tmp_path)
    monkeypatch.setattr(brain_prompt_check, "load_registry", lambda: {"vaults": {"test": {"path": str(vault)}}})
    monkeypatch.setattr(brain_prompt_check, "resolve_project_vault", lambda cwd: ("test", {"path": str(vault)}, vault, None))
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_available", lambda: False)
    monkeypatch.setattr(brain_prompt_check, "local_brain_service_configured", lambda: True)
    monkeypatch.setattr(brain_prompt_check, "local_brain_setup_suggestions_enabled", lambda: False)

    capture_dir = tmp_path / "capture" / "daily"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "today.md").write_text("capture")

    hook_input = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(vault),
        "user_prompt": prompt,
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit):
        brain_prompt_check.main()

    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


MANDATE_MARKERS = ("MANDATORY", "MUST spawn", "spawn a subagent", "not optional")


@pytest.mark.parametrize("level", ["off", "light", "full"])
def test_no_level_emits_unconditional_subagent_mandate(monkeypatch, capsys, tmp_path, level):
    """#355 — no injected text may order a model to spawn a subagent, at any
    context_injection level."""
    vault = tmp_path / "vault"
    context = _run_level_hook(
        monkeypatch, capsys, tmp_path, vault, level,
        "What is the frobnicator routing decision?",
    )
    for marker in MANDATE_MARKERS:
        assert marker not in context, f"{marker!r} leaked into level={level} injection: {context!r}"


def test_full_level_injects_strictly_more_context_than_light(monkeypatch, capsys, tmp_path):
    """#355 — full must still retrieve more/richer context than light, even
    though it no longer conscripts a subagent."""
    prompt = "What is the frobnicator routing decision?"

    light_vault = tmp_path / "light_vault"
    light_context = _run_level_hook(
        monkeypatch, capsys, tmp_path, light_vault, "light", prompt, article_count=15,
    )

    full_vault = tmp_path / "full_vault"
    full_context = _run_level_hook(
        monkeypatch, capsys, tmp_path, full_vault, "full", prompt, article_count=15,
    )

    assert len(full_context) > len(light_context)
    full_articles = [line for line in full_context.splitlines() if "frobnicator-routing-" in line]
    light_articles = [line for line in light_context.splitlines() if "frobnicator-routing-" in line]
    assert len(full_articles) > len(light_articles), "full must surface more matched articles than light"
