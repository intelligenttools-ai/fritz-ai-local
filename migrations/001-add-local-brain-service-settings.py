#!/usr/bin/env python3
"""Report missing Local Brain service settings for existing registries."""

from __future__ import annotations

from pathlib import Path

import yaml


REGISTRY_PATH = Path.home() / ".brain" / "registry.yaml"


def main() -> None:
    if not REGISTRY_PATH.exists():
        print("No registry found; skipped Local Brain service settings migration.")
        return

    text = REGISTRY_PATH.read_text(encoding="utf-8")
    registry = yaml.safe_load(text) or {}
    if not isinstance(registry, dict):
        print("Registry is not a YAML mapping; skipped Local Brain service settings migration.")
        return

    settings = registry.get("settings")
    if settings is not None and not isinstance(settings, dict):
        print("Registry settings is not a YAML mapping; skipped Local Brain service settings migration.")
        return
    if isinstance(settings, dict) and "local_brain_service" in settings:
        print("Local Brain service settings already present; no changes made.")
        return

    print(
        "Local Brain service behavior is unconfigured. Ask the human whether to enable the optional Docker service, keep local workflows with suggestions, or keep local workflows without suggestions; then write settings.local_brain_service accordingly."
    )


if __name__ == "__main__":
    main()
