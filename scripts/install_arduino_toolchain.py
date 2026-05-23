#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


LATEST_RELEASE_URL = "https://api.github.com/repos/arduino/arduino-cli/releases/latest"


class ArduinoToolchainInstallError(RuntimeError):
    pass


def machine_asset_name(version: str) -> str:
    if sys.platform != "linux":
        raise ArduinoToolchainInstallError("Arduino CLI installer currently supports Linux only.")
    return f"arduino-cli_{version}_Linux_64bit.tar.gz"


def resolve_release(version: str | None) -> tuple[str, str]:
    if version:
        normalized = version[1:] if version.startswith("v") else version
        url = f"https://github.com/arduino/arduino-cli/releases/download/v{normalized}/{machine_asset_name(normalized)}"
        return normalized, url

    with urllib.request.urlopen(LATEST_RELEASE_URL, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    tag = str(payload.get("tag_name", "")).strip()
    if not tag:
        raise ArduinoToolchainInstallError("Could not resolve latest Arduino CLI release tag.")

    normalized = tag[1:] if tag.startswith("v") else tag
    expected_name = machine_asset_name(normalized)
    for asset in payload.get("assets", []):
        if asset.get("name") == expected_name and asset.get("browser_download_url"):
            return normalized, str(asset["browser_download_url"])

    raise ArduinoToolchainInstallError(f"Could not find release asset: {expected_name}")


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def write_config(toolchain_dir: Path) -> Path:
    config_path = toolchain_dir / "arduino-cli.yaml"
    data_dir = toolchain_dir / "data"
    downloads_dir = toolchain_dir / "downloads"
    user_dir = toolchain_dir / "user"
    for path in (data_dir, downloads_dir, user_dir):
        path.mkdir(parents=True, exist_ok=True)

    config_path.write_text(
        "\n".join([
            "directories:",
            f"  data: {data_dir}",
            f"  downloads: {downloads_dir}",
            f"  user: {user_dir}",
            "board_manager:",
            "  additional_urls: []",
            "",
        ]),
        encoding="utf-8",
    )
    return config_path


def run(command: list[str]) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise ArduinoToolchainInstallError(f"Command failed with code {result.returncode}: {' '.join(command)}")


def install(repo_root: Path, version: str | None = None, update_index: bool = True) -> Path:
    toolchain_dir = repo_root / "toolchain" / "arduino"
    bin_dir = toolchain_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    resolved_version, url = resolve_release(version)
    with tempfile.TemporaryDirectory(prefix="arduino-cli-") as temp:
        temp_dir = Path(temp)
        archive = temp_dir / machine_asset_name(resolved_version)
        download(url, archive)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(temp_dir)
        cli_source = temp_dir / "arduino-cli"
        if not cli_source.is_file():
            raise ArduinoToolchainInstallError("Downloaded archive did not contain arduino-cli.")
        shutil.copy2(cli_source, bin_dir / "arduino-cli")
        (bin_dir / "arduino-cli").chmod(0o755)

    config_path = write_config(toolchain_dir)
    if update_index:
        run([str(bin_dir / "arduino-cli"), "--config-file", str(config_path), "core", "update-index"])

    (toolchain_dir / "VERSION").write_text(resolved_version + "\n", encoding="utf-8")
    return toolchain_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Arduino CLI into ./toolchain/arduino.")
    parser.add_argument("--version", help="Arduino CLI version, for example 1.5.0. Defaults to latest GitHub release.")
    parser.add_argument("--skip-index", action="store_true", help="Do not run arduino-cli core update-index after install.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    try:
        toolchain_dir = install(repo_root, version=args.version, update_index=not args.skip_index)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Arduino toolchain installed at {toolchain_dir}")
    print(f"Arduino CLI: {toolchain_dir / 'bin' / 'arduino-cli'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
