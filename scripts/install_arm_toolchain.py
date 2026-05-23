#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BASE_PACKAGES = [
    "binutils-arm-none-eabi",
    "gcc-arm-none-eabi",
    "libnewlib-arm-none-eabi",
]

CXX_PACKAGES = [
    "libstdc++-arm-none-eabi-newlib",
]


class ToolchainInstallError(RuntimeError):
    pass


def run(command: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise ToolchainInstallError(f"Command failed with code {result.returncode}: {' '.join(command)}")


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise ToolchainInstallError(f"Required tool not found: {name}")


def copy_tree_contents(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        destination = target / entry.name
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        if entry.is_dir():
            shutil.copytree(entry, destination, symlinks=True)
        else:
            shutil.copy2(entry, destination, follow_symlinks=False)


def install(repo_root: Path, include_cxx: bool = False) -> Path:
    if sys.platform != "linux":
        raise ToolchainInstallError("ARM toolchain installer currently supports Linux package extraction only.")

    require_tool("apt")
    require_tool("dpkg-deb")

    toolchain_dir = repo_root / "toolchain" / "arm-none-eabi"
    packages = [*BASE_PACKAGES, *(CXX_PACKAGES if include_cxx else [])]

    with tempfile.TemporaryDirectory(prefix="arm-toolchain-") as temp:
        temp_dir = Path(temp)
        extracted = temp_dir / "extracted"
        run(["apt", "download", *packages], cwd=temp_dir)
        for package_path in temp_dir.glob("*.deb"):
            run(["dpkg-deb", "-x", str(package_path), str(extracted)])

        copy_tree_contents(extracted / "usr", toolchain_dir)

    return toolchain_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Install ARM none-eabi toolchain into ./toolchain for local STM32 builds.")
    parser.add_argument(
        "--include-cxx",
        action="store_true",
        help="Also download C++ newlib support. This adds roughly 2 GB installed size.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    try:
        toolchain_dir = install(repo_root, include_cxx=args.include_cxx)
    except ToolchainInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    bin_dir = toolchain_dir / "bin"
    print(f"ARM toolchain installed at {toolchain_dir}")
    print(f"Add to PATH for manual use: {bin_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
