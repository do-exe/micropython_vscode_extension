from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _toolchain_root(toolchain_path: str | None = None) -> Path:
    if toolchain_path:
        return Path(toolchain_path).expanduser().resolve()
    if os.environ.get("ARDUINO_TOOLCHAIN_PATH"):
        return Path(str(os.environ["ARDUINO_TOOLCHAIN_PATH"])).expanduser().resolve()
    return _repo_root() / "toolchain" / "arduino"


def _cli_path(toolchain_path: str | None = None) -> Path:
    root = _toolchain_root(toolchain_path)
    direct = root / "arduino-cli"
    if direct.is_file():
        return direct
    return root / "bin" / "arduino-cli"


def _config_path(toolchain_path: str | None = None) -> Path:
    return _toolchain_root(toolchain_path) / "arduino-cli.yaml"


def _run(command: list[str], timeout_seconds: float = 600.0) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, check=False, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": command,
            "returnCode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"Command timed out after {timeout_seconds:g}s.",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "command": command,
            "returnCode": None,
            "stdout": "",
            "stderr": "",
            "error": f"Tool not found: {command[0]}",
        }

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    return {
        "ok": result.returncode == 0,
        "command": command,
        "returnCode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "error": None if result.returncode == 0 else stderr or stdout or f"Command failed with code {result.returncode}.",
    }


def _base_command(toolchain_path: str | None = None) -> list[str]:
    cli = _cli_path(toolchain_path)
    config = _config_path(toolchain_path)
    command = [str(cli)]
    if config.is_file():
        command.extend(["--config-file", str(config)])
    return command


def status(toolchain_path: str | None = None) -> dict[str, Any]:
    root = _toolchain_root(toolchain_path)
    cli = _cli_path(toolchain_path)
    config = _config_path(toolchain_path)
    installed = cli.is_file()
    version = _run([str(cli), "version"], timeout_seconds=20.0) if installed else None
    cores = _run([*_base_command(toolchain_path), "core", "list"], timeout_seconds=60.0) if installed else None
    return {
        "ok": installed,
        "toolchainRoot": str(root),
        "arduinoCli": str(cli),
        "config": str(config),
        "installed": installed,
        "version": version,
        "cores": cores,
        "guidance": "Run npm run install-arduino-toolchain to install Arduino CLI into toolchain/arduino.",
    }


def install_core(fqbn_package: str, toolchain_path: str | None = None, timeout_seconds: float = 600.0) -> dict[str, Any]:
    return _run([*_base_command(toolchain_path), "core", "install", fqbn_package], timeout_seconds)


def install_library(library_name: str, toolchain_path: str | None = None, timeout_seconds: float = 600.0) -> dict[str, Any]:
    result = _run([*_base_command(toolchain_path), "lib", "install", library_name], timeout_seconds)
    return {**result, "library": library_name, "operation": "install-library"}


def search_libraries(query: str, toolchain_path: str | None = None, timeout_seconds: float = 120.0) -> dict[str, Any]:
    result = _run([*_base_command(toolchain_path), "lib", "search", query], timeout_seconds)
    return {**result, "query": query, "operation": "search-libraries"}


def list_libraries(toolchain_path: str | None = None, timeout_seconds: float = 120.0) -> dict[str, Any]:
    result = _run([*_base_command(toolchain_path), "lib", "list"], timeout_seconds)
    return {**result, "operation": "list-libraries"}


def compile_project(
    project_folder: str,
    fqbn: str,
    *,
    toolchain_path: str | None = None,
    output_dir: str | None = None,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    project = Path(project_folder).expanduser().resolve()
    if not project.is_dir():
        return {"ok": False, "projectFolder": str(project), "error": f"Project folder does not exist: {project}"}

    command = [*_base_command(toolchain_path), "compile", "--fqbn", fqbn]
    if output_dir:
        command.extend(["--output-dir", str(Path(output_dir).expanduser().resolve())])
    command.append(str(project))
    result = _run(command, timeout_seconds)
    return {**result, "projectFolder": str(project), "fqbn": fqbn, "operation": "compile"}


def upload_project(
    project_folder: str,
    fqbn: str,
    port: str,
    *,
    toolchain_path: str | None = None,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    project = Path(project_folder).expanduser().resolve()
    if not project.is_dir():
        return {"ok": False, "projectFolder": str(project), "error": f"Project folder does not exist: {project}"}

    result = _run([*_base_command(toolchain_path), "upload", "-p", port, "--fqbn", fqbn, str(project)], timeout_seconds)
    return {**result, "projectFolder": str(project), "fqbn": fqbn, "port": port, "operation": "upload"}


def compile_and_upload(
    project_folder: str,
    fqbn: str,
    port: str,
    *,
    toolchain_path: str | None = None,
    output_dir: str | None = None,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    compile_result = compile_project(
        project_folder,
        fqbn,
        toolchain_path=toolchain_path,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
    )
    if not compile_result.get("ok"):
        return {"ok": False, "failedStep": "compile", "compile": compile_result, "error": compile_result.get("error")}

    upload_result = upload_project(project_folder, fqbn, port, toolchain_path=toolchain_path, timeout_seconds=timeout_seconds)
    return {
        "ok": bool(upload_result.get("ok")),
        "failedStep": None if upload_result.get("ok") else "upload",
        "compile": compile_result,
        "upload": upload_result,
        "error": upload_result.get("error"),
    }


__all__ = [name for name, value in globals().items() if getattr(value, "__module__", None) == __name__]
