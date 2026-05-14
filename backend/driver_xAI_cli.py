from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

from driver_xAI_adapter import DriverXAIAdapter, DriverXAIError
import micropython_backend as backend


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    adapter = DriverXAIAdapter(config_path=args.config)

    try:
        if args.command_name == "catalog":
            payload = adapter.catalog(args.module_type)
        elif args.command_name == "path":
            payload = {"ok": True, "configPath": str(adapter.config_path)}
        elif args.command_name == "list":
            payload = adapter.hardware_catalog(args.module_id)
        elif args.command_name == "add":
            payload = adapter.add_hardware(
                module_id=args.module_id,
                module_type=args.module_type,
                pins=parse_pairs(args.pin),
                options=parse_pairs(args.option),
                replace=not args.no_replace,
            )
        elif args.command_name == "remove":
            payload = adapter.remove_hardware(args.module_id)
        elif args.command_name == "run":
            payload = run_hardware(adapter, args)
        else:
            parser.print_help()
            return 2
    except DriverXAIError as exc:
        print(f"Driver xAI error: {exc}", file=sys.stderr)
        return 1

    print_payload(payload, as_json=args.json)
    return 0 if bool(payload.get("ok", False)) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="driver_xAI_cli",
        description="Configure and control Driver xAI hardware modules for MicroPython MCP access.",
    )
    parser.add_argument(
        "--config",
        help="Hardware profile path. Defaults to .micropython/driver_xAI_hardware.json beside the extension.",
    )
    subparsers = parser.add_subparsers(dest="command_name")

    catalog = subparsers.add_parser("catalog", help="List available Driver xAI module types.")
    catalog.add_argument("module_type", nargs="?", help="Optional module type, such as rgb_led.")
    add_json_flag(catalog)

    path = subparsers.add_parser("path", help="Show the hardware profile path.")
    add_json_flag(path)

    list_command = subparsers.add_parser("list", help="List saved connected hardware modules.")
    list_command.add_argument("module_id", nargs="?", help="Optional saved hardware id.")
    add_json_flag(list_command)

    add = subparsers.add_parser("add", help="Add or update a connected hardware module.")
    add.add_argument("module_id", help="Saved hardware id, such as board_rgb.")
    add.add_argument("module_type", help="Driver xAI module type, such as rgb_led.")
    add.add_argument("--pin", action="append", default=[], metavar="NAME=VALUE", help="Pin setting. Repeat as needed.")
    add.add_argument("--option", action="append", default=[], metavar="NAME=VALUE", help="Option setting. Repeat as needed.")
    add.add_argument("--no-replace", action="store_true", help="Fail if module_id already exists.")
    add_json_flag(add)

    remove = subparsers.add_parser("remove", help="Remove a saved hardware module.")
    remove.add_argument("module_id", help="Saved hardware id.")
    add_json_flag(remove)

    run = subparsers.add_parser("run", help="Run a command on a saved hardware module.")
    run.add_argument("module_id", help="Saved hardware id.")
    run.add_argument("hardware_command", help="Driver command, such as set_color, off, or blink.")
    run.add_argument("--input", action="append", default=[], metavar="NAME=VALUE", help="Command input. Repeat as needed.")
    run.add_argument("--inputs-json", help="Command inputs as a JSON object.")
    run.add_argument("--port", help="Serial port. If omitted, the only detected MicroPython device is used.")
    run.add_argument("--timeout", type=float, default=10.0, help="Run timeout in seconds. Use 0 for no timeout.")
    add_json_flag(run)

    return parser


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")


def run_hardware(adapter: DriverXAIAdapter, args: argparse.Namespace) -> dict[str, Any]:
    port = resolve_port(args.port)
    inputs = parse_inputs(args.input, args.inputs_json)
    hardware = adapter.get_hardware_instance(args.module_id)
    code = adapter.generate_run_code(
        {"name": "driver_xAI_cli", "configPath": str(adapter.config_path)},
        hardware,
        args.hardware_command,
        inputs,
    )

    session = backend.PersistentSession(
        emit_terminal_text=lambda _text: None,
        emit_session_state=lambda _payload: None,
        emit_hybrid_event=lambda _payload: None,
    )
    temp_path: Path | None = None
    started_at = time.monotonic()
    try:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", delete=False)
        try:
            handle.write(code)
        finally:
            handle.close()
        temp_path = Path(handle.name)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        run_result = session.run_file(
            port=port,
            local_file=str(temp_path),
            timeout_seconds=max(0.0, min(600.0, float(args.timeout))),
            stdout_line_callback=stdout_lines.append,
            stderr_line_callback=stderr_lines.append,
        )
        parsed = adapter.parse_peripheral_result(str(run_result.get("output", "")))
        return {
            "ok": bool(run_result.get("ok")) and bool(parsed and parsed.get("ok")),
            "port": port,
            "configPath": str(adapter.config_path),
            "moduleId": hardware.get("id"),
            "moduleType": hardware.get("type"),
            "command": args.hardware_command,
            "inputs": inputs,
            "result": parsed,
            "stdout": run_result.get("output", ""),
            "error": run_result.get("error") or (parsed or {}).get("error"),
            "durationMs": int((time.monotonic() - started_at) * 1000),
            "stdoutLines": stdout_lines,
            "stderrLines": stderr_lines,
        }
    finally:
        try:
            session.close(emit_event=False, reason="driver-xai-cli-complete")
        except Exception:
            pass
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def resolve_port(explicit_port: str | None) -> str:
    if explicit_port:
        return explicit_port
    devices = backend.list_detected_esp_ports()
    if len(devices) == 1 and devices[0].get("port"):
        return str(devices[0]["port"])
    if len(devices) > 1:
        ports = ", ".join(str(device.get("port", "")) for device in devices)
        raise DriverXAIError(f"Multiple MicroPython devices detected ({ports}). Pass --port.")
    raise DriverXAIError("No MicroPython device detected. Connect a device or pass --port.")


def parse_inputs(pairs: list[str], inputs_json: str | None) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if inputs_json:
        try:
            parsed = json.loads(inputs_json)
        except json.JSONDecodeError as exc:
            raise DriverXAIError(f"--inputs-json must be a JSON object: {exc}") from exc
        if not isinstance(parsed, dict):
            raise DriverXAIError("--inputs-json must be a JSON object.")
        inputs.update(parsed)
    inputs.update(parse_pairs(pairs))
    return inputs


def parse_pairs(pairs: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise DriverXAIError(f"Expected NAME=VALUE, got: {pair}")
        key, raw_value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise DriverXAIError(f"Expected non-empty NAME in: {pair}")
        parsed[key] = parse_value(raw_value.strip())
    return parsed


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if "hardware" in payload and isinstance(payload["hardware"], list):
        print(f"Hardware profile: {payload.get('configPath')}")
        if not payload["hardware"]:
            print("No hardware modules configured.")
            return
        for entry in payload["hardware"]:
            commands = ", ".join(command.get("name", "") for command in entry.get("commands", []))
            print(f"- {entry.get('id')} ({entry.get('type')}): pins={entry.get('pins')} options={entry.get('options')}")
            print(f"  commands: {commands}")
        return

    if "modules" in payload and isinstance(payload["modules"], list):
        for module in payload["modules"]:
            print(f"- {module.get('type')}: {module.get('description')}")
        return

    if payload.get("action") in {"add", "update"}:
        hardware = payload.get("hardware", {})
        print(f"{payload.get('action')}: {hardware.get('id')} ({hardware.get('type')})")
        print(f"Saved to: {payload.get('configPath')}")
        return

    if payload.get("action") == "remove":
        print(f"removed: {payload.get('moduleId')}")
        print(f"Saved to: {payload.get('configPath')}")
        return

    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
