"""Small REST CLI for Local Brain."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib import request


def main() -> None:
    parser = argparse.ArgumentParser(prog="fritz-local-brain-cli")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--token", default=None)
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
    data = None
    headers = {"accept": "application/json"}
    if body is not None:
        clean_body = {key: value for key, value in body.items() if value is not None}
        data = json.dumps(clean_body).encode("utf-8")
        headers["content-type"] = "application/json"
    if args.token:
        headers["authorization"] = f"Bearer {args.token}"

    req = request.Request(f"{args.base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
