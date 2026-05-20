from __future__ import annotations

import ast
import posixpath
from pathlib import Path
from typing import Any

from . import device_detection as _device_detection
from . import sync_core as _sync_core
from device import micropython as _micropython
from .constants import (
    SYNC_DEVICE_COMMAND_TIMEOUT_SEC,
    SYNC_FILE_SCRIPT_CHUNK_BYTES,
    SYNC_SCAN_COMMAND_TIMEOUT_SEC,
    SYNC_TARGETED_SCAN_BATCH_SIZE,
    SYNC_TARGETED_SCAN_MAX_SCRIPT_CHARS,
)
from .errors import ControllerError, WorkspaceOperationError

def _scan_esp_ports() -> list[str]:
    return _device_detection.scan_microcontroller_ports()

def list_detected_esp_ports() -> list[dict[str, str]]:
    return _device_detection.list_detected_microcontroller_ports()

def _load_local_text_file(local_file: str) -> tuple[Path, str]:
    local_path = Path(local_file).expanduser().resolve()
    if not local_path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")
    if not local_path.is_file():
        raise IsADirectoryError(f"Path is not a file: {local_path}")
    try:
        return local_path, local_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File must be UTF-8 text: {exc}") from exc

def _normalize_remote_folder(remote_folder: str) -> str:
    return _sync_core.normalize_remote_folder(remote_folder)

def _sync_device_relative_path(remote_path: str) -> str:
    return _sync_core.sync_device_relative_path(remote_path)

def _sync_device_absolute_path(remote_path: str) -> str:
    return _sync_core.sync_device_absolute_path(remote_path)

def _select_workspace_entries(
    remote_dirs: list[str],
    remote_files: dict[str, int],
    selected_paths: list[str] | None,
) -> tuple[list[str], dict[str, int]]:
    normalized_dirs = {
        _sync_device_absolute_path(remote_dir)
        for remote_dir in remote_dirs
        if _sync_device_absolute_path(remote_dir) != "/"
    }
    normalized_files = {
        _sync_device_absolute_path(remote_path): int(file_size)
        for remote_path, file_size in remote_files.items()
    }

    if not selected_paths:
        return sorted(normalized_dirs), dict(sorted(normalized_files.items()))

    selected_dirs: set[str] = set()
    selected_files: dict[str, int] = {}
    normalized_selected: list[str] = []
    missing_paths: list[str] = []
    for remote_path in selected_paths:
        normalized_path = _sync_device_absolute_path(remote_path)
        if normalized_path not in normalized_selected:
            normalized_selected.append(normalized_path)

    for normalized_path in normalized_selected:
        if normalized_path == "/":
            return sorted(normalized_dirs), dict(sorted(normalized_files.items()))

        if normalized_path in normalized_files:
            selected_files[normalized_path] = normalized_files[normalized_path]
            parent = posixpath.dirname(normalized_path)
            while parent and parent != "/":
                if parent in normalized_dirs:
                    selected_dirs.add(parent)
                parent = posixpath.dirname(parent)
            continue

        if normalized_path in normalized_dirs:
            prefix = f"{normalized_path.rstrip('/')}/"
            selected_dirs.add(normalized_path)
            parent = posixpath.dirname(normalized_path)
            while parent and parent != "/":
                if parent in normalized_dirs:
                    selected_dirs.add(parent)
                parent = posixpath.dirname(parent)
            for remote_dir in normalized_dirs:
                if remote_dir.startswith(prefix):
                    selected_dirs.add(remote_dir)
            for remote_file, file_size in normalized_files.items():
                if remote_file.startswith(prefix):
                    selected_files[remote_file] = file_size
            continue

        missing_paths.append(normalized_path)

    if not selected_dirs and not selected_files:
        raise ValueError(
            "Selected MicroPython files or folders were not found on the device: "
            + ", ".join(missing_paths or normalized_selected)
        )

    return sorted(selected_dirs), dict(sorted(selected_files.items()))

def _fnv1a32_bytes(data: bytes) -> str:
    return _sync_core.fnv1a32_bytes(data)

def _compute_local_file_signature(local_path: Path, chunk_size: int = 4096) -> str:
    return _sync_core.compute_local_file_signature(local_path, chunk_size=chunk_size)

def _should_skip_sync_dir(name: str) -> bool:
    return _sync_core.should_skip_sync_dir(name)

def _should_skip_sync_file(relative_path: Path) -> bool:
    return _sync_core.should_skip_sync_file(relative_path)

def _scan_local_folder(local_folder: str, remote_folder: str) -> tuple[Path, list[str], list[dict[str, Any]]]:
    return _sync_core.scan_local_folder(local_folder, remote_folder)

def _build_sync_directory_plan(remote_root: str, files: list[dict[str, Any]]) -> list[str]:
    return _sync_core.build_sync_directory_plan(remote_root, files)

def _build_sync_plan(
    files: list[dict[str, Any]],
    remote_sizes: dict[str, int],
    delete_extraneous: bool,
    signature_matches: set[str] | None = None,
    size_fallback_paths: set[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[str], list[str]]:
    return _sync_core.build_sync_plan(
        files,
        remote_sizes,
        delete_extraneous,
        signature_matches=signature_matches,
        size_fallback_paths=size_fallback_paths,
    )

def _device_mkdir_script(remote_dir: str) -> str:
    return _micropython.device_mkdir_script(remote_dir)

def _device_delete_file_script(remote_file: str) -> str:
    return _micropython.device_delete_file_script(remote_file)

def _device_clear_all_script() -> str:
    return _micropython.device_clear_all_script()

def _device_list_file_sizes_script(remote_root: str) -> str:
    return _micropython.device_list_file_sizes_script(remote_root)

def _device_list_file_sizes_stream_script(remote_root: str) -> str:
    return _micropython.device_list_file_sizes_stream_script(remote_root)

def _device_list_file_signatures_script(remote_paths: list[str]) -> str:
    return _micropython.device_list_file_signatures_script(remote_paths)

def _device_list_file_signatures_stream_script(remote_paths: list[str]) -> str:
    return _micropython.device_list_file_signatures_stream_script(remote_paths)

def _device_selected_file_sizes_script(remote_paths: list[str]) -> str:
    return _micropython.device_selected_file_sizes_script(remote_paths)

def _device_selected_file_sizes_stream_script(remote_paths: list[str]) -> str:
    return _micropython.device_selected_file_sizes_stream_script(remote_paths)

def _device_scan_tree_stream_script(remote_root: str) -> str:
    return _micropython.device_scan_tree_stream_script(remote_root)

def _device_read_file_hex_stream_script(remote_file: str, chunk_bytes: int) -> str:
    return _micropython.device_read_file_hex_stream_script(remote_file, chunk_bytes=chunk_bytes)

def _device_read_text_file_stream_script(remote_file: str, chunk_chars: int) -> str:
    return _micropython.device_read_text_file_stream_script(remote_file, chunk_chars=chunk_chars)

def _device_stat_path_script(remote_path: str) -> str:
    return _micropython.device_stat_path_script(remote_path)

def _device_list_directory_stream_script(remote_dir: str) -> str:
    return _micropython.device_list_directory_stream_script(remote_dir)

def _device_statvfs_script(remote_path: str) -> str:
    return _micropython.device_statvfs_script(remote_path)

def _device_sync_script() -> str:
    return _micropython.device_sync_script()

def _device_delete_path_script(remote_path: str, recursive: bool) -> str:
    return _micropython.device_delete_path_script(remote_path, recursive=recursive)

def _device_rename_path_script(old_path: str, new_path: str) -> str:
    return _micropython.device_rename_path_script(old_path, new_path)

def _device_put_file_script(remote_file: str, data: bytes) -> str:
    return _micropython.device_put_file_script(remote_file, data, chunk_bytes=SYNC_FILE_SCRIPT_CHUNK_BYTES)

def _estimate_sync_source_timeout(source: str, minimum_seconds: float = SYNC_DEVICE_COMMAND_TIMEOUT_SEC) -> float:
    return _micropython.estimate_sync_source_timeout(source, minimum_seconds=minimum_seconds)

def _exec_sync_script(
    controller: MicroPythonController,
    source: str,
    timeout_seconds: float = SYNC_DEVICE_COMMAND_TIMEOUT_SEC,
    keep_raw_repl: bool = False,
) -> str:
    if keep_raw_repl:
        stdout_bytes, stderr_bytes = controller.exec_source_in_raw_repl(source, timeout_seconds)
    else:
        stdout_bytes, stderr_bytes = controller.exec_source(source, timeout_seconds)
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
    if stderr_text:
        raise ControllerError(stderr_text)
    return stdout_bytes.decode("utf-8", errors="replace")

def _parse_device_sizes_output(output: str) -> dict[str, int]:
    marker = "SIZES:"
    start = output.find(marker)
    if start < 0:
        snippet = output.strip()
        if not snippet:
            raise ControllerError("Device size scan returned no output.")
        raise ControllerError(f"Device size scan marker missing in output: {snippet[:200]}")

    start += len(marker)
    depth = 0
    end = start
    for index in range(start, len(output)):
        char = output[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break

    payload = output[start:end].strip()
    if not payload:
        return {}

    parsed = ast.literal_eval(payload)
    if not isinstance(parsed, dict):
        raise ControllerError("Device size scan returned an invalid payload.")

    result: dict[str, int] = {}
    for remote_path, file_size in parsed.items():
        result[str(remote_path)] = int(file_size)
    return result

def _parse_device_sizes_stream_output(output: str) -> dict[str, int]:
    result: dict[str, int] = {}
    done_seen = False

    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line == "SIZE_SCAN_DONE":
            done_seen = True
            continue
        if not line.startswith("SIZE:"):
            continue

        payload = line[len("SIZE:") :]
        split_index = payload.rfind(":")
        if split_index <= 0:
            continue
        remote_path = payload[:split_index]
        size_text = payload[split_index + 1 :]
        try:
            result[remote_path] = int(size_text)
        except Exception:
            continue

    if result or done_seen:
        return result

    snippet = output.strip()
    if not snippet:
        raise ControllerError("Device size scan stream returned no output.")
    raise ControllerError(f"Device size scan stream produced no SIZE rows: {snippet[:200]}")

def _parse_device_signatures_output(output: str) -> dict[str, str | None]:
    marker = "SIGS:"
    start = output.find(marker)
    if start < 0:
        snippet = output.strip()
        if not snippet:
            raise ControllerError("Device signature scan returned no output.")
        raise ControllerError(f"Device signature scan marker missing in output: {snippet[:200]}")

    start += len(marker)
    depth = 0
    end = start
    for index in range(start, len(output)):
        char = output[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break

    payload = output[start:end].strip()
    if not payload:
        return {}

    parsed = ast.literal_eval(payload)
    if not isinstance(parsed, dict):
        raise ControllerError("Device signature scan returned an invalid payload.")

    result: dict[str, str | None] = {}
    for remote_path, signature in parsed.items():
        result[str(remote_path)] = None if signature is None else str(signature)
    return result

def _parse_device_signatures_stream_output(output: str) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    done_seen = False

    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line == "SIG_SCAN_DONE":
            done_seen = True
            continue
        if not line.startswith("SIG:"):
            continue

        payload = line[len("SIG:") :]
        length_sep = payload.find(":")
        if length_sep <= 0:
            continue
        try:
            path_length = int(payload[:length_sep])
        except Exception:
            continue
        if path_length < 0:
            continue

        remainder = payload[length_sep + 1 :]
        minimum_remainder_length = path_length + 3
        if len(remainder) < minimum_remainder_length:
            continue

        remote_path = remainder[:path_length]
        if len(remainder) <= path_length or remainder[path_length] != ":":
            continue

        flag_and_value = remainder[path_length + 1 :]
        flag_sep = flag_and_value.find(":")
        if flag_sep < 0:
            continue
        flag = flag_and_value[:flag_sep]
        signature = flag_and_value[flag_sep + 1 :]

        if flag == "0":
            result[remote_path] = None
        elif flag == "1":
            result[remote_path] = signature

    if result or done_seen:
        return result

    snippet = output.strip()
    if not snippet:
        raise ControllerError("Device signature scan stream returned no output.")
    raise ControllerError(f"Device signature scan stream produced no SIG rows: {snippet[:200]}")

def _parse_device_selected_sizes_output(output: str) -> dict[str, int | None]:
    marker = "PATH_SIZES:"
    start = output.find(marker)
    if start < 0:
        snippet = output.strip()
        if not snippet:
            raise ControllerError("Device targeted size scan returned no output.")
        raise ControllerError(f"Device targeted size scan marker missing in output: {snippet[:200]}")

    start += len(marker)
    depth = 0
    end = start
    for index in range(start, len(output)):
        char = output[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break

    payload = output[start:end].strip()
    if not payload:
        return {}

    parsed = ast.literal_eval(payload)
    if not isinstance(parsed, dict):
        raise ControllerError("Device targeted size scan returned an invalid payload.")

    result: dict[str, int | None] = {}
    for remote_path, file_size in parsed.items():
        if file_size is None:
            result[str(remote_path)] = None
        else:
            result[str(remote_path)] = int(file_size)
    return result

def _parse_device_selected_sizes_stream_output(output: str) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    done_seen = False

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "PATH_SIZE_SCAN_DONE":
            done_seen = True
            continue
        if not line.startswith("PATHSIZE:"):
            continue

        payload = line[len("PATHSIZE:") :]
        length_sep = payload.find(":")
        if length_sep <= 0:
            continue
        try:
            path_length = int(payload[:length_sep])
        except Exception:
            continue
        if path_length < 0:
            continue

        remainder = payload[length_sep + 1 :]
        minimum_remainder_length = path_length + 3
        if len(remainder) < minimum_remainder_length:
            continue

        remote_path = remainder[:path_length]
        if len(remainder) <= path_length or remainder[path_length] != ":":
            continue

        flag_and_size = remainder[path_length + 1 :]
        flag_sep = flag_and_size.find(":")
        if flag_sep < 0:
            continue
        flag = flag_and_size[:flag_sep]
        size_text = flag_and_size[flag_sep + 1 :]

        if flag == "0":
            result[remote_path] = None
            continue
        if flag == "1":
            try:
                result[remote_path] = int(size_text)
            except Exception:
                continue

    if result or done_seen:
        return result

    snippet = output.strip()
    if not snippet:
        raise ControllerError("Device targeted size scan stream returned no output.")
    raise ControllerError(f"Device targeted size scan stream produced no PATHSIZE rows: {snippet[:200]}")

def _parse_device_tree_stream_output(output: str) -> tuple[list[str], dict[str, int]]:
    dirs: set[str] = set()
    files: dict[str, int] = {}
    errors: list[str] = []
    done_seen = False

    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line == "TREE_SCAN_DONE":
            done_seen = True
            continue
        if line.startswith("SCANERR:"):
            errors.append(line[len("SCANERR:") :])
            continue
        if line.startswith("DIR:"):
            payload = line[len("DIR:") :]
            length_sep = payload.find(":")
            if length_sep <= 0:
                continue
            try:
                path_length = int(payload[:length_sep])
            except Exception:
                continue
            remainder = payload[length_sep + 1 :]
            if path_length < 0 or len(remainder) < path_length:
                continue
            dirs.add(remainder[:path_length])
            continue
        if line.startswith("FILE:"):
            payload = line[len("FILE:") :]
            length_sep = payload.find(":")
            if length_sep <= 0:
                continue
            try:
                path_length = int(payload[:length_sep])
            except Exception:
                continue
            remainder = payload[length_sep + 1 :]
            if path_length < 0 or len(remainder) < path_length + 2:
                continue
            remote_path = remainder[:path_length]
            if len(remainder) <= path_length or remainder[path_length] != ":":
                continue
            size_text = remainder[path_length + 1 :]
            try:
                files[remote_path] = int(size_text)
            except Exception:
                files[remote_path] = 0

    if files or dirs or done_seen:
        return sorted(dirs), {remote_path: files[remote_path] for remote_path in sorted(files)}

    if errors:
        raise ControllerError(errors[0])

    snippet = output.strip()
    if not snippet:
        raise ControllerError("Device tree scan returned no output.")
    raise ControllerError(f"Device tree scan produced no DIR/FILE rows: {snippet[:200]}")

def _workspace_errno_to_code(errno_value: str | int | None) -> str | None:
    if errno_value is None:
        return None

    try:
        numeric = int(errno_value)
    except Exception:
        return None

    return {
        1: "EPERM",
        2: "ENOENT",
        17: "EEXIST",
        20: "ENOTDIR",
        21: "EISDIR",
        22: "EINVAL",
        28: "ENOSPC",
        39: "ENOTEMPTY",
    }.get(numeric)

def _parse_workspace_error_payload(payload: str) -> WorkspaceOperationError:
    errno_text, separator, message = payload.partition(":")
    code = _workspace_errno_to_code(errno_text.strip() if separator else None)
    detail = message.strip() if separator else payload.strip()
    if not detail:
        detail = "MicroPython workspace operation failed."
    return WorkspaceOperationError(detail, code=code)

def _workspace_exception_code(exc: Exception) -> str | None:
    code = getattr(exc, "code", None)
    return str(code) if isinstance(code, str) and code else None

def _parse_device_stat_output(output: str, *, remote_path: str) -> dict[str, Any]:
    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("STATERR:"):
            raise _parse_workspace_error_payload(line[len("STATERR:") :])
        if not line.startswith("STAT:"):
            continue

        fields = line[len("STAT:") :].split(":", 3)
        if len(fields) != 4:
            continue
        kind_code, size_text, mtime_text, ctime_text = fields
        kind = "directory" if kind_code == "D" else "file"

        try:
            size = int(size_text)
        except Exception:
            size = 0
        try:
            mtime = int(mtime_text)
        except Exception:
            mtime = 0
        try:
            ctime = int(ctime_text)
        except Exception:
            ctime = mtime

        return {
            "path": remote_path,
            "kind": kind,
            "size": size,
            "mtime": mtime,
            "ctime": ctime,
        }

    snippet = output.strip()
    if not snippet:
        raise ControllerError(f"Device stat returned no output for {remote_path}.")
    raise ControllerError(f"Device stat produced no STAT row for {remote_path}: {snippet[:200]}")

def _parse_device_list_directory_output(output: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    done_seen = False

    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line == "LIST_DONE":
            done_seen = True
            continue
        if line.startswith("LISTERR:"):
            raise _parse_workspace_error_payload(line[len("LISTERR:") :])
        if not line.startswith("ENTRY:"):
            continue

        payload = line[len("ENTRY:") :]
        length_sep = payload.find(":")
        if length_sep <= 0:
            continue
        try:
            path_length = int(payload[:length_sep])
        except Exception:
            continue

        remainder = payload[length_sep + 1 :]
        if path_length < 0 or len(remainder) < path_length + 3:
            continue

        remote_path = remainder[:path_length]
        if len(remainder) <= path_length or remainder[path_length] != ":":
            continue

        fields = remainder[path_length + 1 :].split(":", 2)
        if len(fields) != 3:
            continue
        kind_code, size_text, mtime_text = fields

        try:
            size = int(size_text)
        except Exception:
            size = 0
        try:
            mtime = int(mtime_text)
        except Exception:
            mtime = 0

        entries.append(
            {
                "name": posixpath.basename(remote_path),
                "path": remote_path,
                "kind": "directory" if kind_code == "D" else "file",
                "size": size,
                "mtime": mtime,
                "ctime": mtime,
            }
        )

    if entries or done_seen:
        return sorted(entries, key=lambda entry: (entry["kind"] != "directory", entry["name"].lower()))

    snippet = output.strip()
    if not snippet:
        raise ControllerError("Device directory listing returned no output.")
    raise ControllerError(f"Device directory listing produced no ENTRY rows: {snippet[:200]}")

def _parse_device_statvfs_output(output: str, *, remote_path: str) -> dict[str, Any]:
    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("STATVFSERR:"):
            raise _parse_workspace_error_payload(line[len("STATVFSERR:") :])
        if not line.startswith("STATVFS:"):
            continue

        fields = line[len("STATVFS:") :].split(":")
        if len(fields) < 5:
            continue

        try:
            block_size = max(0, int(fields[0]))
        except Exception:
            block_size = 0
        try:
            fragment_size = max(0, int(fields[1]))
        except Exception:
            fragment_size = 0
        try:
            blocks = max(0, int(fields[2]))
        except Exception:
            blocks = 0
        try:
            free_blocks = max(0, int(fields[3]))
        except Exception:
            free_blocks = 0
        try:
            available_blocks = max(0, int(fields[4]))
        except Exception:
            available_blocks = free_blocks

        byte_unit = fragment_size or block_size
        total_bytes = blocks * byte_unit
        free_bytes = free_blocks * byte_unit
        used_bytes = max(0, total_bytes - free_bytes)

        return {
            "path": remote_path,
            "blockSize": block_size,
            "fragmentSize": fragment_size,
            "blocks": blocks,
            "freeBlocks": free_blocks,
            "availableBlocks": available_blocks,
            "totalBytes": total_bytes,
            "freeBytes": free_bytes,
            "usedBytes": used_bytes,
        }

    snippet = output.strip()
    if not snippet:
        raise ControllerError(f"Device statvfs returned no output for {remote_path}.")
    raise ControllerError(f"Device statvfs produced no STATVFS row for {remote_path}: {snippet[:200]}")

def _parse_device_sync_output(output: str) -> bool:
    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line == "SYNC_OK":
            return True
        if line == "SYNC_UNSUPPORTED":
            return False
        if line.startswith("SYNCERR:"):
            raise _parse_workspace_error_payload(line[len("SYNCERR:") :])

    snippet = output.strip()
    if not snippet:
        raise ControllerError("Device sync returned no output.")
    raise ControllerError(f"Device sync produced no confirmation: {snippet[:200]}")

def _parse_device_delete_path_output(output: str, *, remote_path: str) -> str:
    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("DELERR:"):
            raise _parse_workspace_error_payload(line[len("DELERR:") :])
        if line == "DELOK:D":
            return "directory"
        if line == "DELOK:F":
            return "file"

    snippet = output.strip()
    if not snippet:
        raise ControllerError(f"Device delete returned no output for {remote_path}.")
    raise ControllerError(f"Device delete produced no DELOK row for {remote_path}: {snippet[:200]}")

def _parse_device_rename_path_output(output: str, *, old_path: str, new_path: str) -> None:
    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("RENAMEERR:"):
            raise _parse_workspace_error_payload(line[len("RENAMEERR:") :])
        if line == "RENAME_OK":
            return

    snippet = output.strip()
    if not snippet:
        raise ControllerError(f"Device rename returned no output for {old_path} -> {new_path}.")
    raise ControllerError(f"Device rename produced no confirmation for {old_path} -> {new_path}: {snippet[:200]}")

def _parse_device_file_hex_output(output: str, *, remote_path: str) -> bytes:
    chunks: list[bytes] = []
    done_seen = False
    error_text: str | None = None

    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line == "FILE_READ_DONE":
            done_seen = True
            continue
        if line.startswith("FILE_READ_ERR:"):
            error_text = line[len("FILE_READ_ERR:") :].strip() or f"Failed to read {remote_path}"
            continue
        if not line.startswith("HEX:"):
            continue
        payload = line[len("HEX:") :]
        try:
            chunks.append(bytes.fromhex(payload))
        except Exception:
            continue

    if error_text is not None:
        raise ControllerError(error_text)
    if chunks or done_seen:
        return b"".join(chunks)

    snippet = output.strip()
    if not snippet:
        raise ControllerError(f"Device file read returned no output for {remote_path}.")
    raise ControllerError(f"Device file read produced no HEX rows for {remote_path}: {snippet[:200]}")

def _parse_device_text_file_output(output: str, *, remote_path: str) -> str:
    start_marker = "[[MICROPYTHON_FILE_CONTENT_START]]"
    end_marker = "[[MICROPYTHON_FILE_CONTENT_END]]"
    error_text: str | None = None

    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line.startswith("FILE_READ_ERR:"):
            error_text = line[len("FILE_READ_ERR:") :].strip() or f"Failed to read {remote_path}"
            break

    if error_text is not None:
        raise ControllerError(error_text)

    start_index = output.find(start_marker)
    end_index = output.find(end_marker)
    if start_index < 0 or end_index < 0 or end_index < start_index:
        snippet = output.strip()
        if not snippet:
            raise ControllerError(f"Device text file read returned no output for {remote_path}.")
        raise ControllerError(f"Device text file read markers missing for {remote_path}: {snippet[:200]}")

    content_start = output.find("\n", start_index)
    if content_start < 0:
        return ""
    content = output[content_start + 1 : end_index]
    return content.rstrip("\r\n")

def _parse_clear_all_output(output: str) -> dict[str, Any]:
    files_deleted: list[str] = []
    directories_deleted: list[str] = []
    warning_lines: list[str] = []
    other_lines: list[str] = []
    start_seen = False
    done_seen = False

    for raw_line in output.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line == "CLEANUP_START":
            start_seen = True
            continue
        if line == "CLEANUP_DONE":
            done_seen = True
            continue
        if line.startswith("FILE_DEL:"):
            files_deleted.append(line[len("FILE_DEL:") :].strip())
            continue
        if line.startswith("DIR_DEL:"):
            directories_deleted.append(line[len("DIR_DEL:") :].strip())
            continue
        if line.startswith("FILE_ERR:") or line.startswith("DIR_ERR:") or line.startswith("ERR:"):
            warning_lines.append(line)
            continue
        other_lines.append(line)

    return {
        "startSeen": start_seen,
        "doneSeen": done_seen,
        "filesDeleted": files_deleted,
        "directoriesDeleted": directories_deleted,
        "warningLines": warning_lines,
        "otherLines": other_lines,
    }

def _chunk_remote_paths_for_targeted_scan(
    remote_paths: list[str],
    max_batch_size: int = SYNC_TARGETED_SCAN_BATCH_SIZE,
    max_script_chars: int = SYNC_TARGETED_SCAN_MAX_SCRIPT_CHARS,
) -> list[list[str]]:
    if not remote_paths:
        return []

    batches: list[list[str]] = []
    current_batch: list[str] = []
    for remote_path in remote_paths:
        candidate = current_batch + [remote_path]
        estimated_script_chars = len(_device_selected_file_sizes_script(candidate))
        if current_batch and (
            len(candidate) > max_batch_size or estimated_script_chars > max_script_chars
        ):
            batches.append(current_batch)
            current_batch = [remote_path]
            continue
        current_batch = candidate

    if current_batch:
        batches.append(current_batch)
    return batches

def _read_remote_file_sizes(controller: MicroPythonController, remote_root: str) -> dict[str, int]:
    return controller.sync_get_file_sizes(remote_root, timeout=SYNC_SCAN_COMMAND_TIMEOUT_SEC)

__all__ = [name for name, value in globals().items() if getattr(value, '__module__', None) == __name__]
