"""Small REST CLI for Local Brain."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import urlsplit, urlunsplit

import yaml


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TOKEN_ENV = "LOCAL_BRAIN_API_TOKEN"


@dataclass(frozen=True)
class Connection:
    base_url: str
    token: str | None


def main() -> None:
    parser = argparse.ArgumentParser(prog="fritz-local-brain-cli")
    parser.add_argument("--base-url", default=None, help="Override registry service URL")
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-env", default=None, help="Environment variable containing the API token")
    parser.add_argument("--allow-remote", action="store_true", help="Allow non-loopback service URLs")
    parser.add_argument("--registry", type=Path, default=Path.home() / ".brain" / "registry.yaml")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("status")

    compile_parser = subcommands.add_parser("compile")
    compile_parser.add_argument("--apply", action="store_true")
    compile_parser.add_argument("--max-captures", type=int, default=None)
    compile_parser.add_argument("--approval-token", default=None)

    sync_parser = subcommands.add_parser("sync")
    sync_parser.add_argument("--apply", action="store_true")
    sync_parser.add_argument("--vault", default=None)
    sync_parser.add_argument("--approval-token", default=None)

    runs_parser = subcommands.add_parser("recent-runs")
    runs_parser.add_argument("--limit", type=int, default=10)

    query_parser = subcommands.add_parser("query")
    query_parser.add_argument("query")
    query_parser.add_argument("--vault", default=None)
    query_parser.add_argument("--limit", type=int, default=10)

    lint_parser = subcommands.add_parser("lint")
    lint_parser.add_argument("--apply-log", action="store_true")
    lint_parser.add_argument("--vault", default=None)

    args = parser.parse_args()
    print(json.dumps(_dispatch(args), indent=2, sort_keys=True))


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "status":
        return _request(args, "GET", "/v1/status")
    if args.command == "compile":
        return _request(
            args,
            "POST",
            "/v1/compile/run",
            {"dry_run": not args.apply, "max_captures": args.max_captures, "approval_token": args.approval_token},
        )
    if args.command == "sync":
        return _request(
            args,
            "POST",
            "/v1/sync/run",
            {"dry_run": not args.apply, "vault": args.vault, "approval_token": args.approval_token},
        )
    if args.command == "recent-runs":
        return _request(args, "GET", f"/v1/runs/recent?limit={args.limit}")
    if args.command == "query":
        return _request(args, "POST", "/v1/query/run", {"query": args.query, "vault": args.vault, "limit": args.limit})
    if args.command == "lint":
        return _request(args, "POST", "/v1/lint/run", {"dry_run": not args.apply_log, "vault": args.vault})
    raise SystemExit(f"Unsupported command: {args.command}")


def _request(args: argparse.Namespace, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    connection = resolve_connection(args)
    data = None
    headers = {"accept": "application/json"}
    if body is not None:
        clean_body = {key: value for key, value in body.items() if value is not None}
        data = json.dumps(clean_body).encode("utf-8")
        headers["content-type"] = "application/json"
    if connection.token:
        headers["authorization"] = f"Bearer {connection.token}"

    req = request.Request(f"{connection.base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def resolve_connection(args: argparse.Namespace) -> Connection:
    """Resolve service URL/token from explicit args, env, then registry."""

    service_config = _load_service_config(getattr(args, "registry", None))
    raw_base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("LOCAL_BRAIN_BASE_URL")
        or service_config.get("base_url")
        or DEFAULT_BASE_URL
    )
    allow_remote = getattr(args, "allow_remote", False) or service_config.get("allow_remote", False) is True
    base_url = _validated_base_url(str(raw_base_url), allow_remote)
    token_env = getattr(args, "token_env", None) or service_config.get("api_token_env") or DEFAULT_TOKEN_ENV
    token = getattr(args, "token", None) or os.environ.get(token_env) or None
    return Connection(base_url=base_url, token=token.strip() if isinstance(token, str) and token.strip() else None)


def _load_service_config(registry_path: Path | None) -> dict[str, Any]:
    if registry_path is None or not registry_path.exists():
        return {}
    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    settings = registry.get("settings", {}) if isinstance(registry, dict) else {}
    config = settings.get("local_brain_service", {}) if isinstance(settings, dict) else {}
    return config if isinstance(config, dict) else {}


def _validated_base_url(raw_url: str, allow_remote: bool) -> str:
    try:
        parsed = urlsplit(raw_url.strip())
        parsed_port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"Invalid Local Brain base URL: {raw_url}") from exc

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(f"Invalid Local Brain base URL: {raw_url}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise SystemExit(f"Invalid Local Brain base URL: {raw_url}")

    hostname = parsed.hostname
    if not hostname:
        raise SystemExit(f"Invalid Local Brain base URL: {raw_url}")
    if not _netloc_matches_host_port(parsed.netloc, hostname, parsed_port):
        raise SystemExit(f"Invalid Local Brain base URL: {raw_url}")
    if hostname not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
        raise SystemExit("Remote Local Brain URL is not allowed without --allow-remote or allow_remote: true")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _netloc_matches_host_port(netloc: str, hostname: str, port: int | None) -> bool:
    host_forms = {hostname, f"[{hostname}]"} if ":" in hostname else {hostname}
    if port is not None:
        host_forms.update({f"{host}:{port}" for host in list(host_forms)})
    return netloc in host_forms


if __name__ == "__main__":
    main()
