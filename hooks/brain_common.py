"""Shared utilities for brain hooks across all agents."""

import json
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path
from urllib import request
from urllib.parse import urlsplit, urlunsplit

import yaml


BRAIN_HOME = Path.home() / ".brain"
REGISTRY_PATH = BRAIN_HOME / "registry.yaml"


def load_registry() -> dict:
    """Load the vault registry."""
    if not REGISTRY_PATH.exists():
        return {"version": 1, "vaults": {}}
    with open(REGISTRY_PATH) as f:
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
    with open(manifest_path) as f:
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
    with open(log_path, "a") as f:
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
FRITZ_REPO = Path.home() / ".fritz-ai-local"


def load_fritz_local(cwd: str) -> dict | None:
    """Walk up from cwd looking for .fritz-local.json. Return parsed JSON or None."""
    current = Path(cwd).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / FRITZ_LOCAL_FILENAME
        if candidate.exists():
            try:
                with open(candidate) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
    return None


def load_settings() -> dict:
    """Load global settings from registry.yaml. Returns empty dict if none."""
    registry = load_registry()
    return registry.get("settings", {})


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
    return f" -H {shlex.quote(f'authorization: Bearer {token}')}"


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
        "Agent integration order: use registered MCP tools first when available and authorized (`brain_query`, `brain_compile`, `brain_sync`, `brain_lint`), "
        "then HTTP calls from the host. The optional CLI is for installed local packages only; do not assume it is on the host PATH.\n\n"
        "Supported service-backed workflows:\n"
        f"- Query: `curl -fsS -X POST {base_url}/v1/query/run{auth} -H 'content-type: application/json' -d '{{\"query\":\"<query>\"}}'`\n"
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
    """Resolve cwd to vault using .fritz-local.json first, then cwd matching.

    Returns (vault_name, vault_config, vault_path, fritz_local_config).
    fritz_local_config is the parsed .fritz-local.json or None.
    """
    fritz_local = load_fritz_local(cwd)

    if fritz_local and "vault" in fritz_local:
        registry = load_registry()
        vault_name = fritz_local["vault"]
        vaults = registry.get("vaults", {})
        if vault_name in vaults:
            config = vaults[vault_name]
            vault_path = Path(config["path"]).expanduser().resolve()
            # Trust boundary: only honor .fritz-local.json if cwd is within
            # a registered vault path or the fritz-ai-local repo itself.
            # This prevents an untrusted cloned repo from redirecting hooks
            # into personal vaults.
            cwd_resolved = Path(cwd).resolve()
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
                # Check if cwd is within the fritz-ai-local repo
                try:
                    cwd_resolved.relative_to(FRITZ_REPO.resolve())
                    trusted = True
                except ValueError:
                    pass
            if trusted:
                return vault_name, config, vault_path, fritz_local
            # Untrusted location — ignore .fritz-local.json, fall through

    # Fallback to cwd matching
    vault_name, vault_config, vault_path = find_vault_for_cwd(cwd)
    return vault_name, vault_config, vault_path, fritz_local


def get_context_injection_level(fritz_local: dict | None) -> str:
    """Determine context injection level.

    Precedence:
    1. .fritz-local.json context_injection field
    2. Global settings.context_injection in registry.yaml
    3. Default: "off"

    If .fritz-local.json exists but has no context_injection → "off"
    If no .fritz-local.json → "off" (today's behavior)
    """
    if fritz_local is not None:
        level = fritz_local.get("context_injection")
        if level in ("off", "light", "full"):
            return level
        # .fritz-local.json exists but no context_injection → off
        return "off"

    # No .fritz-local.json: check global settings
    settings = load_settings()
    level = settings.get("context_injection")
    if level in ("off", "light", "full"):
        return level

    return "off"


def get_max_injection_chars(fritz_local: dict | None) -> int:
    """Get max injection chars. Project overrides global."""
    if fritz_local and "max_injection_chars" in fritz_local:
        return int(fritz_local["max_injection_chars"])
    settings = load_settings()
    return int(settings.get("max_injection_chars", 8000))


def get_fritz_version() -> str | None:
    """Read VERSION from the fritz-ai-local repo."""
    version_path = FRITZ_REPO / "VERSION"
    if version_path.exists():
        return version_path.read_text().strip()
    return None
