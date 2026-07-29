"""Behavioral tests for Pi's security-sensitive runtime helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PI_DIR = REPO_ROOT / "bindings" / "pi" / "runtime" / "current"


def _node(script: str, *args: str, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "--input-type=module", "-e", script, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_pi_config_preserves_unrelated_settings_and_servers(tmp_path):
    settings = tmp_path / "settings.json"
    mcp = tmp_path / "mcp.json"
    absent_settings = tmp_path / "new-settings.json"
    settings.write_text(json.dumps({"packages": ["claude-agent-sdk-pi"], "theme": "dark"}))
    mcp.write_text(json.dumps({"custom": 7, "mcpServers": {"other": {"url": "https://other.test/mcp"}}}))
    script = """
      import { readFileSync } from 'node:fs';
      const mod = await import(process.argv[1]);
      const settings = process.argv[2];
      const mcp = process.argv[3];
      const absentSettings = process.argv[4];
      const isolated = mod.ensureClaudeAgentSdkIsolation(settings);
      const createdIsolation = mod.ensureClaudeAgentSdkIsolation(absentSettings);
      const registered = mod.reconcilePiMcpConfig(mcp, {
        base_url: 'https://brain.example.test', api_token_env: 'BRAIN_TOKEN',
        has_token: true, token_env_ready: true,
      });
      const afterRegister = JSON.parse(readFileSync(mcp, 'utf8'));
      const removed = mod.reconcilePiMcpConfig(mcp, {
        base_url: 'https://brain.example.test', api_token_env: 'BRAIN_TOKEN',
        has_token: true, token_env_ready: false,
      });
      console.log(JSON.stringify({
        isolated, createdIsolation, registered, removed,
        settings: JSON.parse(readFileSync(settings, 'utf8')),
        absentSettings: JSON.parse(readFileSync(absentSettings, 'utf8')),
        afterRegister,
        afterRemove: JSON.parse(readFileSync(mcp, 'utf8')),
      }));
    """
    result = _node(script, (PI_DIR / "pi-config.mjs").as_uri(), str(settings), str(mcp), str(absent_settings))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["isolated"] is True
    assert data["createdIsolation"] is True
    assert data["absentSettings"] == {"claudeAgentSdkProvider": {"strictMcpConfig": True}}
    assert data["settings"]["theme"] == "dark"
    assert data["settings"]["claudeAgentSdkProvider"]["strictMcpConfig"] is True
    assert data["registered"] == "registered"
    assert data["afterRegister"]["custom"] == 7
    assert data["afterRegister"]["mcpServers"]["other"]["url"] == "https://other.test/mcp"
    assert data["afterRegister"]["mcpServers"]["fritz-brain"]["bearerTokenEnv"] == "BRAIN_TOKEN"
    assert data["removed"] == "removed"
    assert data["afterRemove"]["mcpServers"] == {"other": {"url": "https://other.test/mcp"}}


@pytest.mark.parametrize("invalid", ["not-an-object", [], None])
def test_pi_config_rejects_invalid_nested_values_without_replacing_files(tmp_path, invalid):
    settings = tmp_path / "settings.json"
    mcp = tmp_path / "mcp.json"
    settings_text = json.dumps({"claudeAgentSdkProvider": invalid, "preserve": True})
    mcp_text = json.dumps({"mcpServers": invalid, "preserve": True})
    settings.write_text(settings_text)
    mcp.write_text(mcp_text)
    script = """
      const mod = await import(process.argv[1]);
      const errors = [];
      try { mod.ensureClaudeAgentSdkIsolation(process.argv[2]); } catch (error) { errors.push(error.message); }
      try {
        mod.reconcilePiMcpConfig(process.argv[3], {
          base_url: 'https://brain.example.test', api_token_env: 'BRAIN_TOKEN',
          has_token: true, token_env_ready: true,
        });
      } catch (error) { errors.push(error.message); }
      console.log(JSON.stringify(errors));
    """
    result = _node(script, (PI_DIR / "pi-config.mjs").as_uri(), str(settings), str(mcp))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "claudeAgentSdkProvider must be a JSON object",
        "mcpServers must be a JSON object",
    ]
    assert settings.read_text() == settings_text
    assert mcp.read_text() == mcp_text


def test_private_json_rejects_malformed_input_without_replacing_it(tmp_path):
    config = tmp_path / "broken.json"
    config.write_text("{broken")
    script = """
      const { updatePrivateJsonFile } = await import(process.argv[1]);
      try {
        updatePrivateJsonFile(process.argv[2], value => { value.changed = true; return true; });
        process.exitCode = 2;
      } catch (error) {
        console.log(error.name);
      }
    """
    result = _node(script, (PI_DIR / "private-json.mjs").as_uri(), str(config))
    assert result.returncode == 0, result.stderr
    assert config.read_text() == "{broken"
    assert not Path(f"{config}.lock").exists()


def test_contender_backs_off_while_owner_metadata_is_being_published(tmp_path):
    config = tmp_path / "shared.json"
    config.write_text("{}")
    lock = Path(f"{config}.lock")
    lock.mkdir()
    worker = """
      const { updatePrivateJsonFile } = await import(process.argv[1]);
      updatePrivateJsonFile(process.argv[2], value => { value.updated = true; return true; });
    """
    process = subprocess.Popen(
        ["node", "--input-type=module", "-e", worker, (PI_DIR / "private-json.mjs").as_uri(), str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.1)
    shutil.rmtree(lock)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stdout + stderr
    assert json.loads(config.read_text()) == {"updated": True}


def test_two_stale_lock_contenders_serialize_and_preserve_changes(tmp_path):
    config = tmp_path / "shared.json"
    config.write_text(json.dumps({"unrelated": {"preserved": True}}))
    lock = Path(f"{config}.lock")
    lock.mkdir()
    (lock / "owner.json").write_text(json.dumps({"id": "stale", "pid": 1, "createdAt": "2000-01-01T00:00:00Z"}))
    old = 946684800
    os.utime(lock, (old, old))
    worker = """
      const { updatePrivateJsonFile } = await import(process.argv[1]);
      updatePrivateJsonFile(process.argv[2], value => {
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);
        value[process.argv[3]] = true;
        return true;
      });
    """
    command = ["node", "--input-type=module", "-e", worker, (PI_DIR / "private-json.mjs").as_uri(), str(config)]
    first = subprocess.Popen([*command, "first"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen([*command, "second"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_out, first_err = first.communicate(timeout=10)
    second_out, second_err = second.communicate(timeout=10)
    assert first.returncode == 0, first_out + first_err
    assert second.returncode == 0, second_out + second_err
    assert json.loads(config.read_text()) == {
        "unrelated": {"preserved": True},
        "first": True,
        "second": True,
    }
    assert not lock.exists()


def test_run_command_decodes_utf8_once_after_chunk_collection():
    script = """
      const { runCommand } = await import(process.argv[1]);
      const child = `process.stdout.write(Buffer.from([0xe2])); setTimeout(() => process.stdout.write(Buffer.from([0x82, 0xac])), 75);`;
      const result = await runCommand(process.execPath, ['-e', child], { timeoutMs: 5000 });
      console.log(JSON.stringify(result));
    """
    result = _node(script, (PI_DIR / "run-command.mjs").as_uri(), timeout=5)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == {"ok": True, "stdout": "€", "stderr": ""}


def test_run_command_rejects_oversized_output():
    script = """
      const { runCommand } = await import(process.argv[1]);
      const result = await runCommand(process.execPath, ['-e', `process.stdout.write('x'.repeat(1000))`], {
        timeoutMs: 5000,
        maxOutputBytes: 100,
      });
      console.log(JSON.stringify(result));
    """
    result = _node(script, (PI_DIR / "run-command.mjs").as_uri(), timeout=5)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["stderr"] == "Process output exceeded 100 bytes"


def test_run_command_handles_child_closing_stdin_early():
    script = """
      const { runCommand } = await import(process.argv[1]);
      const child = `process.stdin.destroy(); setTimeout(() => process.exit(0), 50);`;
      const result = await runCommand(process.execPath, ['-e', child], {
        stdin: 'x'.repeat(1024 * 1024),
        timeoutMs: 5000,
      });
      console.log(JSON.stringify(result));
    """
    result = _node(script, (PI_DIR / "run-command.mjs").as_uri(), timeout=5)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data["ok"], bool)


def test_run_command_cancellation_terminates_child(tmp_path):
    marker = tmp_path / "should-not-exist"
    script = """
      import { existsSync } from 'node:fs';
      const { runCommand } = await import(process.argv[1]);
      const marker = process.argv[2];
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 75);
      const result = await runCommand(process.execPath, [
        '-e',
        `const {writeFileSync}=require('node:fs'); setTimeout(()=>writeFileSync(process.argv[1], 'late'), 750);`,
        marker,
      ], { signal: controller.signal, timeoutMs: 5000 });
      await new Promise(resolve => setTimeout(resolve, 900));
      console.log(JSON.stringify({ result, markerExists: existsSync(marker) }));
    """
    result = _node(script, (PI_DIR / "run-command.mjs").as_uri(), str(marker), timeout=5)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["result"]["ok"] is False
    assert data["result"]["stderr"] == "Cancelled"
    assert data["markerExists"] is False
