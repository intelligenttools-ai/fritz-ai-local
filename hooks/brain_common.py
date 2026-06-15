"""Shared utilities for brain hooks across all agents."""

import asyncio
import json
import os
import re
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit, urlunsplit

import yaml


BRAIN_HOME = Path.home() / ".brain"
REGISTRY_PATH = BRAIN_HOME / "registry.yaml"


def load_registry() -> dict:
    """Load the vault registry."""
    if not REGISTRY_PATH.exists():
        return {"version": 1, "vaults": {}}
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"version": 1, "vaults": {}}


def get_default_vault() -> tuple[str | None, dict | None, Path | None]:
    """Get the default vault from the registry.

    Checks for 'default_vault' key, then falls back to first vault with status: active.
    Returns (vault_name, vault_config, vault_path) or (None, None, None).
    """
    registry = load_registry()
    vaults = registry.get("vaults", {})

    # Explicit default
    default_name = registry.get("default_vault")
    if default_name and default_name in vaults:
        config = vaults[default_name]
        return default_name, config, Path(config["path"]).expanduser().resolve()

    # Fallback: first vault with status: active
    for name, config in vaults.items():
        if config.get("status") == "active":
            return name, config, Path(config["path"]).expanduser().resolve()

    # Fallback: first vault
    if vaults:
        name = next(iter(vaults))
        config = vaults[name]
        return name, config, Path(config["path"]).expanduser().resolve()

    return None, None, None


def find_vault_for_cwd(cwd: str, fallback_to_default: bool = False) -> tuple[str | None, dict | None, Path | None]:
    """Find which vault the current working directory belongs to.

    If fallback_to_default is True and no vault matches cwd, returns the default vault.
    Returns (vault_name, vault_config, vault_path) or (None, None, None).
    """
    registry = load_registry()
    cwd_path = Path(cwd).resolve()

    for name, config in registry.get("vaults", {}).items():
        vault_path = Path(config["path"]).expanduser().resolve()
        try:
            cwd_path.relative_to(vault_path)
            return name, config, vault_path
        except ValueError:
            continue

    if fallback_to_default:
        return get_default_vault()

    return None, None, None


def load_manifest(vault_path: Path) -> dict | None:
    """Load the .brain/manifest.yaml for a vault."""
    manifest_path = vault_path / ".brain" / "manifest.yaml"
    if not manifest_path.exists():
        return None
    with open(manifest_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(vault_path: Path, manifest: dict, key: str) -> Path | None:
    """Resolve a manifest path key to an absolute path."""
    paths = manifest.get("paths", {})
    rel = paths.get(key)
    if not rel:
        return None
    return vault_path / rel


def append_log(vault_path: Path, operation: str, agent: str, summary: str):
    """Append an entry to .brain/log.md."""
    log_path = vault_path / ".brain" / "log.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"{timestamp} | {operation} | {agent} | {summary}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def read_hook_input() -> dict:
    """Read JSON input from stdin (Claude Code / Codex / Gemini hook protocol)."""
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return {}


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


FRITZ_LOCAL_FILENAME = ".fritz-local.json"


def _resolve_fritz_repo() -> Path:
    """Resolve the Fritz repo root, independent of clone location.

    Order of precedence:
    1. FRITZ_REPO_PATH environment variable (if set).
    2. The repo root derived from this file's own location.
       Path(__file__).resolve() follows symlinks, so when hooks are symlinked
       into ~/.brain/hooks/ this still points back to the real repo. The repo
       root is the parent of the hooks/ directory.
    """
    env_path = os.environ.get("FRITZ_REPO_PATH")
    if env_path and env_path.strip():
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


FRITZ_REPO = _resolve_fritz_repo()


def load_fritz_local(cwd: str) -> dict | None:
    """Walk up from cwd looking for .fritz-local.json. Return parsed JSON or None."""
    current = Path(cwd).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / FRITZ_LOCAL_FILENAME
        if candidate.exists():
            try:
                with open(candidate, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
    return None


def load_settings() -> dict:
    """Load global settings from registry.yaml. Returns empty dict if none."""
    registry = load_registry()
    return registry.get("settings", {})


# Sentinel distinguishing "key absent" from "key present with value None".
_MISSING = object()


def get_setting(
    key: str,
    default=None,
    *,
    fritz_local: dict | None = None,
    cwd: str | None = None,
):
    """Resolve a single project-overridable configuration value.

    This is the ONE generic resolution path for settings that may be
    overridden per project. Precedence is:

        1. project  -> ``.fritz-local.json`` (the ``fritz_local`` dict)
        2. central   -> ``registry.yaml`` ``settings:`` block
        3. default   -> the ``default`` argument

    The project layer may be supplied directly via ``fritz_local`` or, when
    that is ``None``, loaded by walking up from ``cwd`` for a
    ``.fritz-local.json`` (via :func:`load_fritz_local`). If both are ``None``
    the project layer is simply skipped.

    "Present but null/empty" semantics: a layer is considered to *provide* a
    value only when it contains ``key`` AND that value is not ``None``. A
    layer that omits the key, or sets it explicitly to ``null``/``None``,
    falls through to the next layer. This means a present ``.fritz-local.json``
    that lacks ``key`` does not mask the central value or default — it simply
    does not contribute, matching the historical behavior of the per-key
    getters that delegate here. (The injection getters layer their own enum/
    range validation on top of this lookup to preserve their stricter,
    documented edge-case behavior; see ``get_context_injection_level`` and
    ``get_max_injection_chars``.)
    """
    if fritz_local is None and cwd is not None:
        fritz_local = load_fritz_local(cwd)

    if isinstance(fritz_local, dict):
        value = fritz_local.get(key, _MISSING)
        if value is not _MISSING and value is not None:
            return value

    central = load_settings()
    if isinstance(central, dict):
        value = central.get(key, _MISSING)
        if value is not _MISSING and value is not None:
            return value

    return default


def get_brain_store_path(
    *,
    fritz_local: dict | None = None,
    cwd: str | None = None,
) -> Path:
    """Resolve the brain-owned knowledge store root (registry-free).

    Precedence (via :func:`get_setting`): project ``.fritz-local.json`` >
    central ``registry.yaml`` ``settings:`` > default ``<BRAIN_HOME>/knowledge``.
    The configured path is ``~``-expanded so the store is relocatable to any
    folder. Locating the store never requires a registry: with an absent or
    empty ``registry.yaml`` the default applies.
    """

    raw = get_setting("brain_store_path", default=None, fritz_local=fritz_local, cwd=cwd)
    if raw:
        return Path(str(raw)).expanduser()
    return BRAIN_HOME / "knowledge"


def ensure_brain_store_path(
    *,
    fritz_local: dict | None = None,
    cwd: str | None = None,
) -> Path:
    """Resolve the store root and ensure its directory exists.

    Structured sub-layout and indexes are introduced in later work items.
    """

    root = get_brain_store_path(fritz_local=fritz_local, cwd=cwd)
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_local_brain_service_config() -> dict:
    """Return optional Local Brain service config from registry settings."""

    settings = load_settings()
    config = settings.get("local_brain_service", {})
    if not isinstance(config, dict):
        config = {}

    # Backward-compatible flat key for early adopters of the PR branch.
    if "base_url" not in config and settings.get("local_brain_base_url"):
        config = {**config, "base_url": settings["local_brain_base_url"]}
    return config


def local_brain_service_configured() -> bool:
    """Return True when the registry contains an explicit service decision."""

    settings = load_settings()
    return isinstance(settings, dict) and "local_brain_service" in settings


def local_brain_service_enabled() -> bool:
    """Return True only when the human opted into service-mode brain routing."""

    return get_local_brain_service_config().get("enabled", False) is True


def local_brain_setup_suggestions_enabled() -> bool:
    """Return True when agents may suggest installing the optional service."""

    if not local_brain_service_configured():
        return False
    config = get_local_brain_service_config()
    return not local_brain_service_enabled() and config.get("suggest_setup", True) is not False


def _is_loopback_host(hostname: str | None) -> bool:
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _validated_local_brain_base_url() -> str | None:
    config = get_local_brain_service_config()
    env_url = os.environ.get("LOCAL_BRAIN_BASE_URL")
    raw_url = env_url or config.get("base_url") or "http://127.0.0.1:8765"
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None

    try:
        parsed = urlsplit(raw_url.strip())
        parsed_port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.path not in {"", "/"}:
        return None
    if not _netloc_matches_host_port(parsed.netloc, parsed.hostname, parsed_port):
        return None
    if env_url and not _is_loopback_host(parsed.hostname):
        return None
    if not _is_loopback_host(parsed.hostname) and config.get("allow_remote", False) is not True:
        return None

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _netloc_matches_host_port(netloc: str, hostname: str | None, port: int | None) -> bool:
    if not hostname:
        return False
    host_forms = {hostname, f"[{hostname}]"} if ":" in hostname else {hostname}
    if port is not None:
        host_forms.update({f"{host}:{port}" for host in list(host_forms)})
    return netloc in host_forms


def get_local_brain_base_url() -> str:
    """Return the configured Local Brain service base URL."""

    return _validated_local_brain_base_url() or "http://127.0.0.1:8765"


def get_local_brain_api_token_env() -> str:
    """Return the environment variable name that may hold the service API token."""

    config = get_local_brain_service_config()
    token_env = config.get("api_token_env", "LOCAL_BRAIN_API_TOKEN")
    if not isinstance(token_env, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", token_env):
        return "LOCAL_BRAIN_API_TOKEN"
    return token_env


def get_local_brain_api_token() -> str | None:
    """Return the configured service token without logging it."""

    registry_token = get_local_brain_service_config().get("api_token")
    if isinstance(registry_token, str) and registry_token.strip():
        return registry_token.strip()
    token = os.environ.get(get_local_brain_api_token_env())
    if token and token.strip():
        return token.strip()
    return None


def _local_brain_auth_header() -> str:
    token_env = get_local_brain_api_token_env()
    token = get_local_brain_api_token()
    if not token:
        return ""
    env_token = os.environ.get(token_env)
    if isinstance(env_token, str) and env_token.strip() == token:
        return f' -H "authorization: Bearer ${token_env}"'
    return f' -H "authorization: Bearer {_local_brain_token_command()}"'


def _local_brain_token_command() -> str:
    return (
        "$(python3 -c 'import os,pathlib,yaml; "
        "r=yaml.safe_load(pathlib.Path.home().joinpath(\".brain\",\"registry.yaml\").read_text()) or {}; "
        "c=(r.get(\"settings\") or {}).get(\"local_brain_service\") or {}; "
        "print((c.get(\"api_token\") or os.environ.get(c.get(\"api_token_env\") or \"LOCAL_BRAIN_API_TOKEN\") or \"\").strip())')"
    )


@dataclass(frozen=True)
class AutoCompileResult:
    status: str
    message: str


class ServiceCompileErrors(RuntimeError):
    pass


def auto_compile_after_capture(capture_path: Path | None = None) -> AutoCompileResult:
    """Attempt live compile after a capture is written.

    Full Fritz Local mode should not silently accumulate captures. When service
    mode is enabled, compile through the service first and fall back to the
    local in-process compiler. If no processor can run, leave durable markers so
    the next session can surface the failure.
    """

    config = get_local_brain_service_config()
    if not local_brain_service_enabled() or config.get("auto_compile_on_ingest", True) is False:
        message = "Local Brain processing is not active; capture saved for later compile."
        _write_compile_needed(capture_path, processing_active=False, reason=message)
        return AutoCompileResult(status="disabled", message=message)

    try:
        service_result = _try_service_compile()
        if service_result is not None:
            service_status, remaining = service_result
            if service_status == "running":
                _try_service_embedding_refresh_schedule()
            if service_status == "compiled":
                if remaining:
                    message = f"Compile processed a partial batch; {remaining} captures remain pending."
                    _write_compile_needed(capture_path, processing_active=True, reason=message)
                    return AutoCompileResult(status="pending", message=message)
                _clear_compile_markers()
                return AutoCompileResult(status="compiled", message="Compile triggered through Local Brain service.")
            if service_status == "running":
                message = "Compile already running; capture marked pending for the next compile pass."
                _write_compile_needed(capture_path, processing_active=True, reason=message)
                return AutoCompileResult(status="pending", message=message)
    except ServiceCompileErrors as exc:
        reason = f"Auto-compile failed: {exc}"
        _write_compile_failure(reason)
        _write_compile_needed(capture_path, processing_active=True, reason=reason)
        return AutoCompileResult(status="failed", message=reason)
    except Exception as exc:  # noqa: BLE001 - service transport errors should fall back to local compile.
        service_error = str(exc)
    else:
        service_error = "Local Brain service is not reachable."

    try:
        _run_in_process_compile()
        remaining = _pending_capture_count()
        if remaining:
            message = f"Compile processed a partial batch; {remaining} captures remain pending."
            _write_compile_needed(capture_path, processing_active=True, reason=message)
            return AutoCompileResult(status="pending", message=message)
        _clear_compile_markers()
        return AutoCompileResult(status="compiled", message="Compile completed through local in-process fallback.")
    except Exception as exc:  # noqa: BLE001 - marker records provider/import/config failures for operators.
        if _is_compile_already_running(exc):
            message = "Compile already running; capture marked pending for the next compile pass."
            _write_compile_needed(capture_path, processing_active=True, reason=message)
            return AutoCompileResult(status="pending", message=message)
        reason = f"Auto-compile failed: {service_error}; fallback failed: {exc}"
        _write_compile_failure(reason)
        _write_compile_needed(capture_path, processing_active=True, reason=reason)
        return AutoCompileResult(status="failed", message=reason)


def _is_compile_already_running(exc: Exception) -> bool:
    return exc.__class__.__name__ == "OperationAlreadyRunning" or "Compile already running" in str(exc)


def _try_service_compile(timeout: float = 30.0) -> tuple[str, int | None] | None:
    base_url = _validated_local_brain_base_url()
    if base_url is None:
        return None

    payload = json.dumps({"dry_run": False}).encode("utf-8")
    headers = {"accept": "application/json", "content-type": "application/json"}
    token = get_local_brain_api_token()
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = request.Request(f"{base_url}/v1/compile/run", data=payload, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                return None
            payload = json.loads(response.read().decode("utf-8") or "{}")
            errors = payload.get("errors", [])
            if errors:
                raise ServiceCompileErrors(f"Local Brain service compile errors: {'; '.join(str(item) for item in errors)}")
            return ("compiled", _pending_capture_count())
    except error.HTTPError as exc:
        if exc.code == 409:
            return ("running", None)
        raise
    except (TimeoutError, socket.timeout):
        return ("running", None)
    except error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return ("running", None)
        return None


def _try_service_embedding_refresh_schedule(timeout: float = 2.0) -> None:
    base_url = _validated_local_brain_base_url()
    if base_url is None:
        return

    headers = {"accept": "application/json"}
    token = get_local_brain_api_token()
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = request.Request(f"{base_url}/v1/embeddings/index/schedule", data=b"{}", headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout):
            return
    except Exception:
        return


def _should_refresh_embeddings_after_in_process_compile(settings, result: object) -> bool:
    if not getattr(settings, "embedding_enabled", False):
        return False
    if not getattr(settings, "embedding_refresh_after_compile", True):
        return False
    if getattr(result, "errors", None):
        return False
    return bool(getattr(result, "applied", None) or getattr(result, "skipped", None))


def _run_in_process_compile() -> object:
    local_brain_src = FRITZ_REPO / "services" / "local-brain"
    if local_brain_src.exists():
        sys.path.insert(0, str(local_brain_src))

    from fritz_local_brain.compile_workflow import run_compile
    from fritz_local_brain.config import Settings
    from fritz_local_brain.embeddings import refresh_embedding_index
    from fritz_local_brain.models import CompileRunRequest, EmbeddingIndexRequest
    from fritz_local_brain.operation_locks import compile_lock
    from fritz_local_brain.run_history import record_compile

    async def _compile() -> object:
        settings = Settings(LOCAL_BRAIN_HOME=BRAIN_HOME, LOCAL_BRAIN_SKILLS_DIR=FRITZ_REPO / "skills")
        async with compile_lock.guard(BRAIN_HOME):
            result = await run_compile(settings, CompileRunRequest(dry_run=False))
            record_compile(result)
            if _should_refresh_embeddings_after_in_process_compile(settings, result):
                await refresh_embedding_index(settings, EmbeddingIndexRequest(force=False))
            return result

    return asyncio.run(_compile())


def _pending_capture_count() -> int:
    local_brain_src = FRITZ_REPO / "services" / "local-brain"
    if local_brain_src.exists() and str(local_brain_src) not in sys.path:
        sys.path.insert(0, str(local_brain_src))
    try:
        from fritz_local_brain.captures import list_all_captures

        return len(list_all_captures(BRAIN_HOME).paths)
    except Exception:  # noqa: BLE001 - status marker fallback must never break capture.
        capture_parent = BRAIN_HOME / "capture"
        if capture_parent.is_symlink():
            return 0
        count = 0
        for source in ("inbox", "daily", "sessions"):
            capture_dir = capture_parent / source
            if capture_dir.is_symlink():
                continue
            count += sum(1 for path in capture_dir.glob("*.md") if path.is_file() and not path.is_symlink())
        return count


def _write_compile_needed(capture_path: Path | None, *, processing_active: bool, reason: str) -> None:
    BRAIN_HOME.mkdir(parents=True, exist_ok=True)
    marker = BRAIN_HOME / ".compile-needed"
    topics = 1
    if marker.exists():
        try:
            topics = int((json.loads(marker.read_text(encoding="utf-8")) or {}).get("topics", 0)) + 1
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            topics = 1
    data = {
        "since": datetime.now().isoformat(timespec="seconds"),
        "topics": topics,
        "capture": str(capture_path) if capture_path else None,
        "processing_active": processing_active,
        "reason": reason,
    }
    marker.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_compile_failure(reason: str) -> None:
    BRAIN_HOME.mkdir(parents=True, exist_ok=True)
    (BRAIN_HOME / ".compile-failed").write_text(
        json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "reason": reason}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _clear_compile_markers() -> None:
    for marker in (BRAIN_HOME / ".compile-failed", BRAIN_HOME / ".compile-needed"):
        try:
            marker.unlink()
        except FileNotFoundError:
            pass


def local_brain_service_available(timeout: float = 0.4) -> bool:
    """Return True when service routing is enabled and the service is reachable."""

    if not local_brain_service_enabled():
        return False

    base_url = _validated_local_brain_base_url()
    if base_url is None:
        return False

    try:
        headers = {"accept": "application/json"}
        token = get_local_brain_api_token()
        if token:
            headers["authorization"] = f"Bearer {token}"
        req = request.Request(f"{base_url}/v1/status", headers=headers, method="GET")
        with request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def local_brain_service_instructions() -> str:
    """Context block that makes service-backed brain workflows the default."""

    base_url = get_local_brain_base_url().rstrip("/")
    auth = _local_brain_auth_header()
    return (
        "## Local Brain Service Active\n\n"
        f"The Dockerized Local Brain service is reachable at `{base_url}`. "
        "For supported workflows, use this service layer first instead of duplicating the old local slash-skill workflow.\n\n"
        "Agent integration order: use registered MCP tools first when available and authorized (`brain_search`, `brain_query`, `brain_compile`, `brain_sync`, `brain_lint`), "
        "then HTTP calls from the host. The optional CLI is for installed local packages only; do not assume it is on the host PATH.\n\n"
        "Supported service-backed workflows:\n"
        f"- Search/brain check, semantic when embeddings are enabled: `curl -fsS -X POST {base_url}/v1/search/run{auth} -H 'content-type: application/json' -d '{{\"query\":\"<query>\"}}'`\n"
        f"- Exact query compatibility only, not the default brain check: `curl -fsS -X POST {base_url}/v1/query/run{auth} -H 'content-type: application/json' -d '{{\"query\":\"<query>\"}}'`\n"
        f"- Compile: `curl -fsS -X POST {base_url}/v1/compile/run{auth} -H 'content-type: application/json' -d '{{\"dry_run\":true}}'`\n"
        f"- Sync: `curl -fsS -X POST {base_url}/v1/sync/run{auth} -H 'content-type: application/json' -d '{{\"dry_run\":true}}'`\n"
        f"- Lint: `curl -fsS -X POST {base_url}/v1/lint/run{auth} -H 'content-type: application/json' -d '{{}}'`\n"
        f"- Embeddings: `curl -fsS {base_url}/v1/embeddings/status{auth}` and `curl -fsS -X POST {base_url}/v1/embeddings/probe{auth} -H 'content-type: application/json' -d '{{\"dry_run\":true}}'`\n\n"
        "Do not also run `/fritz:brain-query`, `/fritz:brain-compile`, `/fritz:brain-sync`, or `/fritz:brain-lint` "
        "for the same work unless the service is unavailable or the human explicitly requests the non-service path. "
        "Use the existing local skills only for workflows the service does not provide, such as setup, ingest, update, and writing the handover document itself."
    )


def local_brain_setup_suggestion() -> str:
    """Context block that lets agents offer the optional service without enabling it."""

    return (
        "## Optional Local Brain Service Available\n\n"
        "The Dockerized Local Brain service is not enabled in `~/.brain/registry.yaml`. "
        "Continue with the original local hook/slash-skill behavior by default. "
        "For repeated or heavier brain workflows such as compile, sync, query, lint, embeddings, MCP, or CLI automation, "
        "it is appropriate to ask the human whether they want to configure and start the optional Docker stack. "
        "Do not start Docker or change `settings.local_brain_service.enabled` without explicit human approval. "
        "If the human declines, continue with the local workflow and do not block the task."
    )


def local_brain_configuration_decision_prompt() -> str:
    """Context block for existing installs with no explicit service decision."""

    return (
        "## Local Brain Service Decision Needed\n\n"
        "`settings.local_brain_service` is absent from `~/.brain/registry.yaml`, so the optional Dockerized Local Brain service behavior is unconfigured. "
        "Before choosing service or local routing for supported brain workflows, ask the human which behavior they want and then write the chosen setting to the registry.\n\n"
        "Offer these choices:\n"
        "1. Configure and start the optional Docker Local Brain service now, then set `enabled: true`, `base_url`, `api_token` or `api_token_env`, `allow_remote`, and `suggest_setup`.\n"
        "2. Keep using the existing local slash-skill workflow and allow future setup suggestions, setting `enabled: false`, optional `api_token`, `api_token_env: LOCAL_BRAIN_API_TOKEN`, and `suggest_setup: true`.\n"
        "3. Keep using the existing local slash-skill workflow and stop future setup suggestions, setting `enabled: false`, optional `api_token`, `api_token_env: LOCAL_BRAIN_API_TOKEN`, and `suggest_setup: false`.\n\n"
        "Do not start Docker or set `enabled: true` without explicit human approval. If the human chooses local behavior, continue with the original local workflow after writing the setting."
    )


def resolve_project_vault(cwd: str) -> tuple[str | None, dict | None, Path | None, dict | None]:
    """Resolve cwd to vault using a trusted .fritz-local.json, cwd, or default.

    Returns (vault_name, vault_config, vault_path, fritz_local_config).
    fritz_local_config is the parsed .fritz-local.json only when the cwd is
    trusted to control project/context settings.
    """
    registry = load_registry()
    vaults = registry.get("vaults", {})
    cwd_resolved = Path(cwd).resolve()

    # Trust boundary: only honor any .fritz-local.json fields if cwd is within a
    # registered vault path or the fritz-ai-local repo itself. This prevents an
    # untrusted cloned repo from steering default-vault context injection.
    trusted = False
    for _, vc in vaults.items():
        vp = Path(vc["path"]).expanduser().resolve()
        try:
            cwd_resolved.relative_to(vp)
            trusted = True
            break
        except ValueError:
            continue
    if not trusted:
        try:
            cwd_resolved.relative_to(FRITZ_REPO.resolve())
            trusted = True
        except ValueError:
            pass

    fritz_local = load_fritz_local(cwd) if trusted else None

    if fritz_local and "vault" in fritz_local:
        vault_name = fritz_local["vault"]
        if vault_name in vaults:
            config = vaults[vault_name]
            vault_path = Path(config["path"]).expanduser().resolve()
            return vault_name, config, vault_path, fritz_local

    # Fallback to cwd matching.
    vault_name, vault_config, vault_path = find_vault_for_cwd(cwd)
    if vault_path:
        return vault_name, vault_config, vault_path, fritz_local

    # Finally, use only an explicitly configured default_vault. This keeps brain
    # context active for new source projects before they have a trusted
    # .fritz-local.json binding, without implicitly exposing the first active
    # vault in arbitrary directories.
    default_name = registry.get("default_vault")
    if default_name and default_name in vaults:
        vault_config = vaults[default_name]
        vault_path = Path(vault_config["path"]).expanduser().resolve()
        return default_name, vault_config, vault_path, fritz_local

    return vault_name, vault_config, vault_path, fritz_local


def get_context_injection_level(fritz_local: dict | None) -> str:
    """Determine context injection level.

    Precedence:
    1. .fritz-local.json context_injection field
    2. Global settings.context_injection in registry.yaml
    3. Default: "off"

    If .fritz-local.json exists but has no context_injection → "off"
    If no .fritz-local.json → "off" (today's behavior)

    This delegates the value lookup to :func:`get_setting` but keeps a stricter
    edge-case rule: when a ``.fritz-local.json`` IS present, its (possibly
    invalid/absent) value is authoritative and never falls through to the
    central layer — a present-but-uninjected project is "off". Only when no
    project file is present do we consult the central ``settings:`` value.
    """
    valid = ("off", "light", "full")
    if fritz_local is not None:
        # Project file present: its value is authoritative, no central fallthrough.
        level = fritz_local.get("context_injection")
        return level if level in valid else "off"

    # No .fritz-local.json: resolve through the shared path (central → default).
    level = get_setting("context_injection", "off")
    return level if level in valid else "off"


def get_max_injection_chars(fritz_local: dict | None) -> int:
    """Get max injection chars. Project overrides global.

    Resolves through the shared :func:`get_setting` path
    (project ``.fritz-local.json`` > central ``settings:`` > default 8000),
    then coerces the resolved value to ``int`` to preserve the historical
    return type.
    """
    return int(get_setting("max_injection_chars", 8000, fritz_local=fritz_local))


def get_reconciliation_autonomy(
    *,
    fritz_local: dict | None = None,
    cwd: str | None = None,
) -> str:
    """Resolve the reconciliation autonomy mode (project > central > default).

    Default is ``"apply"`` (matches the service ``Settings.reconciliation_autonomy``
    default). Normalises to lowercase; invalid values fall back to ``"apply"``.
    """
    raw = get_setting("reconciliation_autonomy", default="apply", fritz_local=fritz_local, cwd=cwd)
    value = str(raw).strip().lower() if raw is not None else "apply"
    return value if value in {"apply", "propose"} else "apply"


def get_bulk_supersession_threshold(
    *,
    fritz_local: dict | None = None,
    cwd: str | None = None,
) -> int:
    """Resolve the bulk-supersession approval threshold (project > central > default).

    Default is ``5`` (matches the service default). Coerced to ``int``; floored
    at ``1`` so a bad value never disables the gate entirely.
    """
    raw = get_setting("bulk_supersession_threshold", default=5, fritz_local=fritz_local, cwd=cwd)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 5
    return max(1, value)


def get_merge_policy(
    *,
    fritz_local: dict | None = None,
    cwd: str | None = None,
) -> str:
    """Resolve the retrieval-synthesis merge policy (project > central > default).

    Default is ``"brain-first"`` (brain knowledge is authoritative; external
    mirror matches only fill remaining gaps).  Normalises to lowercase; invalid
    values fall back to ``"brain-first"``.
    """
    raw = get_setting("merge_policy", default="brain-first", fritz_local=fritz_local, cwd=cwd)
    value = str(raw).strip().lower() if raw is not None else "brain-first"
    return value if value in {"brain-first", "peer-ranked"} else "brain-first"


def get_query_scope_default(
    *,
    fritz_local: dict | None = None,
    cwd: str | None = None,
) -> str:
    """Resolve the default query scope (project > central > default).

    Default is ``"active"`` (matches ``QueryRunRequest.scope`` default).
    Normalises to lowercase; invalid values fall back to ``"active"``.

    Valid scopes: ``active``, ``include_archive``, ``all``.
    """
    raw = get_setting("query_scope_default", default="active", fritz_local=fritz_local, cwd=cwd)
    value = str(raw).strip().lower() if raw is not None else "active"
    return value if value in {"active", "include_archive", "all"} else "active"


# Note: mirror mode is PER-TARGET in the registry ``external_targets:`` block —
# it is NOT a global setting and therefore has no global resolver here.


def get_fritz_version() -> str | None:
    """Read VERSION from the fritz-ai-local repo."""
    version_path = FRITZ_REPO / "VERSION"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return None
