from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from . import stlink


STM_BUILD_TARGETS: dict[str, dict[str, str]] = {
    "stm32f0": {
        "cpu": "cortex-m0",
        "openocdTarget": "stm32f0",
    },
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _toolchain_bin_dir(toolchain_path: str | None = None) -> Path:
    if toolchain_path:
        candidate = Path(toolchain_path).expanduser().resolve()
    elif os.environ.get("STM_TOOLCHAIN_PATH"):
        candidate = Path(str(os.environ["STM_TOOLCHAIN_PATH"])).expanduser().resolve()
    else:
        candidate = _repo_root() / "toolchain" / "arm-none-eabi" / "bin"

    if candidate.name != "bin" and (candidate / "bin").is_dir():
        candidate = candidate / "bin"
    return candidate


def _tool(bin_dir: Path, name: str) -> str:
    path = bin_dir / f"arm-none-eabi-{name}"
    if path.is_file():
        return str(path)
    found = shutil.which(f"arm-none-eabi-{name}")
    if found:
        return found
    raise FileNotFoundError(f"arm-none-eabi-{name} not found. Run npm run install-arm-toolchain.")


def _run(command: list[str], cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "returnCode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "error": None if completed.returncode == 0 else completed.stderr.strip() or completed.stdout.strip(),
    }


def _target_config(target: str) -> dict[str, str]:
    normalized = target.strip().lower()
    config = STM_BUILD_TARGETS.get(normalized)
    if config is None:
        raise ValueError(f"Unsupported STM build target: {target}. Supported: {', '.join(sorted(STM_BUILD_TARGETS))}.")
    return config


def build_firmware(
    project_folder: str,
    *,
    target: str = "stm32f0",
    output_dir: str | None = None,
    toolchain_path: str | None = None,
    clean: bool = True,
    optimization: str = "-Os",
) -> dict[str, Any]:
    project = Path(project_folder).expanduser().resolve()
    if not project.is_dir():
        return {"ok": False, "projectFolder": str(project), "error": f"Project folder does not exist: {project}"}

    try:
        config = _target_config(target)
        bin_dir = _toolchain_bin_dir(toolchain_path)
        gcc = _tool(bin_dir, "gcc")
        objcopy = _tool(bin_dir, "objcopy")
        size = _tool(bin_dir, "size")
    except Exception as exc:
        return {"ok": False, "projectFolder": str(project), "target": target, "error": str(exc)}

    linker = project / "linker.ld"
    if not linker.is_file():
        return {"ok": False, "projectFolder": str(project), "target": target, "error": f"Missing linker script: {linker}"}

    src_dir = project / "src"
    sources = sorted([*src_dir.glob("*.c"), *src_dir.glob("*.s"), *src_dir.glob("*.S")])
    if not sources:
        return {"ok": False, "projectFolder": str(project), "target": target, "error": f"No sources found in {src_dir}"}

    build_dir = Path(output_dir).expanduser().resolve() if output_dir else project / "build"
    if clean and build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    name = project.name
    elf = build_dir / f"{name}.elf"
    binary = build_dir / f"{name}.bin"
    map_file = build_dir / f"{name}.map"

    common_flags = [
        f"-mcpu={config['cpu']}",
        "-mthumb",
        optimization,
        "-g3",
        "-ffreestanding",
        "-fdata-sections",
        "-ffunction-sections",
        "-Wall",
        "-Wextra",
        "-I",
        str(project / "include"),
    ]
    command = [
        gcc,
        *common_flags,
        *[str(source) for source in sources],
        "-nostdlib",
        "-Wl,--gc-sections",
        f"-Wl,-Map={map_file}",
        "-T",
        str(linker),
        "-o",
        str(elf),
    ]

    steps: list[dict[str, Any]] = []
    compile_result = _run(command, cwd=project)
    steps.append({"step": "compileAndLink", **compile_result})
    if not compile_result["ok"]:
        return {
            "ok": False,
            "projectFolder": str(project),
            "target": target,
            "buildDir": str(build_dir),
            "steps": steps,
            "error": compile_result["error"],
        }

    objcopy_result = _run([objcopy, "-O", "binary", str(elf), str(binary)], cwd=project)
    steps.append({"step": "objcopyBin", **objcopy_result})
    if not objcopy_result["ok"]:
        return {
            "ok": False,
            "projectFolder": str(project),
            "target": target,
            "buildDir": str(build_dir),
            "elf": str(elf),
            "steps": steps,
            "error": objcopy_result["error"],
        }

    size_result = _run([size, str(elf)], cwd=project)
    steps.append({"step": "size", **size_result})

    return {
        "ok": True,
        "projectFolder": str(project),
        "target": target,
        "openocdTarget": config["openocdTarget"],
        "toolchainBin": str(bin_dir),
        "buildDir": str(build_dir),
        "elf": str(elf),
        "bin": str(binary),
        "map": str(map_file),
        "size": size_result.get("stdout", ""),
        "steps": steps,
    }


def build_and_flash(
    project_folder: str,
    *,
    target: str = "stm32f0",
    output_dir: str | None = None,
    toolchain_path: str | None = None,
    verify: bool = True,
    reset: bool = True,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    build = build_firmware(
        project_folder,
        target=target,
        output_dir=output_dir,
        toolchain_path=toolchain_path,
    )
    if not build.get("ok"):
        return {"ok": False, "build": build, "failedStep": "build", "error": build.get("error")}

    flash = stlink.flash_firmware(
        target=str(build["openocdTarget"]),
        firmware_path=str(build["bin"]),
        verify=verify,
        reset=reset,
        timeout_seconds=timeout_seconds,
    )
    return {
        "ok": bool(flash.get("ok")),
        "build": build,
        "flash": flash,
        "failedStep": None if flash.get("ok") else "flash",
        "error": flash.get("error"),
    }


__all__ = [name for name, value in globals().items() if getattr(value, "__module__", None) == __name__]
