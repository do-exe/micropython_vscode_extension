#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_SOURCE_ROOT = Path("/home/rupesh/Music/calsci/CalSci_firmware")


class EspIdfInstallError(RuntimeError):
    pass


def copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise EspIdfInstallError(f"Source directory does not exist: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=True)


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise EspIdfInstallError(f"Command failed with code {result.returncode}: {' '.join(command)}")


def install(repo_root: Path, source_root: Path = DEFAULT_SOURCE_ROOT, validate: bool = True) -> Path:
    source_idf = source_root / "toolchain" / "esp-idf"
    source_tools = Path.home() / ".espressif"
    target_idf = repo_root / "toolchain" / "esp-idf"
    target_tools = repo_root / "toolchain" / "espressif"

    copy_tree(source_idf, target_idf)
    copy_tree(source_tools, target_tools)

    if validate:
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        env["IDF_TOOLS_PATH"] = str(target_tools)
        run(
            ["bash", "-lc", "source toolchain/esp-idf/export.sh >/tmp/micropython_extension_idf_export.log && idf.py --version"],
            cwd=repo_root,
            env=env,
        )

    return target_idf


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy ESP-IDF and Espressif tools into ./toolchain for local ESP builds.")
    parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Source root containing toolchain/esp-idf. Defaults to the local CalSci_firmware checkout.",
    )
    parser.add_argument("--skip-validate", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    try:
        target_idf = install(repo_root, Path(args.source_root).expanduser().resolve(), validate=not args.skip_validate)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"ESP-IDF installed at {target_idf}")
    print(f"ESP-IDF tools installed at {repo_root / 'toolchain' / 'espressif'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
