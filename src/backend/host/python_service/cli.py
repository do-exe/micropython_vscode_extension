from __future__ import annotations

import argparse
import json

from .constants import DEFAULT_RUN_TIMEOUT_SEC
from .operations import run_file, run_soft_reset
from .service import serve_loop
from .sync_utils import list_detected_esp_ports

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
    return emit({"ok": False, "error": f"Unsupported command: {args.command}"}, getattr(args, "json", False))

__all__ = [name for name, value in globals().items() if getattr(value, '__module__', None) == __name__]
