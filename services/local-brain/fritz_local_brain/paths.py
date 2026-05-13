"""Host/container path mapping helpers."""

from __future__ import annotations

from pathlib import Path


class PathMapper:
    """Translate registry host paths into container paths."""

    def __init__(self, mapping_spec: str = "") -> None:
        self._mappings: list[tuple[Path, Path]] = []
        for raw in mapping_spec.split(","):
            item = raw.strip()
            if not item or "=" not in item:
                continue
            host, container = item.split("=", 1)
            self._mappings.append((Path(host).expanduser().resolve(), Path(container).resolve()))
        self._mappings.sort(key=lambda mapping: len(mapping[0].parts), reverse=True)

    def to_container(self, path: str | Path) -> Path:
        raw_path = str(path)
        if raw_path.startswith("~/"):
            home_relative = Path(raw_path[2:])
            if ".." in home_relative.parts:
                return Path(path).expanduser().absolute()
            for host_root, container_root in self._mappings:
                host_home_relative = _host_root_home_relative(host_root)
                if host_home_relative is None:
                    continue
                try:
                    rel = home_relative.relative_to(host_home_relative)
                except ValueError:
                    continue
                return container_root / rel

        expanded = Path(path).expanduser()
        try:
            resolved = expanded.resolve()
        except FileNotFoundError:
            resolved = expanded.absolute()

        for host_root, container_root in self._mappings:
            try:
                rel = resolved.relative_to(host_root)
            except ValueError:
                continue
            return container_root / rel

        return resolved


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _host_root_home_relative(host_root: Path) -> Path | None:
    parts = host_root.parts
    if len(parts) >= 4 and parts[1] in {"Users", "home"}:
        return Path(*parts[3:])
    return None
