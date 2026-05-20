from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

from .catalog import DriverXaiCatalog, DriverXaiError


SUPPORTED_BUNDLE_LANGUAGES = {"micropython"}


def prepare_bundle(
    module_ids: list[str],
    output_dir: str,
    *,
    catalog_root: str | None = None,
    language: str = "micropython",
    parameters: dict[str, Any] | None = None,
    include_examples: bool = False,
) -> dict[str, Any]:
    normalized_language = language.strip().lower()
    if normalized_language not in SUPPORTED_BUNDLE_LANGUAGES:
        raise DriverXaiError(f"Unsupported Driver xAI bundle language: {language}")
    if not module_ids:
        raise DriverXaiError("At least one Driver xAI module is required")

    catalog = DriverXaiCatalog(catalog_root)
    output_root = Path(output_dir).expanduser().resolve()
    parameter_overrides = parameters or {}

    make_mini = _catalog_make_mini(catalog.root)
    if make_mini is not None:
        result = make_mini(
            module_ids,
            str(output_root),
            parameters=parameter_overrides,
            include_examples=include_examples,
        )
        return {
            "ok": bool(result.get("ok")),
            "catalogRoot": str(catalog.root),
            "outputDir": str(output_root),
            "modules": result.get("modules", module_ids),
            "language": normalized_language,
            "files": result.get("files", []),
            "entrypoint": result.get("entrypoint", "driver_xai.py"),
            "nextAction": "Deploy this generated bundle with driver_xai_deploy_bundle or micropython_sync_project.",
        }

    written: list[str] = []
    lock_modules: dict[str, Any] = {}
    config_modules: dict[str, Any] = {}

    lib_modules = output_root / "lib" / "modules"
    lib_modules.mkdir(parents=True, exist_ok=True)
    _write_text(lib_modules / "__init__.py", "# Generated Driver xAI modules package.\n", written)
    _copy_file(catalog.root / "modules" / "base.py", lib_modules / "base.py", written)

    for module_id in module_ids:
        info = catalog.info(module_id)
        module_dir = catalog.module_dir(module_id)
        source_driver = module_dir / "drivers" / "micropython.py"
        if not source_driver.is_file():
            raise DriverXaiError(f"MicroPython driver not found for module: {module_id}")

        target_module = lib_modules / module_id
        target_drivers = target_module / "drivers"
        target_drivers.mkdir(parents=True, exist_ok=True)
        _write_text(target_module / "__init__.py", f"# Generated {module_id} package.\n", written)
        _write_text(target_drivers / "__init__.py", f"# Generated {module_id} drivers package.\n", written)
        _copy_file(source_driver, target_drivers / "micropython.py", written)

        if include_examples:
            examples_dir = module_dir / "examples"
            if examples_dir.is_dir():
                for example in sorted(path for path in examples_dir.rglob("*") if path.is_file()):
                    _copy_file(example, output_root / "examples" / module_id / example.relative_to(examples_dir), written)

        config_modules[module_id] = parameter_overrides.get(module_id, {})
        lock_modules[module_id] = {
            "module_id": module_id,
            "version": info.get("version"),
            "driver": "micropython",
            "source": str(module_dir),
        }

    config = {
        "schema_version": "1.0",
        "language": normalized_language,
        "modules": config_modules,
    }
    lock = {
        "schema_version": "1.0",
        "catalog_root": str(catalog.root),
        "modules": lock_modules,
    }

    _write_json(output_root / "driver_xai_config.json", config, written)
    _write_text(output_root / "driver_xai_config.py", f"CONFIG = {config!r}\n", written)
    _write_json(output_root / "driver_xai.lock.json", lock, written)
    _write_text(output_root / "main.py", _default_main_source(module_ids), written)

    return {
        "ok": True,
        "catalogRoot": str(catalog.root),
        "outputDir": str(output_root),
        "modules": module_ids,
        "language": normalized_language,
        "files": written,
        "nextAction": "Deploy this generated bundle with driver_xai_deploy_bundle or micropython_sync_project.",
    }


def _copy_file(source: Path, target: Path, written: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    written.append(str(target))


def _write_json(path: Path, data: dict[str, Any], written: list[str]) -> None:
    _write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", written)


def _write_text(path: Path, text: str, written: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    written.append(str(path))


def _default_main_source(module_ids: list[str]) -> str:
    module_lines = "\n".join(f"    print({module_id!r})" for module_id in module_ids)
    return "\n".join([
        "from driver_xai_config import CONFIG",
        "",
        "",
        "print('Driver xAI bundle ready')",
        "print('modules:')",
        module_lines or "    pass",
        "",
        "# Import drivers from modules.<module_id>.drivers.micropython.",
        "# Instantiate them with board-specific pins, buses, and runtime parameters.",
        "",
    ])


def _catalog_make_mini(catalog_root: Path) -> Any | None:
    main_path = catalog_root / "main.py"
    if not main_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("driver_xai_catalog_main", main_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    make_mini = getattr(module, "make_mini", None)
    return make_mini if callable(make_mini) else None
