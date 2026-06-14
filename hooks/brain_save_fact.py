#!/usr/bin/env python3
"""Save a durable fact into Fritz-Brain capture/inbox.

Behavior-identical port of the Pi extension's ``writeBrainInboxFact`` and the
``brain_save_fact`` tool (see ``bindings/pi/index.ts``). Available both as an
importable function (:func:`save_fact`) and as a CLI that reads a fact as JSON
from stdin or accepts ``--title``/``--body``/... flags.

Brain root resolution: the ``BRAIN_HOME`` environment variable wins, falling
back to ``~/.brain``. Callers (and tests) may also pass an explicit
``brain_home`` so the live ``~/.brain`` is never touched.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def brain_home() -> Path:
    """Resolve the brain root: ``$BRAIN_HOME`` if set, else ``~/.brain``."""
    env = os.environ.get("BRAIN_HOME")
    if env and env.strip():
        return Path(env.strip())
    return Path.home() / ".brain"


def today_str() -> str:
    # UTC to mirror the role model's ``new Date().toISOString().slice(0, 10)``
    # in ``bindings/pi/index.ts`` (used for the inbox filename + ``created:``).
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def timestamp() -> str:
    # Local time to mirror the role model's ``timestamp()`` (getFullYear/
    # getHours/...); used only for the ``log.md`` audit line.
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def slugify(value: str) -> str:
    """Mirror the TS ``slugify``: lowercase, non-alnum runs -> ``-``, trim, cap 80."""
    slug = value.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"^-+|-+$", "", slug)
    slug = slug[:80]
    return slug or "brain-fact"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _write_private_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _append_private_file(path: Path, content: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(content)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def ensure_brain_capture_dirs(root: Path) -> None:
    _ensure_private_dir(root)
    _ensure_private_dir(root / "capture")
    _ensure_private_dir(root / "capture" / "inbox")


def _json_quote(value: str) -> str:
    """Match JS ``JSON.stringify`` for a string (used for YAML scalar quoting)."""
    return json.dumps(value, ensure_ascii=False)


def save_fact(
    title: str,
    body: str,
    source: str | None = None,
    sensitive: bool = False,
    tags: list[str] | None = None,
    agent: str = "pi",
    root: Path | None = None,
) -> Path:
    """Write a fact to ``capture/inbox`` and append a ``log.md`` audit line.

    Returns the path to the written inbox file. Output is byte-identical to the
    Pi extension's ``writeBrainInboxFact``.
    """
    if root is None:
        root = brain_home()
    ensure_brain_capture_dirs(root)

    inbox = root / "capture" / "inbox"
    file = inbox / f"{today_str()}-{slugify(title)}.md"

    source_line = _json_quote(source) if source else "pi-session"
    frontmatter = "\n".join(
        [
            "---",
            "type: capture",
            f"title: {_json_quote(title)}",
            "domain: work",
            "sources:",
            f"  - {source_line}",
            f"created: {today_str()}",
            f"agent_last_edit: {agent or 'pi'}",
            f"sensitive: {'true' if sensitive else 'false'}",
            "---",
            "",
        ]
    )

    if tags:
        rendered = " ".join(f"#{re.sub(r'^#', '', t)}" for t in tags)
        tags_suffix = f"\n\nTags: {rendered}\n"
    else:
        tags_suffix = "\n"

    _write_private_file(file, f"{frontmatter}# {title}\n\n{body.strip()}{tags_suffix}")
    _append_private_file(
        root / "log.md",
        f'{timestamp()} | INGEST | pi-extension | Auto-saved "{title}" to {file}\n',
    )
    return file


def _load_cli_fact(argv: list[str]) -> dict:
    parser = argparse.ArgumentParser(
        description="Save a durable fact into Fritz-Brain capture/inbox."
    )
    parser.add_argument("--title")
    parser.add_argument("--body")
    parser.add_argument("--source")
    parser.add_argument("--sensitive", action="store_true")
    parser.add_argument(
        "--tags", help="Comma-separated tags without leading #"
    )
    parser.add_argument("--agent", default="pi")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Read the fact as a JSON object from stdin",
    )
    args = parser.parse_args(argv)

    # Read JSON from stdin when requested, or when no --title was provided and
    # stdin is not a TTY (piped input).
    use_stdin = args.json or (args.title is None and not sys.stdin.isatty())
    if use_stdin:
        raw = sys.stdin.read().strip()
        if raw:
            data = json.loads(raw)
        else:
            data = {}
    else:
        data = {}

    title = args.title if args.title is not None else data.get("title")
    body = args.body if args.body is not None else data.get("body")
    if title is None or body is None:
        parser.error("a fact requires both 'title' and 'body'")

    tags = data.get("tags")
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    return {
        "title": title,
        "body": body,
        "source": args.source if args.source is not None else data.get("source"),
        "sensitive": bool(args.sensitive or data.get("sensitive", False)),
        "tags": tags,
        "agent": args.agent if args.agent != "pi" else data.get("agent", "pi"),
    }


def main(argv: list[str] | None = None) -> int:
    fact = _load_cli_fact(sys.argv[1:] if argv is None else argv)
    file = save_fact(**fact)
    print(f"Saved to Fritz-Brain: {file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
