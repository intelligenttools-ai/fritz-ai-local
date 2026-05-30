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
    assert calls[0][0] == "http://127.0.0.1:8765/v1/compile/run"
    assert calls[0][1] == "POST"
    assert json.loads(calls[0][2].decode("utf-8")) == {"dry_run": False}
    assert (tmp_path / ".compile-needed").exists() is False
    assert (tmp_path / ".compile-failed").exists() is False


def test_auto_compile_service_timeout_marks_pending_without_fallback(monkeypatch, tmp_path):
    def fake_urlopen(req, timeout):
        raise socket.timeout("compile still running")

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


def test_brain_capture_warns_but_does_not_fail_when_auto_compile_fails(monkeypatch, capsys, tmp_path):
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
    monkeypatch.setattr(
        brain_capture,
        "auto_compile_after_capture",
        lambda capture_path: brain_common.AutoCompileResult(status="failed", message="missing processor"),
    )

    with pytest.raises(SystemExit) as exc:
        brain_capture.main()

    assert exc.value.code == 0
    assert "Fritz Brain auto-compile warning: missing processor" in capsys.readouterr().err
