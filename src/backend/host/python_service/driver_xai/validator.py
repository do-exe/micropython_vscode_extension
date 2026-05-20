from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import DriverXaiCatalog, DriverXaiError


REQUIRED_SCHEMAS = {
    "info.schema.json",
    "parameters.schema.json",
    "commands.schema.json",
    "registry.schema.json",
}


def validate_catalog(catalog_root: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    modules: list[str] = []
    registries: list[str] = []

    try:
        catalog = DriverXaiCatalog(catalog_root)
    except Exception as exc:
        return {
            "ok": False,
            "catalogRoot": catalog_root,
            "modules": modules,
            "registries": registries,
            "errors": [str(exc)],
            "warnings": warnings,
        }

    schemas_dir = catalog.root / "schemas"
    missing_schemas = sorted(REQUIRED_SCHEMAS - {path.name for path in schemas_dir.glob("*.schema.json")})
    for schema in missing_schemas:
        warnings.append(f"missing schema: schemas/{schema}")

    for module_id in catalog.modules():
        module_dir = catalog.module_dir(module_id)
        try:
            _validate_module(catalog, module_dir)
            modules.append(module_id)
        except Exception as exc:
            errors.append(f"{module_id}: {exc}")

    for registry_type, root in (("protocol", catalog.protocols_dir), ("interface", catalog.interfaces_dir)):
        for registry_path in sorted(root.glob("*/registry.json")):
            try:
                data = catalog.registry(registry_type, registry_path.parent.name)
                _validate_registry_members(catalog, data)
                registries.append(f"{registry_type}:{registry_path.parent.name}")
            except Exception as exc:
                errors.append(f"{registry_type}:{registry_path.parent.name}: {exc}")

    return {
        "ok": not errors,
        "catalogRoot": str(catalog.root),
        "modules": modules,
        "registries": registries,
        "errors": errors,
        "warnings": warnings,
    }


def _validate_module(catalog: DriverXaiCatalog, module_dir: Path) -> None:
    module_id = module_dir.name
    info = catalog.info(module_id)
    parameters = catalog.get(module_id, False, None, "parameters", "json", "all")
    commands = catalog.get(module_id, False, None, "commands", "json", "all")

    if parameters.get("module_id") != module_id:
        raise DriverXaiError("parameters.module_id must match folder name")
    if info.get("interface") != parameters.get("interface"):
        raise DriverXaiError("info.interface must match parameters.interface")
    if info.get("protocol") != parameters.get("protocol"):
        raise DriverXaiError("info.protocol must match parameters.protocol")
    if info.get("interface") and info.get("protocol"):
        raise DriverXaiError("module cannot declare both interface and protocol")

    drivers_dir = module_dir / "drivers"
    if not (drivers_dir / "micropython.py").is_file():
        raise DriverXaiError("drivers/micropython.py is required")

    command_names: set[str] = set()
    for command in commands.get("commands", []):
        name = command.get("name")
        if not name:
            raise DriverXaiError("command.name is required")
        if name in command_names:
            raise DriverXaiError(f"duplicate command: {name}")
        command_names.add(str(name))
        inputs = command.get("inputs")
        if not isinstance(inputs, (dict, list)):
            raise DriverXaiError(f"command inputs must be an object or list: {name}")
        if "output" not in command:
            raise DriverXaiError(f"command output is required: {name}")


def _validate_registry_members(catalog: DriverXaiCatalog, registry_data: dict[str, Any]) -> None:
    registry_type = registry_data["type"]
    registry_id = registry_data["id"]
    for module_ref in registry_data.get("modules", []):
        module_id = module_ref["id"]
        module_info = catalog.info(module_id)
        if module_info.get(registry_type) != registry_id:
            raise DriverXaiError(f"{module_id} does not declare {registry_type}={registry_id}")
