from __future__ import annotations

import os
import platform
from pathlib import Path
import shutil
import subprocess
from typing import Any


DEFAULT_STLINK_USB_IDS = ("0483:3748", "0483:374b", "0483:374f", "0483:3752", "0483:3753")
DEFAULT_INTERFACE_CONFIG = "interface/stlink.cfg"
DEFAULT_FLASH_ADDRESS = "0x08000000"

STM_TARGET_CONFIGS: dict[str, str] = {
    "stm32f0": "target/stm32f0x.cfg",
    "stm32f1": "target/stm32f1x.cfg",
    "stm32f2": "target/stm32f2x.cfg",
    "stm32f3": "target/stm32f3x.cfg",
    "stm32f4": "target/stm32f4x.cfg",
    "stm32f7": "target/stm32f7x.cfg",
    "stm32g0": "target/stm32g0x.cfg",
    "stm32g4": "target/stm32g4x.cfg",
    "stm32h7": "target/stm32h7x.cfg",
    "stm32l0": "target/stm32l0.cfg",
    "stm32l1": "target/stm32l1.cfg",
    "stm32l4": "target/stm32l4x.cfg",
    "stm32l5": "target/stm32l5x.cfg",
    "stm32u5": "target/stm32u5x.cfg",
    "stm32wb": "target/stm32wbx.cfg",
    "stm32wl": "target/stm32wlx.cfg",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _runtime_platform_key() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        if machine in {"x86_64", "amd64"}:
            return "linux-x64"
        if machine in {"aarch64", "arm64"}:
            return "linux-arm64"
    if system == "windows":
        if machine in {"amd64", "x86_64"}:
            return "win32-x64"
        if machine in {"arm64", "aarch64"}:
            return "win32-arm64"
    return None


def _bundled_runtime_root() -> Path | None:
    explicit = os.environ.get("MICROPYTHON_RUNTIME_ROOT")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_dir():
            return path

    platform_key = _runtime_platform_key()
    if not platform_key:
        return None

    path = _repo_root() / "runtime" / platform_key
    return path if path.is_dir() else None


def _bundled_tool_path(name: str) -> str | None:
    runtime_root = _bundled_runtime_root()
    if runtime_root is None:
        return None

    candidates = [runtime_root / "bin" / name]
    if platform.system().lower() == "windows" and not name.lower().endswith(".exe"):
        candidates.append(runtime_root / "bin" / f"{name}.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _runtime_root_from_tool(tool_path: str | None) -> Path | None:
    if not tool_path:
        return None
    path = Path(tool_path).resolve()
    if path.parent.name != "bin":
        return None
    return path.parent.parent


def _openocd_scripts_dir(openocd_path: str | None = None) -> str | None:
    explicit = os.environ.get("MICROPYTHON_OPENOCD_SCRIPTS")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_dir():
            return str(path)

    runtime_root = _runtime_root_from_tool(openocd_path)
    candidates: list[Path] = []
    if runtime_root is not None:
        candidates.extend([
            runtime_root / "share" / "openocd" / "scripts",
            runtime_root / "scripts",
        ])

    candidates.extend([
        Path("/usr/share/openocd/scripts"),
        Path("/usr/local/share/openocd/scripts"),
    ])

    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return None


def _stlink_share_dir(tool_path: str | None = None) -> str | None:
    runtime_root = _runtime_root_from_tool(tool_path)
    candidates: list[Path] = []
    if runtime_root is not None:
        candidates.append(runtime_root / "share" / "stlink")
    candidates.append(Path("/usr/share/stlink"))

    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return None


def _which(name: str) -> str | None:
    bundled = _bundled_tool_path(name)
    if bundled:
        return bundled
    found = shutil.which(name)
    return str(Path(found).resolve()) if found else None


def _runtime_env_for_tool(tool_path: str | None) -> dict[str, str] | None:
    runtime_root = _runtime_root_from_tool(tool_path)
    if runtime_root is None:
        runtime_root = _bundled_runtime_root()
    if runtime_root is None:
        return None

    env = os.environ.copy()
    bin_dir = runtime_root / "bin"
    if bin_dir.is_dir():
        env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])

    lib_dir = runtime_root / "lib"
    if platform.system().lower() == "linux" and lib_dir.is_dir():
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [str(lib_dir), env.get("LD_LIBRARY_PATH", "")]
        )
    return env


def _run_process(command: list[str], timeout_seconds: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
            env=_runtime_env_for_tool(command[0]),
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "command": command,
            "returnCode": None,
            "stdout": "",
            "stderr": "",
            "error": f"Tool not found: {command[0]}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": command,
            "returnCode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"Command timed out after {timeout_seconds:g}s.",
        }

    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "returnCode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "error": None if completed.returncode == 0 else stderr or stdout or f"Command failed with code {completed.returncode}.",
    }


def normalize_target_config(target: str | None) -> str:
    value = (target or "").strip().lower()
    if not value:
        raise ValueError("target is required, for example stm32f4 or target/stm32f4x.cfg.")
    if value in STM_TARGET_CONFIGS:
        return STM_TARGET_CONFIGS[value]
    if value.startswith("target/") and value.endswith(".cfg"):
        return value
    if value.endswith(".cfg") and "/" not in value:
        return f"target/{value}"
    raise ValueError(
        "Unsupported STM target. Pass one of "
        f"{', '.join(sorted(STM_TARGET_CONFIGS))} or an OpenOCD target/*.cfg path."
    )


def stlink_status(timeout_seconds: float = 10.0) -> dict[str, Any]:
    openocd = _which("openocd")
    st_info = _which("st-info")
    st_flash = _which("st-flash")
    scripts_dir = _openocd_scripts_dir(openocd)
    stlink_share = _stlink_share_dir(st_info or st_flash)

    usb_devices: list[str] = []
    lsusb = _which("lsusb")
    if lsusb:
        result = _run_process([lsusb], timeout_seconds)
        if result.get("stdout"):
            for line in str(result["stdout"]).splitlines():
                if any(usb_id in line.lower() for usb_id in DEFAULT_STLINK_USB_IDS):
                    usb_devices.append(line.strip())

    probe: dict[str, Any] | None = None
    if st_info:
        probe = _run_process([st_info, "--probe"], timeout_seconds)

    return {
        "ok": bool(openocd or st_info or st_flash),
        "tools": {
            "openocd": openocd,
            "stInfo": st_info,
            "stFlash": st_flash,
            "openocdScripts": scripts_dir,
            "stlinkShare": stlink_share,
        },
        "stlinkUsbDevices": usb_devices,
        "probe": probe,
        "guidance": (
            "Bundle OpenOCD/stlink tools under runtime/<platform>/bin and OpenOCD scripts under "
            "runtime/<platform>/share/openocd/scripts, or install them on the host PATH."
        ),
    }


def openocd_command(
    target: str,
    commands: list[str],
    *,
    interface: str = DEFAULT_INTERFACE_CONFIG,
    openocd_path: str | None = None,
    scripts_dir: str | None = None,
) -> list[str]:
    openocd = openocd_path or _which("openocd")
    if not openocd:
        raise FileNotFoundError("openocd was not found in the bundled runtime or host PATH.")

    resolved_scripts = scripts_dir or _openocd_scripts_dir(openocd)
    target_config = normalize_target_config(target)
    command = [openocd]
    if resolved_scripts:
        command.extend(["-s", resolved_scripts])
    command.extend(["-f", interface, "-f", target_config])
    for item in commands:
        command.extend(["-c", item])
    return command


def erase_chip(target: str, timeout_seconds: float = 60.0, interface: str = DEFAULT_INTERFACE_CONFIG) -> dict[str, Any]:
    try:
        command = openocd_command(target, ["init", "reset halt", "flash erase_sector 0 0 last", "reset run", "shutdown"], interface=interface)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "command": None}
    result = _run_process(command, timeout_seconds)
    return {
        **result,
        "target": target,
        "operation": "erase",
        "note": "Uses OpenOCD flash bank 0 sector erase across all sectors.",
    }


def flash_firmware(
    target: str,
    firmware_path: str,
    *,
    verify: bool = True,
    reset: bool = True,
    timeout_seconds: float = 120.0,
    interface: str = DEFAULT_INTERFACE_CONFIG,
    address: str = DEFAULT_FLASH_ADDRESS,
) -> dict[str, Any]:
    firmware = Path(firmware_path).expanduser().resolve()
    if not firmware.is_file():
        return {
            "ok": False,
            "target": target,
            "firmware": str(firmware),
            "operation": "flash",
            "command": None,
            "error": f"Firmware file does not exist: {firmware}",
        }

    suffix = firmware.suffix.lower()
    if suffix in {".elf", ".hex"}:
        program_arg = f'program "{firmware}"'
    else:
        program_arg = f'program "{firmware}" {address}'
    if verify:
        program_arg += " verify"
    if reset:
        program_arg += " reset"

    try:
        command = openocd_command(target, [program_arg, "shutdown"], interface=interface)
    except Exception as exc:
        return {
            "ok": False,
            "target": target,
            "firmware": str(firmware),
            "operation": "flash",
            "command": None,
            "error": str(exc),
        }

    result = _run_process(command, timeout_seconds)
    return {
        **result,
        "target": target,
        "firmware": str(firmware),
        "operation": "flash",
        "verify": verify,
        "reset": reset,
    }


__all__ = [name for name, value in globals().items() if getattr(value, "__module__", None) == __name__]
