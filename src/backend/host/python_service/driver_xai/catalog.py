from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


MODULE_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
JSON_SOURCES = {"info", "parameters", "commands"}


class DriverXaiError(ValueError):
    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def resolve_catalog_root(catalog_root: str | os.PathLike[str] | None = None) -> Path:
    if catalog_root:
        return _require_catalog_root(Path(catalog_root).expanduser().resolve())

    env_root = os.environ.get("DRIVER_XAI_CATALOG_ROOT")
    if env_root:
        return _require_catalog_root(Path(env_root).expanduser().resolve())

    for parent in Path(__file__).resolve().parents:
        for candidate in (parent / "vendor" / "driver_xAI", parent / "driver_xAI"):
            if (candidate / "modules").is_dir():
                return _require_catalog_root(candidate)

    raise DriverXaiError(
        "Driver xAI catalog not found. Add vendor/driver_xAI or set DRIVER_XAI_CATALOG_ROOT."
    )


def _require_catalog_root(path: Path) -> Path:
    if not (path / "modules").is_dir():
        raise DriverXaiError(f"Driver xAI modules directory not found: {path / 'modules'}")
    if not (path / "protocols").is_dir():
        raise DriverXaiError(f"Driver xAI protocols directory not found: {path / 'protocols'}")
    if not (path / "interfaces").is_dir():
        raise DriverXaiError(f"Driver xAI interfaces directory not found: {path / 'interfaces'}")
    return path


class DriverXaiCatalog:
    def __init__(self, catalog_root: str | os.PathLike[str] | None = None) -> None:
        self.root = resolve_catalog_root(catalog_root)
        self.modules_dir = self.root / "modules"
        self.protocols_dir = self.root / "protocols"
        self.interfaces_dir = self.root / "interfaces"

    def search(self, module_id_or_module_name: str) -> list[dict[str, Any]]:
        query = self._normalize_query(module_id_or_module_name)
        matches: dict[str, dict[str, Any]] = {}

        for registry_data in self.all_registries():
            for module_ref in registry_data.get("modules", []):
                module_id = str(module_ref.get("id", ""))
                module_name = str(module_ref.get("name", ""))
                module_info = self.info(module_id)
                haystack = {
                    module_id.lower(),
                    module_name.lower(),
                    str(module_info.get("summary", "")).lower(),
                }
                if query not in module_id.lower() and query not in module_name.lower() and not any(query in item for item in haystack):
                    continue
                matches[module_id] = {
                    "module_id": module_id,
                    "module_name": module_name,
                    "source_type": registry_data["type"],
                    "source_id": registry_data["id"],
                    "interface": module_info.get("interface"),
                    "protocol": module_info.get("protocol"),
                    "version": module_info.get("version"),
                    "summary": module_info.get("summary", ""),
                    "verified": bool(module_info.get("verified", False)),
                }

        return [matches[key] for key in sorted(matches)]

    def registry(self, registry_type: str, name: str | None = None) -> list[dict[str, Any]] | dict[str, Any]:
        normalized_type = self._normalize_registry_type(registry_type)
        root = self.protocols_dir if normalized_type == "protocol" else self.interfaces_dir
        if name is None:
            return [self._read_registry(path) for path in sorted(root.glob("*/registry.json"))]
        return self._read_registry(root / name / "registry.json")

    def all_registries(self) -> list[dict[str, Any]]:
        return [*self.registry("protocol"), *self.registry("interface")]

    def modules(self) -> list[str]:
        return sorted(path.name for path in self.modules_dir.iterdir() if path.is_dir())

    def info(self, module_id: str) -> dict[str, Any]:
        module_dir = self._module_dir(module_id)
        data = self._read_json(module_dir / "info.json")
        self._validate_module_identity(module_dir.name, data)
        return data

    def inspect(
        self,
        module_id: str,
        has_variant: bool = False,
        variant_name: str | None = None,
    ) -> dict[str, Any]:
        module_dir = self._module_variant_dir(module_id, has_variant, variant_name)
        sources = sorted(path.name for path in module_dir.iterdir() if path.is_file())
        drivers_dir = module_dir / "drivers"
        examples_dir = module_dir / "examples"
        tests_dir = module_dir / "tests"
        drivers = sorted(path.name for path in drivers_dir.iterdir() if path.is_file()) if drivers_dir.exists() else []
        examples = sorted(path.relative_to(examples_dir).as_posix() for path in examples_dir.rglob("*") if path.is_file()) if examples_dir.exists() else []
        tests = sorted(path.relative_to(tests_dir).as_posix() for path in tests_dir.rglob("*") if path.is_file()) if tests_dir.exists() else []
        info_data = self.info(module_id)
        parameters = self._read_json(module_dir / "parameters.json")
        commands = self._read_json(module_dir / "commands.json").get("commands", [])

        return {
            "module_id": module_id,
            "catalog_root": str(self.root),
            "sources": sources,
            "drivers": drivers,
            "examples": examples,
            "tests": tests,
            "get_keys": {
                "info": sorted(info_data.keys()),
                "parameters": sorted(parameters.keys()),
                "commands": [command["name"] for command in commands],
            },
            "set_keys": [],
            "commands": [command["name"] for command in commands],
            "read_only": True,
        }

    def get(
        self,
        module_id: str,
        has_variant: bool,
        variant_name: str | None,
        source: str,
        file_type: str,
        key: str = "all",
    ) -> Any:
        module_dir = self._module_variant_dir(module_id, has_variant, variant_name)
        normalized_type = str(file_type).strip().lower()
        if normalized_type == "json":
            source_name = self._json_source_name(source)
            data = self._read_json(module_dir / source_name)
            return data if key == "all" else data[key]
        if normalized_type == "driver":
            driver_name = self._driver_source_name(module_dir, source)
            return (module_dir / "drivers" / driver_name).read_text(encoding="utf-8")
        raise DriverXaiError("file_type must be json or driver")

    def module_dir(self, module_id: str) -> Path:
        return self._module_dir(module_id)

    def _module_dir(self, module_id: str) -> Path:
        module_id = self._normalize_module_id(module_id)
        path = self.modules_dir / module_id
        if not path.is_dir():
            raise DriverXaiError(f"Driver xAI module not found: {module_id}")
        return path

    def _module_variant_dir(self, module_id: str, has_variant: bool, variant_name: str | None) -> Path:
        module_dir = self._module_dir(module_id)
        if not has_variant:
            return module_dir
        if not variant_name:
            raise DriverXaiError("variant_name is required when has_variant is true")
        variant_dir = module_dir / "variants" / variant_name
        if not variant_dir.is_dir():
            raise DriverXaiError(f"Driver xAI variant not found: {module_id}/{variant_name}")
        return variant_dir

    def _read_registry(self, path: Path) -> dict[str, Any]:
        data = self._read_json(path)
        if data.get("id") != path.parent.name or data.get("name") != path.parent.name:
            raise DriverXaiError(f"registry id/name must match folder name: {path}")
        if data.get("type") not in {"protocol", "interface"}:
            raise DriverXaiError(f"registry type must be protocol or interface: {path}")
        for module_ref in data.get("modules", []):
            module_id = module_ref.get("id")
            if module_id != module_ref.get("name"):
                raise DriverXaiError(f"registry module id/name mismatch: {path}")
            self.info(str(module_id))
        return data

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise DriverXaiError(f"Driver xAI file not found: {path}") from exc
        if not isinstance(data, dict):
            raise DriverXaiError(f"Driver xAI JSON file must contain an object: {path}")
        return data

    def _json_source_name(self, source: str) -> str:
        normalized = str(source).strip()
        normalized = normalized[:-5] if normalized.endswith(".json") else normalized
        if normalized not in JSON_SOURCES:
            raise DriverXaiError(f"JSON source must be one of {sorted(JSON_SOURCES)}")
        return f"{normalized}.json"

    def _driver_source_name(self, module_dir: Path, source: str) -> str:
        normalized = str(source).strip()
        drivers_dir = module_dir / "drivers"
        available = sorted(path.name for path in drivers_dir.iterdir() if path.is_file()) if drivers_dir.is_dir() else []
        aliases = {
            "": "micropython.py",
            "default": "micropython.py",
            "driver": "micropython.py",
            "micropython": "micropython.py",
            "py": "micropython.py",
            "c": "c.c",
            "rust": "rust.rs",
            "rs": "rust.rs",
        }
        driver_name = aliases.get(normalized.lower(), normalized)
        if driver_name in available:
            return driver_name
        if normalized == "" and "micropython.py" in available:
            return "micropython.py"

        suggestion = "micropython.py" if "micropython.py" in available else (available[0] if available else None)
        message = (
            f"Unknown driver source {normalized!r} for module {module_dir.name}. "
            f"Available driver sources: {', '.join(available) or 'none'}."
        )
        if suggestion:
            message = f"{message} Did you mean {suggestion!r}?"
        raise DriverXaiError(
            message,
            code="unknown_source",
            details={
                "requestedSource": normalized,
                "availableSources": available,
                "suggestedSource": suggestion,
            },
        )

    def _normalize_query(self, value: str) -> str:
        query = str(value).strip().lower()
        if not query:
            raise DriverXaiError("search query is required")
        return query

    def _normalize_module_id(self, value: str) -> str:
        module_id = str(value).strip()
        if not MODULE_ID_PATTERN.match(module_id):
            raise DriverXaiError("module_id must use lowercase letters, numbers, and underscores")
        return module_id

    def _normalize_registry_type(self, value: str) -> str:
        registry_type = str(value).strip().lower()
        if registry_type not in {"protocol", "interface"}:
            raise DriverXaiError("registry type must be protocol or interface")
        return registry_type

    def _validate_module_identity(self, module_id: str, data: dict[str, Any]) -> None:
        if data.get("module_id") != module_id or data.get("module_name") != module_id:
            raise DriverXaiError("folder name, module_id, and module_name must be the same")
