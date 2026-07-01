"""Tests for the live configuration API (#208).

Acceptance mapping:
- GET /v1/config lists every known field with correct mutable/requires flags.
- The llm_api_key value is NEVER returned (bool set-flag only).
- PATCH runtime-mutable applies live to the get_settings() singleton AND writes
  the env key to a tmp .env.
- PATCH rebuild-required is rejected and NOT persisted / NOT applied.
- Invalid value -> 400; unknown field -> 400.
- AUTH: GET and PATCH return 401 without the Bearer token.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain import env_persist
from fritz_local_brain.api import auth, routes
from fritz_local_brain.app import create_app
from fritz_local_brain.config import CONFIG_FIELD_META, Settings

_AUTH = {"Authorization": "Bearer secret"}


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, LOCAL_BRAIN_HOME=tmp_path, LOCAL_BRAIN_API_TOKEN="secret", **overrides)


def _client(monkeypatch, settings, env_file: Path | None = None) -> TestClient:
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    if env_file is not None:
        monkeypatch.setattr(env_persist, "resolve_env_path", lambda: env_file)
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# GET /v1/config
# ---------------------------------------------------------------------------


def test_config_get_lists_all_known_fields(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    resp = client.get("/v1/config", headers=_AUTH)
    assert resp.status_code == 200
    fields = resp.json()["fields"]
    assert set(fields) == set(CONFIG_FIELD_META)


def test_config_get_mutable_and_requires_flags(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    fields = client.get("/v1/config", headers=_AUTH).json()["fields"]

    assert fields["scheduler_enabled"]["mutable"] is True
    assert fields["scheduler_enabled"]["requires"] == "runtime"
    assert fields["reconciliation_autonomy"]["mutable"] is True

    assert fields["llm_base_url"]["mutable"] is False
    assert fields["llm_base_url"]["requires"] == "rebuild"
    assert fields["embedding_enabled"]["mutable"] is False


def test_config_get_never_leaks_api_key_value(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="super-secret-key")
    client = _client(monkeypatch, settings)
    fields = client.get("/v1/config", headers=_AUTH).json()["fields"]
    # The value must be a bool set-flag, never the raw key.
    assert fields["llm_api_key"]["value"] is True
    assert "super-secret-key" not in client.get("/v1/config", headers=_AUTH).text


def test_config_get_api_key_absent_reports_false(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    fields = client.get("/v1/config", headers=_AUTH).json()["fields"]
    assert fields["llm_api_key"]["value"] is False


def test_config_get_requires_auth(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    assert client.get("/v1/config").status_code == 401


# ---------------------------------------------------------------------------
# PATCH /v1/config
# ---------------------------------------------------------------------------


def test_patch_runtime_mutable_applies_live_and_persists(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch(
        "/v1/config",
        headers=_AUTH,
        json={"scheduler_enabled": True, "interval_minutes": 45, "reconciliation_autonomy": "propose"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["applied"]) == {"scheduler_enabled", "interval_minutes", "reconciliation_autonomy"}
    assert body["rejected"] == []

    # Applied live to the singleton.
    assert settings.scheduler_enabled is True
    assert settings.interval_minutes == 45
    assert settings.reconciliation_autonomy == "propose"

    # Persisted to the tmp .env using the canonical env keys.
    written = env_file.read_text(encoding="utf-8")
    assert "SCHEDULER_ENABLED=true" in written
    assert "BRAIN_INTERVAL_MINUTES=45" in written
    assert "RECONCILIATION_AUTONOMY=propose" in written

    # The returned effective config reflects the change.
    assert body["config"]["scheduler_enabled"]["value"] is True


def test_patch_rebuild_required_is_rejected_and_not_persisted(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_MODEL="original-model")
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_model": "new-model"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] == []
    assert len(body["rejected"]) == 1
    assert "re-provision" in body["rejected"][0]

    # Not applied to the singleton.
    assert settings.llm_model == "original-model"
    # Not persisted — no .env written (no runtime updates in this request).
    assert not env_file.exists()


def test_patch_invalid_value_returns_400(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    resp = client.patch("/v1/config", headers=_AUTH, json={"interval_minutes": "not-a-number"})
    assert resp.status_code == 400


def test_patch_invalid_autonomy_returns_400(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    resp = client.patch("/v1/config", headers=_AUTH, json={"reconciliation_autonomy": "sometimes"})
    assert resp.status_code == 400


def test_patch_unknown_field_returns_400(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    resp = client.patch("/v1/config", headers=_AUTH, json={"nonexistent": 1})
    assert resp.status_code == 400


def test_patch_bool_from_string_coerces(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    resp = client.patch("/v1/config", headers=_AUTH, json={"telemetry_enabled": "false"})
    assert resp.status_code == 200
    assert settings.telemetry_enabled is False


def test_patch_requires_auth(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _settings(tmp_path))
    assert client.patch("/v1/config", json={"scheduler_enabled": True}).status_code == 401


# ---------------------------------------------------------------------------
# HTTP verb contract (#208 review): the config write is PATCH, not POST.
# Guards against the client/route verb diverging again (the 405 UI bug).
# ---------------------------------------------------------------------------


def test_config_write_is_patch_not_post(monkeypatch, tmp_path) -> None:
    """POST /v1/config must be 405 (method not allowed) and PATCH must be 200.

    This documents the contract: the config write verb is PATCH. If a future
    change registers the route under a different verb, or a caller uses the wrong
    verb, this fails.
    """
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=env_file)

    post = client.post("/v1/config", headers=_AUTH, json={"scheduler_enabled": True})
    assert post.status_code == 405, "POST /v1/config must be rejected — the write verb is PATCH"

    patch = client.patch("/v1/config", headers=_AUTH, json={"scheduler_enabled": True})
    assert patch.status_code == 200, "PATCH /v1/config must succeed"


# ---------------------------------------------------------------------------
# Atomicity (#208 review): a partial-failure PATCH must mutate NOTHING.
# ---------------------------------------------------------------------------


def test_patch_invalid_value_leaves_live_state_and_env_untouched(monkeypatch, tmp_path) -> None:
    """A body mixing a valid runtime field with an invalid one must 400 and leave
    the live singleton UNCHANGED and the .env unwritten — no partial mutation.

    Before the two-pass fix, scheduler_enabled would have been flipped True on the
    singleton before the invalid interval_minutes raised, diverging live state
    from the (unwritten) .env in an order-dependent way.
    """
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)  # scheduler_enabled defaults False
    assert settings.scheduler_enabled is False
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch(
        "/v1/config",
        headers=_AUTH,
        json={"scheduler_enabled": True, "interval_minutes": "bad"},
    )
    assert resp.status_code == 400

    # No partial live mutation: the valid field was NOT applied.
    assert settings.scheduler_enabled is False, "invalid PATCH must not mutate the live singleton"
    # No .env written.
    assert not env_file.exists(), "invalid PATCH must not persist anything"
