from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_CONTEXT_FILE = "project.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_dir(project_folder: str) -> Path:
    path = Path(project_folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Project folder does not exist: {path}")
    return path


def _context_path(project_folder: str) -> Path:
    return _project_dir(project_folder) / PROJECT_CONTEXT_FILE


def default_context(project_folder: str, framework: str | None = None) -> dict[str, Any]:
    project_dir = _project_dir(project_folder)
    timestamp = _now()
    return {
        "schema_version": "1.0",
        "name": project_dir.name,
        "framework": framework or "",
        "created_at": timestamp,
        "updated_at": timestamp,
        "board": {},
        "modules": [],
        "notes": [],
    }


def read(project_folder: str, *, create_if_missing: bool = False, framework: str | None = None) -> dict[str, Any]:
    path = _context_path(project_folder)
    created = False
    if not path.is_file():
        if not create_if_missing:
            return {
                "ok": False,
                "projectFolder": str(path.parent),
                "path": str(path),
                "exists": False,
                "error": f"Project context not found: {path}",
            }
        context = default_context(str(path.parent), framework)
        _write_context_file(path, context)
        created = True
    else:
        context = _read_context_file(path)

    return {
        "ok": True,
        "projectFolder": str(path.parent),
        "path": str(path),
        "exists": True,
        "created": created,
        "context": context,
    }


def update(
    project_folder: str,
    patch: dict[str, Any],
    *,
    replace: bool = False,
    create_if_missing: bool = True,
    framework: str | None = None,
) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")

    path = _context_path(project_folder)
    if replace:
        context = copy.deepcopy(patch)
        context.setdefault("schema_version", "1.0")
        context.setdefault("name", path.parent.name)
        context.setdefault("framework", framework or "")
        context.setdefault("created_at", _now())
    else:
        existing = read(str(path.parent), create_if_missing=create_if_missing, framework=framework)
        if not existing.get("ok"):
            return existing
        context = _deep_merge(existing["context"], patch)

    context["updated_at"] = _now()
    _write_context_file(path, context)
    return {
        "ok": True,
        "projectFolder": str(path.parent),
        "path": str(path),
        "context": context,
    }


def _read_context_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Project context must be a JSON object: {path}")
    return data


def _write_context_file(path: Path, context: dict[str, Any]) -> None:
    path.write_text(json.dumps(context, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


__all__ = [name for name, value in globals().items() if getattr(value, "__module__", None) == __name__]
