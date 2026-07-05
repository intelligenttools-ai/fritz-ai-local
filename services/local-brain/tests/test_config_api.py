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

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fritz_local_brain import compile_workflow, embeddings, env_persist, llm
from fritz_local_brain.api import auth, routes
from fritz_local_brain.app import create_app
from fritz_local_brain.config import CONFIG_FIELD_META, Settings
from fritz_local_brain.models import ArticleWriteProposal, CompileAgentOutput, CompileRunRequest

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

    # #226: LLM + embedding provider settings are now runtime-mutable.
    for name in (
        "llm_protocol",
        "llm_base_url",
        "llm_model",
        "llm_api_key",
        "llm_timeout_seconds",
        "embedding_enabled",
        "embedding_protocol",
        "embedding_base_url",
        "embedding_model",
        "embedding_api_key",
        "embedding_timeout_seconds",
    ):
        assert fields[name]["mutable"] is True, name
        assert fields[name]["requires"] == "runtime", name

    # autostart stays host-side / rebuild-required.
    assert fields["local_brain_autostart_installed"]["mutable"] is False
    assert fields["local_brain_autostart_installed"]["requires"] == "rebuild"


def test_config_get_surfaces_secret_flag(monkeypatch, tmp_path) -> None:
    fields = _client(monkeypatch, _settings(tmp_path)).get("/v1/config", headers=_AUTH).json()["fields"]
    assert fields["llm_api_key"]["secret"] is True
    assert fields["embedding_api_key"]["secret"] is True
    assert fields["llm_model"]["secret"] is False


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
    settings = _settings(tmp_path)  # autostart defaults False
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"local_brain_autostart_installed": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] == []
    assert len(body["rejected"]) == 1
    assert "re-provision" in body["rejected"][0]

    # Not applied to the singleton.
    assert settings.local_brain_autostart_installed is False
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


# ---------------------------------------------------------------------------
# #226: runtime-mutable LLM/embedding settings + write-only secrets.
# ---------------------------------------------------------------------------


def test_patch_llm_model_applies_live_and_persists(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_MODEL="old-model")
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_model": "new-model"})
    assert resp.status_code == 200
    assert resp.json()["applied"] == ["llm_model"]
    assert settings.llm_model == "new-model"
    assert "LLM_MODEL=new-model" in env_file.read_text(encoding="utf-8")


def test_patch_llm_timeout_accepts_float(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_timeout_seconds": 120.0})
    assert resp.status_code == 200
    assert settings.llm_timeout_seconds == 120.0
    assert "LLM_TIMEOUT_SECONDS=120.0" in env_file.read_text(encoding="utf-8")


def test_patch_llm_timeout_rejects_non_numeric(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_timeout_seconds": "soon"})
    assert resp.status_code == 400


def _secret_roundtrip(monkeypatch, tmp_path, field: str, env_key: str) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=env_file)

    # Set a secret: applied live + persisted, but NEVER echoed back.
    resp = client.patch("/v1/config", headers=_AUTH, json={field: "top-secret-value"})
    assert resp.status_code == 200
    assert getattr(settings, field) == "top-secret-value"
    assert "top-secret-value" not in resp.text, "PATCH response must not echo the key"
    assert f"{env_key}=top-secret-value" in env_file.read_text(encoding="utf-8")

    # GET exposes only a bool set-flag, never the value.
    got = client.get("/v1/config", headers=_AUTH)
    assert got.json()["fields"][field]["value"] is True
    assert "top-secret-value" not in got.text

    # Clearing with null stores None and the set-flag flips to False.
    resp = client.patch("/v1/config", headers=_AUTH, json={field: None})
    assert resp.status_code == 200
    assert getattr(settings, field) is None
    got = client.get("/v1/config", headers=_AUTH)
    assert got.json()["fields"][field]["value"] is False


def test_llm_api_key_write_only_roundtrip_and_clear(monkeypatch, tmp_path) -> None:
    _secret_roundtrip(monkeypatch, tmp_path, "llm_api_key", "LLM_API_KEY")


def test_embedding_api_key_write_only_roundtrip_and_clear(monkeypatch, tmp_path) -> None:
    _secret_roundtrip(monkeypatch, tmp_path, "embedding_api_key", "EMBEDDING_API_KEY")


# ---------------------------------------------------------------------------
# #226: a PATCHed llm_model reaches the NEXT compile (proves no startup cache).
# ---------------------------------------------------------------------------


class _CaptureAgent:
    def __init__(self, proposal: ArticleWriteProposal) -> None:
        self.proposal = proposal

    async def run(self, prompt: str, *, deps: object, usage_limits: object) -> SimpleNamespace:
        return SimpleNamespace(output=CompileAgentOutput(proposals=[self.proposal]))


def test_patched_llm_model_used_by_next_compile(monkeypatch, tmp_path) -> None:
    brain_home = tmp_path / "brain"
    capture_path = brain_home / "capture" / "inbox" / "fact.md"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("# Capture\n\nDurable fact.\n", encoding="utf-8")

    settings = _settings(brain_home, LOCAL_BRAIN_LLM_MODEL="old-model")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    proposal = ArticleWriteProposal(
        vault="brain",
        relative_path="facts/durable.md",
        operation="create",
        title="Durable Fact",
        summary="proposal",
        sources=[str(capture_path)],
        body="Durable body.",
    )
    seen_models: list[str] = []
    monkeypatch.setattr(
        compile_workflow,
        "build_compile_agent",
        lambda s, skill_text: (seen_models.append(s.llm_model), _CaptureAgent(proposal))[1],
    )
    monkeypatch.setattr(compile_workflow, "append_global_log", lambda *a, **k: None)

    # PATCH the model live, then run the next compile against the SAME singleton.
    assert client.patch("/v1/config", headers=_AUTH, json={"llm_model": "patched-model"}).status_code == 200
    asyncio.run(compile_workflow.run_compile(settings, CompileRunRequest(dry_run=True, max_captures=1)))

    assert seen_models == ["patched-model"], "next compile must build the model from the PATCHed value"


# ---------------------------------------------------------------------------
# #226: connection-test probe endpoints (mocked upstream, never persist/mutate).
# ---------------------------------------------------------------------------


class _FakeModels:
    def __init__(self, boom: bool) -> None:
        self._boom = boom

    async def list(self):
        if self._boom:
            raise RuntimeError("connection refused")
        return SimpleNamespace(data=[])


def _fake_openai(captured: dict, boom: bool = False):
    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.models = _FakeModels(boom)

    return _FakeClient


def test_test_llm_ok(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    monkeypatch.setattr(llm, "AsyncOpenAI", _fake_openai({}))

    resp = client.post("/v1/config/test-llm", headers=_AUTH, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["latency_ms"] is not None
    assert body["error"] is None


def test_test_llm_error_path(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    monkeypatch.setattr(llm, "AsyncOpenAI", _fake_openai({}, boom=True))

    body = client.post("/v1/config/test-llm", headers=_AUTH, json={}).json()
    assert body["ok"] is False
    assert "connection refused" in body["error"]


def test_test_llm_uses_body_values_without_mutating_or_persisting(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LLM_BASE_URL="http://original:11434/v1", LOCAL_BRAIN_LLM_MODEL="orig")
    client = _client(monkeypatch, settings, env_file=env_file)
    captured: dict = {}
    monkeypatch.setattr(llm, "AsyncOpenAI", _fake_openai(captured))

    resp = client.post(
        "/v1/config/test-llm",
        headers=_AUTH,
        json={"base_url": "http://supplied:9999/v1", "api_key": "probe-key"},
    )
    assert resp.status_code == 200
    # The probe used the body-supplied endpoint...
    assert captured["base_url"] == "http://supplied:9999/v1"
    # ...but the singleton is untouched and nothing was persisted or leaked.
    assert settings.llm_base_url == "http://original:11434/v1"
    assert not env_file.exists()
    assert "probe-key" not in resp.text


def test_test_embedding_ok(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)  # embedding disabled by default
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    async def _fake_embed(s, text):
        return [0.1] * 8

    monkeypatch.setattr(embeddings, "_embed_text", _fake_embed)

    resp = client.post("/v1/config/test-embedding", headers=_AUTH, json={"model": "probe-embed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["error"] is None
    # Probe of a not-yet-enabled endpoint must NOT mutate the singleton.
    assert settings.embedding_enabled is False
    assert settings.embedding_model != "probe-embed"


# ---------------------------------------------------------------------------
# #226: an embedding provider change schedules a reindex; the PATCH says so.
# ---------------------------------------------------------------------------


def test_embedding_model_change_schedules_reindex(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_MODEL="old-embed")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    calls: list[str] = []
    monkeypatch.setattr(
        routes,
        "schedule_embedding_refresh_after_compile",
        lambda s, *, reason: calls.append(reason) or "scheduled",
    )

    resp = client.patch("/v1/config", headers=_AUTH, json={"embedding_model": "new-embed"})
    assert resp.status_code == 200
    assert resp.json()["reindex_scheduled"] is True
    assert len(calls) == 1


def test_non_provider_change_does_not_schedule_reindex(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    calls: list[str] = []
    monkeypatch.setattr(
        routes,
        "schedule_embedding_refresh_after_compile",
        lambda s, *, reason: calls.append(reason) or "scheduled",
    )

    resp = client.patch("/v1/config", headers=_AUTH, json={"telemetry_enabled": False})
    assert resp.status_code == 200
    assert resp.json()["reindex_scheduled"] is False
    assert calls == []


# ---------------------------------------------------------------------------
# #226 security review — adversarial fixes.
# ---------------------------------------------------------------------------


def _stub_schedule(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        routes,
        "schedule_embedding_refresh_after_compile",
        lambda s, *, reason: calls.append(reason) or "scheduled",
    )
    return calls


# FIX 1 — the saved (write-only) api_key must NEVER be sent to an endpoint the
# caller controls via a base_url / protocol override.


def test_probe_never_sends_saved_key_to_overridden_base_url(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="saved-secret")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    captured: dict = {}
    monkeypatch.setattr(llm, "AsyncOpenAI", _fake_openai(captured))

    resp = client.post("/v1/config/test-llm", headers=_AUTH, json={"base_url": "http://attacker:9/v1"})
    assert resp.status_code == 200
    # The probe hit the caller endpoint but did NOT carry the saved key.
    assert captured["base_url"] == "http://attacker:9/v1"
    assert captured["api_key"] != "saved-secret"
    assert "saved-secret" not in resp.text


def test_probe_never_sends_saved_key_to_overridden_protocol(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="saved-secret")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    captured: dict = {}
    monkeypatch.setattr(llm, "AsyncOpenAI", _fake_openai(captured))

    resp = client.post("/v1/config/test-llm", headers=_AUTH, json={"protocol": "openai-compatible"})
    assert resp.status_code == 200
    assert captured["api_key"] != "saved-secret"
    assert "saved-secret" not in resp.text


def test_probe_uses_body_key_on_overridden_endpoint(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="saved-secret")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    captured: dict = {}
    monkeypatch.setattr(llm, "AsyncOpenAI", _fake_openai(captured))

    resp = client.post(
        "/v1/config/test-llm",
        headers=_AUTH,
        json={"base_url": "http://new:9/v1", "api_key": "brand-new-key"},
    )
    assert resp.status_code == 200
    assert captured["api_key"] == "brand-new-key"
    assert "saved-secret" not in resp.text
    assert "brand-new-key" not in resp.text


def test_probe_saved_endpoint_uses_saved_key(monkeypatch, tmp_path) -> None:
    # Testing the ALREADY-SAVED endpoint (no override) legitimately uses the saved key.
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="saved-secret")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    captured: dict = {}
    monkeypatch.setattr(llm, "AsyncOpenAI", _fake_openai(captured))

    resp = client.post("/v1/config/test-llm", headers=_AUTH, json={})
    assert resp.status_code == 200
    assert captured["api_key"] == "saved-secret"
    assert "saved-secret" not in resp.text  # used, but never echoed


# FIX 2 — an upstream error must never leak key material via the returned error
# text or a log.


class _LeakModels:
    def __init__(self, message: str) -> None:
        self._message = message

    async def list(self):
        raise RuntimeError(self._message)


def test_probe_llm_error_redacts_saved_key(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="leaky-key-123")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    class _LeakClient:
        def __init__(self, **kwargs) -> None:
            self.models = _LeakModels("401 Unauthorized: Bearer leaky-key-123 rejected")

    monkeypatch.setattr(llm, "AsyncOpenAI", _LeakClient)

    body = client.post("/v1/config/test-llm", headers=_AUTH, json={}).json()
    assert body["ok"] is False
    assert "leaky-key-123" not in (body["error"] or "")
    assert "***" in body["error"]


def test_probe_embedding_error_redacts_saved_key(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_API_KEY="embed-leak-9")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    async def _boom(s, text):
        raise RuntimeError("embed 403 denied key=embed-leak-9")

    monkeypatch.setattr(embeddings, "_embed_text", _boom)

    body = client.post("/v1/config/test-embedding", headers=_AUTH, json={}).json()
    assert body["ok"] is False
    assert "embed-leak-9" not in (body["error"] or "")


# FIX 3 — nan/inf timeouts are rejected (coercion and probe request validation).


def test_patch_timeout_rejects_nan(monkeypatch, tmp_path) -> None:
    # A real client can't send bare JSON NaN; a form control sends the string "nan".
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=env_file)
    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_timeout_seconds": "nan"})
    assert resp.status_code == 400
    assert not env_file.exists()


def test_patch_timeout_rejects_inf(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=env_file)
    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_timeout_seconds": "inf"})
    assert resp.status_code == 400
    assert not env_file.exists()


def test_probe_timeout_inf_rejected(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    resp = client.post("/v1/config/test-llm", headers=_AUTH, json={"timeout_seconds": "inf"})
    assert resp.status_code == 422  # request-model validation rejects non-finite


# FIX 4 — a secret PATCH accepts only a string or null; never coerce other types.


def test_patch_secret_rejects_non_string(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=env_file)
    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_api_key": 123})
    assert resp.status_code == 400
    assert settings.llm_api_key is None  # not stored as "123"
    assert not env_file.exists()


def test_patch_secret_rejects_bool(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_api_key": True})
    assert resp.status_code == 400
    assert settings.llm_api_key is None


# FIX 6 — empty-string clears a secret exactly like null, and persists an empty
# assignment line while GET reports set:false.


def test_secret_empty_string_clears_like_null(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="preset")
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_api_key": ""})
    assert resp.status_code == 200
    assert settings.llm_api_key is None

    lines = [line.strip() for line in env_file.read_text(encoding="utf-8").splitlines()]
    assert "LLM_API_KEY=" in lines  # empty assignment, no value

    got = client.get("/v1/config", headers=_AUTH).json()["fields"]["llm_api_key"]
    assert got["value"] is False


# FIX 5 — reindex is scheduled only on an EFFECTIVE provider change.


def test_same_value_embedding_model_patch_does_not_schedule(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_MODEL="same-embed")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    calls = _stub_schedule(monkeypatch)

    resp = client.patch("/v1/config", headers=_AUTH, json={"embedding_model": "same-embed"})
    assert resp.status_code == 200
    assert resp.json()["reindex_scheduled"] is False
    assert calls == []


def test_embedding_protocol_change_schedules_reindex(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)  # default protocol openai-compatible
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    calls = _stub_schedule(monkeypatch)

    resp = client.patch("/v1/config", headers=_AUTH, json={"embedding_protocol": "anthropic-compatible"})
    assert resp.status_code == 200
    assert resp.json()["reindex_scheduled"] is True
    assert calls == ["config change"]


def test_embedding_base_url_change_schedules_reindex(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_BASE_URL="http://old-embed:11434/v1")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")
    calls = _stub_schedule(monkeypatch)

    resp = client.patch("/v1/config", headers=_AUTH, json={"embedding_base_url": "http://new-embed:9/v1"})
    assert resp.status_code == 200
    assert resp.json()["reindex_scheduled"] is True
    assert calls == ["config change"]


# ---------------------------------------------------------------------------
# #254 — clear the stored API key on an effective endpoint change.
#
# A key is bound to the endpoint/provider it was issued for. Repointing
# base_url/protocol without supplying a new key is both semantically wrong and
# an exfiltration channel (the old key would be sent to the new host), so the
# stored key must be cleared unless the same PATCH also supplies a new one.
# ---------------------------------------------------------------------------


def test_patch_llm_base_url_only_clears_api_key(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="preset-llm-key")
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_base_url": "http://new-llm:9/v1"})
    assert resp.status_code == 200
    body = resp.json()

    assert settings.llm_base_url == "http://new-llm:9/v1"
    assert settings.llm_api_key is None
    assert body["config"]["llm_api_key"]["value"] is False

    lines = [line.strip() for line in env_file.read_text(encoding="utf-8").splitlines()]
    assert "LLM_API_KEY=" in lines  # cleared: empty assignment, no value

    got = client.get("/v1/config", headers=_AUTH).json()["fields"]["llm_api_key"]
    assert got["value"] is False


def test_patch_llm_protocol_only_clears_api_key(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="preset-llm-key")
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_protocol": "anthropic-compatible"})
    assert resp.status_code == 200
    body = resp.json()

    assert settings.llm_api_key is None
    assert body["config"]["llm_api_key"]["value"] is False

    lines = [line.strip() for line in env_file.read_text(encoding="utf-8").splitlines()]
    assert "LLM_API_KEY=" in lines  # cleared: empty assignment, no value

    got = client.get("/v1/config", headers=_AUTH).json()["fields"]["llm_api_key"]
    assert got["value"] is False


def test_patch_llm_base_url_and_api_key_both_applied(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="preset-llm-key")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    resp = client.patch(
        "/v1/config",
        headers=_AUTH,
        json={"llm_base_url": "http://new-llm:9/v1", "llm_api_key": "brand-new-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert settings.llm_base_url == "http://new-llm:9/v1"
    assert settings.llm_api_key == "brand-new-key"
    assert body["config"]["llm_api_key"]["value"] is True


def test_patch_embedding_base_url_only_clears_api_key(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_API_KEY="preset-embed-key")
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"embedding_base_url": "http://new-embed:9/v1"})
    assert resp.status_code == 200
    body = resp.json()

    assert settings.embedding_api_key is None
    assert body["config"]["embedding_api_key"]["value"] is False

    lines = [line.strip() for line in env_file.read_text(encoding="utf-8").splitlines()]
    assert "EMBEDDING_API_KEY=" in lines  # cleared: empty assignment, no value

    got = client.get("/v1/config", headers=_AUTH).json()["fields"]["embedding_api_key"]
    assert got["value"] is False


def test_patch_embedding_protocol_only_clears_api_key(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_API_KEY="preset-embed-key")
    client = _client(monkeypatch, settings, env_file=env_file)

    resp = client.patch("/v1/config", headers=_AUTH, json={"embedding_protocol": "anthropic-compatible"})
    assert resp.status_code == 200
    body = resp.json()

    assert settings.embedding_api_key is None
    assert body["config"]["embedding_api_key"]["value"] is False

    lines = [line.strip() for line in env_file.read_text(encoding="utf-8").splitlines()]
    assert "EMBEDDING_API_KEY=" in lines  # cleared: empty assignment, no value

    got = client.get("/v1/config", headers=_AUTH).json()["fields"]["embedding_api_key"]
    assert got["value"] is False


def test_patch_embedding_base_url_and_api_key_both_applied(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_EMBEDDING_API_KEY="preset-embed-key")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    resp = client.patch(
        "/v1/config",
        headers=_AUTH,
        json={"embedding_base_url": "http://new-embed:9/v1", "embedding_api_key": "brand-new-embed-key"},
    )
    assert resp.status_code == 200
    assert settings.embedding_base_url == "http://new-embed:9/v1"
    assert settings.embedding_api_key == "brand-new-embed-key"


def test_patch_llm_model_only_does_not_clear_api_key(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, LOCAL_BRAIN_LLM_API_KEY="preset-llm-key", LOCAL_BRAIN_LLM_MODEL="old-model")
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_model": "new-model"})
    assert resp.status_code == 200
    assert settings.llm_model == "new-model"
    assert settings.llm_api_key == "preset-llm-key"


def test_patch_same_value_llm_base_url_does_not_clear_api_key(monkeypatch, tmp_path) -> None:
    settings = _settings(
        tmp_path,
        LOCAL_BRAIN_LLM_API_KEY="preset-llm-key",
        LOCAL_BRAIN_LLM_BASE_URL="http://same-llm:11434/v1",
    )
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    resp = client.patch("/v1/config", headers=_AUTH, json={"llm_base_url": "http://same-llm:11434/v1"})
    assert resp.status_code == 200
    assert settings.llm_api_key == "preset-llm-key"


def test_patch_same_value_embedding_base_url_does_not_clear_api_key(monkeypatch, tmp_path) -> None:
    settings = _settings(
        tmp_path,
        LOCAL_BRAIN_EMBEDDING_API_KEY="preset-embed-key",
        LOCAL_BRAIN_EMBEDDING_BASE_URL="http://same-embed:11434/v1",
    )
    client = _client(monkeypatch, settings, env_file=tmp_path / ".env")

    resp = client.patch(
        "/v1/config", headers=_AUTH, json={"embedding_base_url": "http://same-embed:11434/v1"}
    )
    assert resp.status_code == 200
    assert settings.embedding_api_key == "preset-embed-key"
