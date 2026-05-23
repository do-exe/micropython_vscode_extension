from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _idf_path(idf_path: str | None = None) -> Path:
    if idf_path:
        return Path(idf_path).expanduser().resolve()
    if os.environ.get("ESP_IDF_PATH"):
        return Path(str(os.environ["ESP_IDF_PATH"])).expanduser().resolve()
    return _repo_root() / "toolchain" / "esp-idf"


def _tools_path(tools_path: str | None = None) -> Path:
    if tools_path:
        return Path(tools_path).expanduser().resolve()
    if os.environ.get("IDF_TOOLS_PATH"):
        return Path(str(os.environ["IDF_TOOLS_PATH"])).expanduser().resolve()
    return _repo_root() / "toolchain" / "espressif"


def _run_idf(
    args: list[str],
    *,
    project_folder: str | None = None,
    idf_path: str | None = None,
    tools_path: str | None = None,
    timeout_seconds: float = 900.0,
) -> dict[str, Any]:
    idf = _idf_path(idf_path)
    tools = _tools_path(tools_path)
    cwd = Path(project_folder).expanduser().resolve() if project_folder else _repo_root()
    if not idf.is_dir():
        return {"ok": False, "error": f"ESP-IDF path does not exist: {idf}", "idfPath": str(idf), "toolsPath": str(tools)}
    if not tools.is_dir():
        return {"ok": False, "error": f"ESP-IDF tools path does not exist: {tools}", "idfPath": str(idf), "toolsPath": str(tools)}
    if not cwd.is_dir():
        return {"ok": False, "error": f"Project folder does not exist: {cwd}", "projectFolder": str(cwd)}

    env = os.environ.copy()
    env["IDF_TOOLS_PATH"] = str(tools)
    script = f'source "{idf / "export.sh"}" >/tmp/micropython_extension_idf_export.log && idf.py ' + " ".join(
        _shell_quote(arg) for arg in args
    )
    try:
        completed = subprocess.run(["bash", "-lc", script], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "idfPath": str(idf),
            "toolsPath": str(tools),
            "projectFolder": str(cwd),
            "returnCode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"Command timed out after {timeout_seconds:g}s.",
        }

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    return {
        "ok": completed.returncode == 0,
        "idfPath": str(idf),
        "toolsPath": str(tools),
        "projectFolder": str(cwd),
        "args": args,
        "returnCode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "error": None if completed.returncode == 0 else stderr or stdout or f"idf.py failed with code {completed.returncode}.",
    }


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def status(idf_path: str | None = None, tools_path: str | None = None) -> dict[str, Any]:
    result = _run_idf(["--version"], idf_path=idf_path, tools_path=tools_path, timeout_seconds=120.0)
    return {
        **result,
        "installed": bool(result.get("ok")),
        "guidance": "Run npm run install-esp-idf-toolchain to copy ESP-IDF into toolchain/esp-idf and tools into toolchain/espressif.",
    }


def set_target(project_folder: str, target: str = "esp32", **kwargs: Any) -> dict[str, Any]:
    return _run_idf(["set-target", target], project_folder=project_folder, **kwargs)


def build(project_folder: str, **kwargs: Any) -> dict[str, Any]:
    return _run_idf(["build"], project_folder=project_folder, **kwargs)


def flash(project_folder: str, port: str, **kwargs: Any) -> dict[str, Any]:
    return _run_idf(["-p", port, "flash"], project_folder=project_folder, **kwargs)


def build_and_flash(project_folder: str, port: str, target: str | None = None, **kwargs: Any) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if target:
        target_result = set_target(project_folder, target, **kwargs)
        steps.append({"step": "setTarget", **target_result})
        if not target_result.get("ok"):
            return {"ok": False, "failedStep": "setTarget", "steps": steps, "error": target_result.get("error")}

    build_result = build(project_folder, **kwargs)
    steps.append({"step": "build", **build_result})
    if not build_result.get("ok"):
        return {"ok": False, "failedStep": "build", "steps": steps, "error": build_result.get("error")}

    flash_result = flash(project_folder, port, **kwargs)
    steps.append({"step": "flash", **flash_result})
    return {
        "ok": bool(flash_result.get("ok")),
        "failedStep": None if flash_result.get("ok") else "flash",
        "steps": steps,
        "error": flash_result.get("error"),
    }


__all__ = [name for name, value in globals().items() if getattr(value, "__module__", None) == __name__]
