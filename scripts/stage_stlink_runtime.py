#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PACKAGES = [
    "openocd",
    "stlink-tools",
    "libcapstone4",
    "libftdi1-2",
    "libgpiod2t64",
    "libhidapi-hidraw0",
    "libjaylink0",
    "libjim0.82t64",
    "libstlink1",
]


class StageStlinkError(RuntimeError):
    pass


def run(command: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise StageStlinkError(f"Command failed with code {result.returncode}: {' '.join(command)}")


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise StageStlinkError(f"Required tool not found: {name}")


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file() and not source.is_symlink():
        raise StageStlinkError(f"Required file missing from extracted packages: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)


def copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise StageStlinkError(f"Required directory missing from extracted packages: {source}")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)


def stage(repo_root: Path) -> Path:
    if sys.platform != "linux":
        raise StageStlinkError("ST-Link runtime staging currently supports Linux only.")

    runtime_dir = repo_root / "runtime" / "linux-x64"
    bin_dir = runtime_dir / "bin"
    lib_dir = runtime_dir / "lib"
    share_dir = runtime_dir / "share"
    rules_dir = lib_dir / "udev" / "rules.d"

    require_tool("apt")
    require_tool("dpkg-deb")

    with tempfile.TemporaryDirectory(prefix="stlink-runtime-") as temp:
        temp_dir = Path(temp)
        extracted = temp_dir / "extracted"
        run(["apt", "download", *PACKAGES], cwd=temp_dir)
        for package_path in temp_dir.glob("*.deb"):
            run(["dpkg-deb", "-x", str(package_path), str(extracted)])

        for name in ("openocd", "st-info", "st-flash", "st-util", "st-trace"):
            copy_file(extracted / "usr" / "bin" / name, bin_dir / name)
            (bin_dir / name).chmod(0o755)

        copy_tree(extracted / "usr" / "share" / "openocd" / "scripts", share_dir / "openocd" / "scripts")
        copy_tree(extracted / "usr" / "share" / "stlink" / "chips", share_dir / "stlink" / "chips")

        package_lib_dir = extracted / "usr" / "lib" / "x86_64-linux-gnu"
        for source in package_lib_dir.glob("*.so*"):
            copy_file(source, lib_dir / source.name)

        rules_dir.mkdir(parents=True, exist_ok=True)
        for source in (extracted / "usr" / "lib" / "udev" / "rules.d").glob("*.rules"):
            copy_file(source, rules_dir / source.name)

    return runtime_dir


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    try:
        runtime_dir = stage(repo_root)
    except StageStlinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"ST-Link/OpenOCD runtime staged at {runtime_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
