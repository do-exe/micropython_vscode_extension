#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from typing import Callable


EXTENSION_ID = "micropython-extension.micropython-vscode-extension"
RUNTIME_ROOT_NAME = "runtime"


class RuntimeBuildError(RuntimeError):
    pass


def platform_key() -> str:
    machine = platform.machine().lower()
    if sys.platform == "linux":
        if machine in {"x86_64", "amd64"}:
            return "linux-x64"
        if machine in {"aarch64", "arm64"}:
            return "linux-arm64"
        raise RuntimeBuildError(f"Unsupported Linux architecture for bundled runtime: {machine}")

    if sys.platform == "win32":
        if machine in {"amd64", "x86_64"}:
            return "win32-x64"
        if machine in {"arm64", "aarch64"}:
            return "win32-arm64"
        raise RuntimeBuildError(f"Unsupported Windows architecture for bundled runtime: {machine}")

    raise RuntimeBuildError(f"Unsupported platform for bundled MicroPython runtime packaging: {sys.platform}")


def python_version_tag() -> str:
    return f"python{sys.version_info.major}.{sys.version_info.minor}"


def windows_site_packages_path() -> str:
    return "Lib/site-packages"


def runtime_default_site_packages_path() -> str:
    if sys.platform == "win32":
        return windows_site_packages_path()
    return f"lib/{python_version_tag()}/site-packages"


def resolve_existing_runtime_site_packages(repo_root: Path, version_tag: str) -> Path | None:
    runtime_dir = repo_root / RUNTIME_ROOT_NAME / platform_key()
    manifest_path = runtime_dir / "manifest.json"
    if not manifest_path.is_file():
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    candidates: list[Path] = []

    source_site_packages = manifest.get("sourceSitePackages")
    if isinstance(source_site_packages, str) and source_site_packages:
        source_path = Path(source_site_packages).expanduser()
        if source_path.is_absolute():
            candidates.append(source_path)
        else:
            candidates.append((runtime_dir / source_path).resolve())
            candidates.append((repo_root / source_path).resolve())

    site_packages = manifest.get("sitePackages")
    if isinstance(site_packages, str) and site_packages:
        candidates.append((runtime_dir / site_packages).resolve())

    candidates.append(runtime_dir / "lib" / version_tag / "site-packages")
    candidates.append(runtime_dir / windows_site_packages_path())

    for candidate in candidates:
        if candidate.is_dir():
            try:
                ensure_required_packages(candidate)
            except RuntimeBuildError:
                continue
            return candidate

    return None


def resolve_source_site_packages(repo_root: Path) -> Path:
    version_tag = python_version_tag()
    explicit_site_packages = os.environ.get("MICROPYTHON_SOURCE_SITE_PACKAGES")
    explicit_pyenv = os.environ.get("MICROPYTHON_SOURCE_PYENV")

    candidates: list[Path] = []
    if explicit_site_packages:
        candidates.append(Path(explicit_site_packages).expanduser())
    if explicit_pyenv:
        pyenv_path = Path(explicit_pyenv).expanduser()
        candidates.extend([
            pyenv_path / "lib" / version_tag / "site-packages",
            pyenv_path / windows_site_packages_path(),
        ])

    home = Path.home()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            for code_dir in ("Code", "Code - Insiders"):
                candidates.append(Path(appdata) / code_dir / "User" / "globalStorage" / EXTENSION_ID / "pyenv" / windows_site_packages_path())
    else:
        for code_dir in ("Code", "Code - Insiders"):
            candidates.append(home / ".config" / code_dir / "User" / "globalStorage" / EXTENSION_ID / "pyenv" / "lib" / version_tag / "site-packages")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    existing_runtime_site_packages = resolve_existing_runtime_site_packages(repo_root, version_tag)
    if existing_runtime_site_packages is not None:
        return existing_runtime_site_packages

    raise RuntimeBuildError(
        "No MicroPython source site-packages found. Set MICROPYTHON_SOURCE_SITE_PACKAGES or MICROPYTHON_SOURCE_PYENV, "
        "activate the extension once so it bootstraps its builder venv, or keep a previously bundled runtime in place."
    )


def ensure_required_packages(site_packages: Path) -> None:
    required = ("serial", "esptool")
    missing = [name for name in required if not (site_packages / name).exists()]
    if missing:
        raise RuntimeBuildError(f"Missing required packages in {site_packages}: {', '.join(missing)}")


def ignore_stdlib(_: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", "site-packages", "dist-packages"}
    return {name for name in names if name in ignored or name.endswith((".pyc", ".pyo"))}


def ignore_site_packages(_: str, names: list[str]) -> set[str]:
    ignored_prefixes = ("pip", "setuptools", "wheel")
    ignored = {"__pycache__"}
    for name in names:
        if name in ignored or name.endswith((".pyc", ".pyo")):
            ignored.add(name)
            continue
        if name.startswith(ignored_prefixes):
            ignored.add(name)
    return ignored


def copy_entry(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False, ignore=ignore_site_packages)
        return
    shutil.copy2(source, target, follow_symlinks=True)


def manifest_source_site_packages(repo_root: Path, runtime_key: str, source_site_packages: Path, site_packages_path: str) -> str:
    runtime_dir = repo_root / RUNTIME_ROOT_NAME / runtime_key
    resolved_source = source_site_packages.resolve()

    for base_dir in (runtime_dir.resolve(), repo_root.resolve()):
        try:
            relative = resolved_source.relative_to(base_dir)
        except ValueError:
            continue
        return relative.as_posix()

    return site_packages_path


def stage_runtime(repo_root: Path) -> Path:
    if sys.platform == "linux":
        return stage_linux_runtime(repo_root)
    if sys.platform == "win32":
        return stage_windows_runtime(repo_root)
    raise RuntimeBuildError(f"Unsupported platform for bundled MicroPython runtime packaging: {sys.platform}")


def stage_linux_runtime(repo_root: Path) -> Path:
    key = platform_key()
    runtime_root = repo_root / RUNTIME_ROOT_NAME
    destination = runtime_root / key
    runtime_root.mkdir(parents=True, exist_ok=True)

    stdlib_source = Path(sysconfig.get_path("stdlib"))
    if not stdlib_source.is_dir():
        raise RuntimeBuildError(f"Python stdlib path does not exist: {stdlib_source}")

    libpython_name = sysconfig.get_config_var("LDLIBRARY")
    lib_dir = sysconfig.get_config_var("LIBDIR")
    if not libpython_name or not lib_dir:
        raise RuntimeBuildError("Could not resolve libpython shared library for bundled runtime packaging.")
    libpython_source = (Path(lib_dir) / libpython_name).resolve()
    if not libpython_source.is_file():
        raise RuntimeBuildError(f"libpython shared library not found: {libpython_source}")

    interpreter_source = Path(sys.executable).resolve()
    if not interpreter_source.is_file():
        raise RuntimeBuildError(f"Python executable not found: {interpreter_source}")

    source_site_packages = resolve_source_site_packages(repo_root)
    ensure_required_packages(source_site_packages)

    temp_parent = runtime_root / ".tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{key}-", dir=temp_parent))
    try:
        bin_dir = temp_dir / "bin"
        lib_dir_path = temp_dir / "lib"
        python_lib_dir = lib_dir_path / python_version_tag()
        site_packages_dest = python_lib_dir / "site-packages"

        bin_dir.mkdir(parents=True, exist_ok=True)
        site_packages_dest.mkdir(parents=True, exist_ok=True)

        shutil.copy2(interpreter_source, bin_dir / "python3")
        os.chmod(bin_dir / "python3", 0o755)
        python_link = bin_dir / "python"
        if python_link.exists() or python_link.is_symlink():
            python_link.unlink()
        python_link.symlink_to("python3")

        shutil.copytree(stdlib_source, python_lib_dir, symlinks=False, dirs_exist_ok=True, ignore=ignore_stdlib)
        shutil.copy2(libpython_source, lib_dir_path / libpython_source.name)
        soname_link = lib_dir_path / libpython_name
        if soname_link.name != libpython_source.name:
            if soname_link.exists() or soname_link.is_symlink():
                soname_link.unlink()
            soname_link.symlink_to(libpython_source.name)

        for entry in source_site_packages.iterdir():
            if entry.name.startswith(("pip", "setuptools", "wheel")):
                continue
            if entry.name == "__pycache__":
                continue
            copy_entry(entry, site_packages_dest / entry.name)

        site_packages_path = runtime_default_site_packages_path()
        manifest = {
            "platformKey": key,
            "pythonVersion": sys.version.split()[0],
            "pythonExecutable": "bin/python3",
            "sitePackages": site_packages_path,
            "libraryPath": "lib",
            "sourceSitePackages": manifest_source_site_packages(repo_root, key, source_site_packages, site_packages_path),
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        validate_runtime(temp_dir)

        replace_runtime_directory(temp_dir, destination)
        return destination
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def stage_windows_runtime(repo_root: Path) -> Path:
    key = platform_key()
    runtime_root = repo_root / RUNTIME_ROOT_NAME
    destination = runtime_root / key
    runtime_root.mkdir(parents=True, exist_ok=True)

    stdlib_source = Path(sysconfig.get_path("stdlib"))
    if not stdlib_source.is_dir():
        raise RuntimeBuildError(f"Python stdlib path does not exist: {stdlib_source}")

    source_site_packages = resolve_source_site_packages(repo_root)
    ensure_required_packages(source_site_packages)

    interpreter_source = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if not interpreter_source.is_file():
        raise RuntimeBuildError(f"Python executable not found: {interpreter_source}")

    base_prefix = Path(sys.base_prefix).resolve()
    dlls_source = base_prefix / "DLLs"
    if not dlls_source.is_dir():
        raise RuntimeBuildError(f"Python DLLs path does not exist: {dlls_source}")

    temp_parent = runtime_root / ".tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{key}-", dir=temp_parent))
    try:
        lib_dir = temp_dir / "Lib"
        site_packages_dest = lib_dir / "site-packages"

        site_packages_dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(interpreter_source, temp_dir / "python.exe")

        pythonw_source = interpreter_source.with_name("pythonw.exe")
        if pythonw_source.is_file():
            shutil.copy2(pythonw_source, temp_dir / "pythonw.exe")

        shutil.copytree(stdlib_source, lib_dir, symlinks=False, dirs_exist_ok=True, ignore=ignore_stdlib)
        shutil.copytree(dlls_source, temp_dir / "DLLs", symlinks=False, dirs_exist_ok=True, ignore=ignore_stdlib)
        copy_windows_runtime_dlls(base_prefix, interpreter_source.parent, temp_dir)

        for entry in source_site_packages.iterdir():
            if entry.name.startswith(("pip", "setuptools", "wheel")):
                continue
            if entry.name == "__pycache__":
                continue
            copy_entry(entry, site_packages_dest / entry.name)

        site_packages_path = runtime_default_site_packages_path()
        manifest = {
            "platformKey": key,
            "pythonVersion": sys.version.split()[0],
            "pythonExecutable": "python.exe",
            "sitePackages": site_packages_path,
            "libraryPath": ".",
            "sourceSitePackages": manifest_source_site_packages(repo_root, key, source_site_packages, site_packages_path),
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        validate_runtime(temp_dir)

        replace_runtime_directory(temp_dir, destination)
        return destination
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def copy_windows_runtime_dlls(base_prefix: Path, executable_dir: Path, target_dir: Path) -> None:
    patterns = ("python*.dll", "vcruntime*.dll", "ucrtbase.dll", "api-ms-win-*.dll")
    copied: set[str] = set()

    for source_dir in (executable_dir, base_prefix):
        for pattern in patterns:
            for source in source_dir.glob(pattern):
                if not source.is_file():
                    continue
                target_name = source.name.lower()
                if target_name in copied:
                    continue
                shutil.copy2(source, target_dir / source.name)
                copied.add(target_name)


def replace_runtime_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        remove_runtime_directory(destination)

    retry_filesystem_operation(
        lambda: source.rename(destination),
        f"replace bundled runtime directory {destination}",
    )


def remove_runtime_directory(path: Path) -> None:
    retry_filesystem_operation(
        lambda: shutil.rmtree(path, onerror=remove_readonly),
        f"remove existing bundled runtime directory {path}",
    )


def remove_readonly(func: Callable[[str], None], path: str, _: object) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def retry_filesystem_operation(operation: Callable[[], None], description: str) -> None:
    last_error: OSError | None = None
    for _ in range(20):
        try:
            operation()
            return
        except OSError as exc:
            last_error = exc
            if not should_retry_filesystem_error(exc):
                break
            time.sleep(0.5)

    if last_error is None:
        return

    if sys.platform == "win32" and isinstance(last_error, PermissionError):
        raise RuntimeBuildError(
            f"Could not {description}: {last_error}. Close any VS Code windows or Python processes using the runtime. "
            "If Windows still reports Access is denied, reset ownership/ACLs for the generated runtime directory and rerun staging."
        ) from last_error

    raise last_error


def should_retry_filesystem_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if sys.platform == "win32":
        return getattr(exc, "winerror", None) in {5, 32, 33, 145}
    return False


def validate_runtime(runtime_dir: Path) -> None:
    manifest = json.loads((runtime_dir / "manifest.json").read_text(encoding="utf-8"))
    python_path = runtime_dir / manifest["pythonExecutable"]
    env = create_runtime_env(runtime_dir, manifest)

    result = subprocess.run(
        [
            str(python_path),
            "-c",
            "import sys, serial, esptool; print(sys.executable); print(sys.prefix); print(esptool.__version__)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        details = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        raise RuntimeBuildError(f"Bundled runtime validation failed.\n{details}")


def create_runtime_env(runtime_dir: Path, manifest: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    library_path = runtime_dir / manifest["libraryPath"]
    site_packages = runtime_dir / manifest["sitePackages"]
    env["PYTHONHOME"] = str(runtime_dir)
    env["PYTHONPATH"] = str(site_packages)
    env["PYTHONNOUSERSITE"] = "1"

    if sys.platform == "win32":
        path_entries = [str(runtime_dir), str(runtime_dir / "DLLs"), str(runtime_dir / "Scripts"), env.get("PATH", "")]
        env["PATH"] = os.pathsep.join(entry for entry in path_entries if entry)
    else:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(entry for entry in (str(library_path), env.get("LD_LIBRARY_PATH", "")) if entry)
        env["PATH"] = os.pathsep.join(entry for entry in (str(runtime_dir / "bin"), env.get("PATH", "")) if entry)

    return env


def directory_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}G"


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    try:
        destination = stage_runtime(repo_root)
    except RuntimeBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{format_size(directory_size(destination))}\t{destination}")
    print(f"Bundled runtime staged at {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
