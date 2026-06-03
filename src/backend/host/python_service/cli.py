from __future__ import annotations

import argparse
import base64
import json
from typing import Any

from .constants import DEFAULT_RUN_TIMEOUT_SEC
from .operations import run_file, run_soft_reset
from .session import PersistentSession
from .service import serve_loop
from .sync_utils import list_detected_esp_ports


def _quiet_session() -> PersistentSession:
    return PersistentSession(
        emit_terminal_text=lambda _text: None,
        emit_session_state=lambda _state: None,
    )

def emit(payload: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload))
    else:
        print(payload)
    return 0 if payload.get("ok") else 1

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MicroPython serial controller backend")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--stream", action="store_true", help="Stream output in CLI mode")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="List MicroPython serial ports")

    soft_reset = subparsers.add_parser("soft-reset", help="Soft reset device")
    soft_reset.add_argument("--port", required=True, help="Serial port path")
    soft_reset.add_argument("--timeout", type=float, default=5.0, help="Timeout seconds")

    run_file_parser = subparsers.add_parser("run-file", help="Run Python file on device")
    run_file_parser.add_argument("--port", required=True, help="Serial port path")
    run_file_parser.add_argument("--local-file", required=True, help="Local file path")
    run_file_parser.add_argument("--timeout", type=float, default=DEFAULT_RUN_TIMEOUT_SEC, help="Timeout seconds (0 disables timeout)")

    fs_scan_tree = subparsers.add_parser("fs-scan-tree", help="List all files and folders on the device")
    fs_scan_tree.add_argument("--port", required=True, help="Serial port path")

    fs_read = subparsers.add_parser("fs-read", help="Read a file from the device")
    fs_read.add_argument("--port", required=True, help="Serial port path")
    fs_read.add_argument("--remote-path", required=True, help="Device file path")

    fs_write = subparsers.add_parser("fs-write", help="Write a local file to the device")
    fs_write.add_argument("--port", required=True, help="Serial port path")
    fs_write.add_argument("--remote-path", required=True, help="Device file path")
    fs_write.add_argument("--local-file", required=True, help="Local file path")
    fs_write.add_argument("--overwrite", action="store_true", help="Overwrite an existing device file")

    fs_mkdir = subparsers.add_parser("fs-mkdir", help="Create a directory on the device")
    fs_mkdir.add_argument("--port", required=True, help="Serial port path")
    fs_mkdir.add_argument("--remote-path", required=True, help="Device directory path")

    fs_delete = subparsers.add_parser("fs-delete", help="Delete a file or folder from the device")
    fs_delete.add_argument("--port", required=True, help="Serial port path")
    fs_delete.add_argument("--remote-path", required=True, help="Device file or directory path")
    fs_delete.add_argument("--recursive", action="store_true", help="Delete directory contents recursively")

    fs_import = subparsers.add_parser("fs-import", help="Import the device filesystem into a local folder")
    fs_import.add_argument("--port", required=True, help="Serial port path")
    fs_import.add_argument("--local-folder", required=True, help="Local workspace folder")

    fs_sync_folder = subparsers.add_parser("fs-sync-folder", help="Sync a local folder to the device")
    fs_sync_folder.add_argument("--port", required=True, help="Serial port path")
    fs_sync_folder.add_argument("--local-folder", required=True, help="Local workspace folder")
    fs_sync_folder.add_argument("--remote-folder", default="/", help="Device destination folder")
    fs_sync_folder.add_argument("--delete-extraneous", action="store_true", help="Delete remote files missing from the local folder")

    subparsers.add_parser("serve", help="Run persistent backend service over stdio")
    return parser.parse_args()

def main() -> int:
    args = parse_args()

    if args.command == "serve":
        return serve_loop()
    if args.command == "scan":
        return emit({"ok": True, "devices": list_detected_esp_ports()}, getattr(args, "json", False))
    if args.command == "soft-reset":
        return emit(run_soft_reset(port=args.port, timeout_seconds=args.timeout), getattr(args, "json", False))
    if args.command == "run-file":
        stream = bool(getattr(args, "stream", False))
        if stream:
            payload = run_file(
                port=args.port,
                local_file=args.local_file,
                timeout_seconds=args.timeout,
                stdout_line_callback=lambda line: print(f"MICROPYTHON_OUT:{line}", flush=True),
                stderr_line_callback=lambda line: print(f"MICROPYTHON_ERR:{line}", flush=True),
            )
            print(json.dumps(payload), flush=True)
            return 0 if payload.get("ok") else 1
        return emit(
            run_file(port=args.port, local_file=args.local_file, timeout_seconds=args.timeout),
            getattr(args, "json", False),
        )
    if args.command == "fs-scan-tree":
        session = _quiet_session()
        try:
            return emit(session.workspace_scan_tree(port=args.port), getattr(args, "json", False))
        finally:
            session.close(emit_event=False, reason="cli-exit")
    if args.command == "fs-read":
        session = _quiet_session()
        try:
            result = session.workspace_read_file(port=args.port, remote_path=args.remote_path)
            content_base64 = result.get("contentBase64")
            if isinstance(content_base64, str):
                try:
                    result = {
                        **result,
                        "content": base64.b64decode(content_base64.encode("ascii")).decode("utf-8", errors="replace"),
                    }
                except Exception:
                    pass
            return emit(result, getattr(args, "json", False))
        finally:
            session.close(emit_event=False, reason="cli-exit")
    if args.command == "fs-write":
        session = _quiet_session()
        try:
            with open(args.local_file, "rb") as handle:
                content_base64 = base64.b64encode(handle.read()).decode("ascii")
            return emit(
                session.workspace_write_file(
                    port=args.port,
                    remote_path=args.remote_path,
                    content_base64=content_base64,
                    create=True,
                    overwrite=bool(args.overwrite),
                ),
                getattr(args, "json", False),
            )
        finally:
            session.close(emit_event=False, reason="cli-exit")
    if args.command == "fs-mkdir":
        session = _quiet_session()
        try:
            return emit(
                session.workspace_create_directory(port=args.port, remote_path=args.remote_path),
                getattr(args, "json", False),
            )
        finally:
            session.close(emit_event=False, reason="cli-exit")
    if args.command == "fs-delete":
        session = _quiet_session()
        try:
            return emit(
                session.workspace_delete(port=args.port, remote_path=args.remote_path, recursive=bool(args.recursive)),
                getattr(args, "json", False),
            )
        finally:
            session.close(emit_event=False, reason="cli-exit")
    if args.command == "fs-import":
        session = _quiet_session()
        try:
            return emit(
                session.import_workspace(port=args.port, local_folder=args.local_folder),
                getattr(args, "json", False),
            )
        finally:
            session.close(emit_event=False, reason="cli-exit")
    if args.command == "fs-sync-folder":
        session = _quiet_session()
        try:
            return emit(
                session.sync_folder(
                    port=args.port,
                    local_folder=args.local_folder,
                    remote_folder=args.remote_folder,
                    delete_extraneous=bool(args.delete_extraneous),
                ),
                getattr(args, "json", False),
            )
        finally:
            session.close(emit_event=False, reason="cli-exit")
    return emit({"ok": False, "error": f"Unsupported command: {args.command}"}, getattr(args, "json", False))

__all__ = [name for name, value in globals().items() if getattr(value, '__module__', None) == __name__]
