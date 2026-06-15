from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolchainDefinition:
    platform: str
    name: str
    root_parts: tuple[str, ...]
    check_parts: tuple[str, ...]
    install_script: str
    removable: bool = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _runtime_platform_key() -> str:
    if sys.platform.startswith("linux"):
        return "linux-x64"
    if sys.platform == "win32":
        return "win32-x64"
    if sys.platform == "darwin":
        return "darwin-x64"
    return sys.platform


def _definitions() -> dict[str, ToolchainDefinition]:
    runtime_key = _runtime_platform_key()
    return {
        "arduino": ToolchainDefinition(
            platform="arduino",
            name="Arduino CLI",
            root_parts=("toolchain", "arduino"),
            check_parts=("toolchain", "arduino", "bin", "arduino-cli"),
            install_script="install_arduino_toolchain.py",
        ),
        "espidf": ToolchainDefinition(
            platform="espidf",
            name="ESP-IDF",
            root_parts=("toolchain", "esp-idf"),
            check_parts=("toolchain", "esp-idf", "export.sh"),
            install_script="install_esp_idf_toolchain.py",
        ),
        "stm-arm": ToolchainDefinition(
            platform="stm-arm",
            name="STM ARM GCC",
            root_parts=("toolchain", "arm-none-eabi"),
            check_parts=("toolchain", "arm-none-eabi", "bin", "arm-none-eabi-gcc"),
            install_script="install_arm_toolchain.py",
        ),
        "stm-stlink": ToolchainDefinition(
            platform="stm-stlink",
            name="ST-Link Runtime",
            root_parts=("runtime", runtime_key),
            check_parts=("runtime", runtime_key, "bin", "openocd"),
            install_script="stage_stlink_runtime.py",
        ),
    }


def _definition(platform: str) -> ToolchainDefinition:
    key = platform.strip().lower()
    definitions = _definitions()
    if key not in definitions:
        allowed = ", ".join(sorted(definitions))
        raise ValueError(f"Unknown toolchain platform: {platform}. Expected one of: {allowed}")
    return definitions[key]


def _path(parts: tuple[str, ...]) -> Path:
    return _repo_root().joinpath(*parts)


def list_status() -> dict[str, Any]:
    items = [status(platform) for platform in sorted(_definitions())]
    return {
        "ok": True,
        "toolchains": items,
    }


def status(platform: str) -> dict[str, Any]:
    definition = _definition(platform)
    root = _path(definition.root_parts)
    check_path = _path(definition.check_parts)
    installed = check_path.exists()
    return {
        "ok": True,
        "platform": definition.platform,
        "name": definition.name,
        "installed": installed,
        "root": str(root),
        "checkPath": str(check_path),
        "state": "ok" if installed else "missing",
        "action": "open_folder" if installed else "install",
    }


def install(platform: str, timeout_seconds: float = 1800.0) -> dict[str, Any]:
    definition = _definition(platform)
    script = _repo_root() / "scripts" / definition.install_script
    if not script.is_file():
        return {
            "ok": False,
            "platform": definition.platform,
            "name": definition.name,
            "error": f"Installer script not found: {script}",
        }
    result = _run([sys.executable, str(script)], timeout_seconds=timeout_seconds)
    return {
        **result,
        "platform": definition.platform,
        "name": definition.name,
        "operation": "install",
        "status": status(definition.platform),
    }


def update(platform: str, timeout_seconds: float = 1800.0) -> dict[str, Any]:
    result = install(platform, timeout_seconds=timeout_seconds)
    return {
        **result,
        "operation": "update",
    }


def remove(platform: str) -> dict[str, Any]:
    definition = _definition(platform)
    root = _path(definition.root_parts)
    if not definition.removable:
        return {
            "ok": False,
            "platform": definition.platform,
            "name": definition.name,
            "root": str(root),
            "error": "This toolchain is not removable through the backend.",
        }
    if not root.exists():
        return {
            "ok": True,
            "platform": definition.platform,
            "name": definition.name,
            "root": str(root),
            "removed": False,
            "status": status(definition.platform),
        }
    shutil.rmtree(root)
    return {
        "ok": True,
        "platform": definition.platform,
        "name": definition.name,
        "root": str(root),
        "removed": True,
        "status": status(definition.platform),
    }


def open_folder(platform: str) -> dict[str, Any]:
    definition = _definition(platform)
    root = _path(definition.root_parts)
    return {
        "ok": True,
        "platform": definition.platform,
        "name": definition.name,
        "root": str(root),
        "exists": root.exists(),
    }


def _run(command: list[str], timeout_seconds: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(_repo_root()),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": command,
            "returnCode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"Command timed out after {timeout_seconds} seconds.",
        }
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "error": None if completed.returncode == 0 else f"Command failed with exit code {completed.returncode}.",
    }
