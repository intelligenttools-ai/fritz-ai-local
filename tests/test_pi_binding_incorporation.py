"""Contract tests for incorporating the pi extension as the role model (issue #57).

The live pi extension is brought into this repo as bindings/pi/index.ts. These
tests assert the incorporated copy is repointed at the Forgejo remote, has no
hardcoded runtime path, and uses the FRITZ_REPO_PATH env override. They also
assert the capability spec documents all 9 capabilities from epic #55.

These are pure file-contract tests; they never touch the live ~/.brain.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PI_BINDING = REPO_ROOT / "bindings" / "pi" / "index.ts"
CAPABILITY_SPEC = REPO_ROOT / "docs" / "capability-spec.md"


def _binding_source() -> str:
    return PI_BINDING.read_text(encoding="utf-8")


def test_pi_binding_exists_and_non_empty():
    assert PI_BINDING.exists(), f"missing {PI_BINDING}"
    assert PI_BINDING.stat().st_size > 0, "bindings/pi/index.ts is empty"


def test_pi_binding_has_no_github_remote():
    source = _binding_source()
    assert "github.com" not in source, "github.com remote must be repointed to Forgejo"


def test_pi_binding_has_no_hardcoded_home_path():
    source = _binding_source()
    # The original hardcoded `join(homedir(), ".fritz-ai-local")` must be gone.
    assert '".fritz-ai-local"' not in source, (
        "hardcoded ~/.fritz-ai-local literal must not remain"
    )
    # Defensive: the exact homedir() + literal pairing must not survive in any form.
    assert 'homedir(), ".fritz-ai-local"' not in source


def test_pi_binding_uses_env_override():
    source = _binding_source()
    assert "FRITZ_REPO_PATH" in source, "must honor the FRITZ_REPO_PATH env override"


def test_pi_binding_points_at_forgejo_host():
    source = _binding_source()
    assert "git.intelligenttools.ai" in source, "must use the Forgejo host"


def test_pi_binding_resolves_path_relative_to_file():
    """The dynamic resolution must derive the repo from the file's own location.

    index.ts is two levels below the repo root, so it must walk up two dirs.
    """
    source = _binding_source()
    assert "import.meta.url" in source, "must resolve from the module's own URL"


def test_pi_binding_registers_api_backed_brain_tools_and_mcp_config():
    source = _binding_source() + "\n" + "\n".join(
        (PI_BINDING.parent / "runtime" / "current" / name).read_text(encoding="utf-8")
        for name in ("pi-config.mjs", "private-json.mjs", "run-command.mjs")
    )
    for name in ("brain_search", "brain_query", "brain_compile", "brain_sync", "brain_lint"):
        assert f'name: "{name}"' in source
    assert 'brainHook("brain_service_request.py")' in source
    assert 'FRITZ_AGENT: "pi"' in source
    assert 'join(homedir(), ".pi", "mcp.json")' in source
    assert 'bearerTokenEnv' in source
    assert "token_env_ready" in source
    assert 'delete servers["fritz-brain"]' in source
    assert '"fritz-brain": desired' in source
    assert "ensureClaudeAgentSdkIsolation" in source
    assert "strictMcpConfig: true" in source
    assert "Pi MCP registration could not be persisted" in source
    assert "Claude Agent SDK MCP isolation could not be verified" in source
    assert "Repair ~/.pi/agent/settings.json and reload Pi" in source
    assert "LOCK_WAIT_ATTEMPTS" in source
    assert "createdAt" in source
    assert "LOCK_STALE_MS" in source
    assert "configuration changed outside the managed lock" in source
    assert "renameSync(tempPath, path)" in source
    assert 'signal?.addEventListener("abort", abort, { once: true })' in source
    assert 'child.kill("SIGKILL")' in source
    assert 'ctx.cwd, signal)' in source


def test_capability_spec_exists():
    assert CAPABILITY_SPEC.exists(), f"missing {CAPABILITY_SPEC}"
    assert CAPABILITY_SPEC.stat().st_size > 0, "capability-spec.md is empty"


def test_capability_spec_mentions_all_nine_capabilities():
    spec = CAPABILITY_SPEC.read_text(encoding="utf-8")
    required_keywords = [
        "Context injection",      # 1
        "BRAIN CHECK",            # 2
        "brain_save_fact",        # 3
        "Auto-capture",           # 4
        "Session capture",        # 5
        "Mode detection",         # 6
        "Bootstrap",              # 7
        "Skills",                 # 8
        "config",                 # 9
    ]
    missing = [kw for kw in required_keywords if kw not in spec]
    assert not missing, f"capability-spec.md missing keywords: {missing}"
