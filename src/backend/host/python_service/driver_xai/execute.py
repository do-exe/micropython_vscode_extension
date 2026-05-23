from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .bundle import prepare_bundle
from .catalog import DriverXaiCatalog, DriverXaiError


DEFAULT_EXECUTE_TIMEOUT_SECONDS = 30.0


def execute_module(
    session: Any,
    *,
    port: str | None,
    module_id: str,
    setup: dict[str, Any],
    command: str,
    command_parameters: dict[str, Any] | None = None,
    catalog_root: str | None = None,
    output_dir: str | None = None,
    remote_root: str = "/",
    timeout_seconds: float = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    delete_extraneous: bool = False,
    hold_ms: int = 0,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    catalog = DriverXaiCatalog(catalog_root)
    started_at = time.monotonic()
    command_parameters = command_parameters or {}

    plan = build_execute_plan(
        catalog,
        module_id=module_id,
        setup=setup,
        command=command,
        command_parameters=command_parameters,
        hold_ms=hold_ms,
    )
    if plan["problems"]:
        return {
            "ok": False,
            "failedStep": "plan",
            "catalogRoot": str(catalog.root),
            "moduleId": module_id,
            "command": command,
            "problems": plan["problems"],
            "hardwareFlow": plan["hardwareFlow"],
            "aiContext": plan["aiContext"],
            "resolvedSetup": plan["resolvedSetup"],
            "error": {
                "code": "invalid_setup_value" if any(problem.get("code") != "missing_setup_value" for problem in plan["problems"]) else "missing_setup_value",
                "message": "Driver xAI setup validation failed.",
                "details": {"problems": plan["problems"]},
            },
        }

    temp_context = tempfile.TemporaryDirectory(prefix="driver_xai_execute_") if output_dir is None else None
    bundle_root = Path(output_dir or temp_context.name).expanduser().resolve()
    try:
        prepare_result = prepare_bundle(
            [module_id],
            str(bundle_root),
            catalog_root=str(catalog.root),
            parameters={module_id: plan["resolvedSetup"]},
        )
        runner_path = bundle_root / "driver_xai_run.py"
        runner_path.write_text(plan["source"], encoding="utf-8")

        sync_progress: list[str] = []
        sync_result = session.sync_folder(
            port=port,
            local_folder=str(bundle_root),
            remote_folder=remote_root,
            delete_extraneous=delete_extraneous,
            progress_callback=_chain_progress(sync_progress, progress_callback),
        )
        if not sync_result.get("ok"):
            return {
                "ok": False,
                "failedStep": "sync",
                "catalogRoot": str(catalog.root),
                "moduleId": module_id,
                "command": command,
                "bundleDir": str(bundle_root),
                "remoteRoot": remote_root,
                "prepare": prepare_result,
                "sync": sync_result,
                "progress": sync_progress[-80:],
                "hardwareFlow": plan["hardwareFlow"],
                "aiContext": plan["aiContext"],
                "resolvedSetup": plan["resolvedSetup"],
                "durationMs": int((time.monotonic() - started_at) * 1000),
                "error": {
                    "code": "transport_error",
                    "message": str(sync_result.get("error") or "Driver xAI bundle sync failed."),
                    "details": sync_result,
                },
            }

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        run_result = session.run_file(
            port=port,
            local_file=str(runner_path),
            timeout_seconds=timeout_seconds,
            stdout_line_callback=stdout_lines.append,
            stderr_line_callback=stderr_lines.append,
        )
        parsed = parse_driver_xai_output(str(run_result.get("output", "")))
        run_ok = bool(run_result.get("ok"))
        parsed_ok = bool(parsed.get("ok", False))
        execute_ok = run_ok and parsed_ok
        error_payload = None if execute_ok else (
            _normalize_error_payload(parsed.get("error"))
            if run_ok
            else {
                "code": "transport_error",
                "message": str(run_result.get("error") or "Driver xAI run step failed."),
                "details": run_result,
            }
        )

        return {
            "ok": execute_ok,
            "failedStep": None if execute_ok else "run",
            "catalogRoot": str(catalog.root),
            "moduleId": module_id,
            "command": command,
            "bundleDir": str(bundle_root),
            "remoteRoot": remote_root,
            "prepare": prepare_result,
            "sync": sync_result,
            "run": run_result,
            "driverResult": parsed.get("result"),
            "driverError": parsed.get("error"),
            "stdoutLines": stdout_lines,
            "stderrLines": stderr_lines,
            "progress": sync_progress[-80:],
            "hardwareFlow": plan["hardwareFlow"],
            "aiContext": plan["aiContext"],
            "resolvedSetup": plan["resolvedSetup"],
            "durationMs": int((time.monotonic() - started_at) * 1000),
            "error": error_payload,
        }
    finally:
        if temp_context is not None:
            temp_context.cleanup()


def build_execute_plan(
    catalog: DriverXaiCatalog,
    *,
    module_id: str,
    setup: dict[str, Any],
    command: str,
    command_parameters: dict[str, Any],
    hold_ms: int = 0,
) -> dict[str, Any]:
    info = catalog.info(module_id)
    parameters = catalog.get(module_id, False, None, "parameters", "json", "all")
    command_specs = catalog.get(module_id, False, None, "commands", "json", "all").get("commands", [])
    command_spec = next((item for item in command_specs if item.get("name") == command), None)

    problems: list[dict[str, Any]] = []
    if command_spec is None:
        problems.append({
            "code": "unknown_command",
            "message": f"Command {command!r} is not defined for module {module_id!r}.",
            "availableCommands": [item.get("name") for item in command_specs],
        })

    resolved = resolve_setup(parameters, setup, info)
    problems.extend(resolved["problems"])
    if problems:
        return {
            "problems": problems,
            "hardwareFlow": build_hardware_flow(info, resolved["resolvedSetup"], command, command_parameters),
            "aiContext": build_ai_context(info, parameters, command_spec, resolved["resolvedSetup"], command_parameters),
            "resolvedSetup": resolved["resolvedSetup"],
            "source": "",
        }

    source = build_execute_source(
        module_id=module_id,
        info=info,
        resolved_setup=resolved["resolvedSetup"],
        constructor_source=resolved["constructorSource"],
        command=command,
        command_parameters=command_parameters,
        hold_ms=hold_ms,
    )
    return {
        "problems": [],
        "hardwareFlow": build_hardware_flow(info, resolved["resolvedSetup"], command, command_parameters),
        "aiContext": build_ai_context(info, parameters, command_spec, resolved["resolvedSetup"], command_parameters),
        "resolvedSetup": resolved["resolvedSetup"],
        "source": source,
    }


def resolve_setup(parameters: dict[str, Any], setup: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    module_id = str(parameters.get("module_id") or info.get("module_id") or "")
    protocol = info.get("protocol")
    interface = info.get("interface")
    setup = setup or {}
    problems: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {
        "module_id": module_id,
        "interface": interface,
        "protocol": protocol,
        "pins": {},
        "options": {},
        "config": {},
        "bus": {},
    }

    for key, default in (parameters.get("pins") or {}).items():
        value = _setup_value(setup, "pins", key, default)
        if value is None or (key == "channels" and not value):
            problems.append(_missing_problem(module_id, f"pins.{key}", f"GPIO pin for {key}"))
        resolved["pins"][key] = value

    for key, default in (parameters.get("options") or {}).items():
        value = _setup_value(setup, "options", key, _parameter_default(default))
        _validate_allowed(module_id, f"options.{key}", value, default, problems)
        resolved["options"][key] = value

    for key, default in (parameters.get("config") or {}).items():
        value = _setup_value(setup, "config", key, default)
        if value is None:
            problems.append(_missing_problem(module_id, f"config.{key}", f"configuration value for {key}"))
        resolved["config"][key] = value

    for key, default in (parameters.get("bus") or {}).items():
        value = _setup_value(setup, "bus", key, default)
        if protocol == "i2c" and key in {"sda", "scl"} and value is None:
            problems.append(_missing_problem(module_id, f"bus.{key}", f"I2C {key.upper()} GPIO pin"))
        resolved["bus"][key] = value

    constructor_source = ""
    if protocol == "i2c":
        constructor_source = _i2c_constructor_source(resolved)
    elif interface == "gpio":
        constructor_source = _gpio_constructor_source(resolved)
    else:
        problems.append({
            "code": "unsupported_hardware_binding",
            "message": f"Driver xAI execute does not yet know how to instantiate module {module_id!r}.",
            "interface": interface,
            "protocol": protocol,
        })

    return {
        "problems": problems,
        "resolvedSetup": resolved,
        "constructorSource": constructor_source,
    }


def build_execute_source(
    *,
    module_id: str,
    info: dict[str, Any],
    resolved_setup: dict[str, Any],
    constructor_source: str,
    command: str,
    command_parameters: dict[str, Any],
    hold_ms: int,
) -> str:
    imports = [
        "import sys",
        "import time",
        "try:",
        "    import ujson as json",
        "except ImportError:",
        "    import json",
        "import driver_xai",
    ]
    payload = {
        "module_id": module_id,
        "command": command,
    }
    return "\n".join([
        *imports,
        "",
        f"METADATA = {_source_literal(payload)}",
        "",
        "try:",
        f"    response = driver_xai.execute({module_id!r}, {command!r}, {_source_literal(command_parameters)})",
        "    if not response.get('ok'):",
        "        raise RuntimeError(response.get('error'))",
        "    time.sleep_ms(20)",
        "    payload = json.dumps({'ok': True, 'metadata': METADATA, 'result': response.get('result'), 'response': response})",
        "    sys.stdout.write('DRIVER_XAI_RESULT:' + payload + '\\n')",
        "    try:",
        "        sys.stdout.flush()",
        "    except Exception:",
        "        pass",
        f"    HOLD_MS = {max(0, int(hold_ms))}",
        "    if HOLD_MS:",
        "        time.sleep_ms(HOLD_MS)",
        "except Exception as exc:",
        "    time.sleep_ms(20)",
        "    sys.stdout.write('DRIVER_XAI_ERROR:' + json.dumps({'ok': False, 'metadata': METADATA, 'error': repr(exc)}) + '\\n')",
        "    try:",
        "        sys.stdout.flush()",
        "    except Exception:",
        "        pass",
        "    raise",
        "",
    ])


def build_hardware_flow(
    info: dict[str, Any],
    resolved_setup: dict[str, Any],
    command: str,
    command_parameters: dict[str, Any],
) -> dict[str, Any]:
    interface = info.get("interface")
    protocol = info.get("protocol")
    flow: list[str] = []
    if interface == "gpio":
        pins = resolved_setup.get("pins", {})
        flow.append("Host reads Driver xAI module metadata and GPIO parameters.")
        flow.append(f"MicroPython imports modules.{info['module_id']}.drivers.micropython.Driver from the deployed bundle.")
        if pins:
            flow.append("Driver binds PWM/GPIO outputs: " + ", ".join(f"{name}=GPIO{pin}" for name, pin in pins.items()))
        common_type = resolved_setup.get("options", {}).get("common_type")
        if common_type == "common_cathode":
            flow.append("Common cathode/common GND: larger PWM duty means brighter channel.")
        elif common_type == "common_anode":
            flow.append("Common anode/common 3V3: driver inverts PWM so larger logical value means brighter channel.")
    elif protocol == "i2c":
        bus = resolved_setup.get("bus", {})
        flow.append("Host reads Driver xAI module metadata and I2C parameters.")
        flow.append(f"MicroPython creates I2C bus {bus.get('name')} with SDA=GPIO{bus.get('sda')} and SCL=GPIO{bus.get('scl')}.")
        flow.append(f"Driver command runs against I2C address {resolved_setup.get('config', {}).get('address')}.")

    flow.append(f"Driver command executed: {command}({command_parameters})")
    return {
        "moduleId": info.get("module_id"),
        "interface": interface,
        "protocol": protocol,
        "steps": flow,
        "assumptions": [
            "RGB/LED channels have current-limiting resistors.",
            "GPIO numbers are board GPIO numbers, not physical header labels.",
        ],
    }


def build_ai_context(
    info: dict[str, Any],
    parameters: dict[str, Any],
    command_spec: dict[str, Any] | None,
    resolved_setup: dict[str, Any],
    command_parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "module": info,
        "parameterTemplate": parameters,
        "resolvedSetup": resolved_setup,
        "commandSpec": command_spec,
        "commandParameters": command_parameters,
        "controlRule": (
            "Use Driver xAI catalog metadata first, then instantiate the module driver, then deploy/run through the MicroPython backend. "
            "Do not bypass the extension serial/session layer."
        ),
    }


def parse_driver_xai_output(output: str) -> dict[str, Any]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for index, text in enumerate(lines):
        if text.startswith("DRIVER_XAI_OK"):
            inline_payload = text.removeprefix("DRIVER_XAI_OK").strip()
            payload, error = (
                _parse_recoverable_payload([inline_payload, *lines[index + 1 :]], marker="DRIVER_XAI_OK")
                if inline_payload
                else _parse_next_json_payload(lines, index + 1)
            )
            if error is not None:
                return {"ok": False, "error": error}
            return {
                "ok": True,
                "result": payload.get("result"),
                "metadata": payload.get("metadata"),
            }
        if text.startswith("DRIVER_XAI_ERROR"):
            inline_payload = text.removeprefix("DRIVER_XAI_ERROR").lstrip(":").strip()
            payload, error = (
                _parse_recoverable_payload([inline_payload, *lines[index + 1 :]], marker="DRIVER_XAI_ERROR")
                if inline_payload
                else _parse_next_json_payload(lines, index + 1)
            )
            if error is not None:
                return {"ok": False, "error": error}
            return {
                "ok": False,
                "error": payload.get("error", payload),
            }
        if text.startswith("DRIVER_XAI_RESULT:"):
            payload, error = _parse_recoverable_payload(
                [text.removeprefix("DRIVER_XAI_RESULT:"), *lines[index + 1 :]],
                marker="DRIVER_XAI_RESULT",
            )
            if error is not None:
                return {"ok": False, "error": error}
            if isinstance(payload, dict):
                return payload
            return {
                "ok": False,
                "error": {
                    "code": "parse_error",
                    "message": "DRIVER_XAI_RESULT payload must be a JSON object.",
                    "details": {"payloadType": type(payload).__name__},
                },
            }
    return {
        "ok": False,
        "error": {
            "code": "parse_error",
            "message": "Driver xAI result marker was not found in device output.",
            "details": {"output": output},
        },
    }


def _parse_next_json_payload(lines: list[str], start_index: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if start_index >= len(lines):
        return {}, {
            "code": "parse_error",
            "message": "Expected JSON payload line after Driver xAI marker.",
            "details": {"lines": lines},
        }
    # Join progressive lines so wrapped JSON still parses.
    combined = ""
    last_decode_error = ""
    for index in range(start_index, len(lines)):
        combined = f"{combined}\n{lines[index]}".strip() if combined else lines[index]
        payload, error = _parse_json_text(combined, marker="DRIVER_XAI_PAYLOAD")
        if error is None:
            if isinstance(payload, dict):
                return payload, None
            return {}, {
                "code": "parse_error",
                "message": "Driver xAI payload must be a JSON object.",
                "details": {"payloadType": type(payload).__name__},
            }
        details = error.get("details") if isinstance(error, dict) else None
        if isinstance(details, dict):
            last_decode_error = str(details.get("decodeError", ""))
    return {}, {
        "code": "parse_error",
        "message": "Could not parse Driver xAI JSON payload.",
        "details": {
            "raw": combined,
            "decodeError": last_decode_error or None,
        },
    }


def _parse_recoverable_payload(lines: list[str], *, marker: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload, error = _parse_payload_lines(lines, marker=marker)
    if error is None:
        return payload, None

    raw = "\n".join(line for line in lines if line).strip()
    response = _extract_json_object_after_key(raw, "response")
    if isinstance(response, dict) and response.get("ok") is True:
        return {
            "ok": True,
            "metadata": None,
            "response": response,
            "result": response.get("result"),
        }, None

    result = _extract_json_object_after_key(raw, "result")
    if isinstance(result, dict):
        return {
            "ok": True,
            "metadata": None,
            "result": result,
        }, None

    return {}, error


def _parse_payload_lines(lines: list[str], *, marker: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    combined = ""
    last_decode_error = ""
    for line in lines:
        if not line:
            continue
        combined = f"{combined}\n{line}".strip() if combined else line
        payload, error = _parse_json_text(combined, marker=marker)
        if error is None:
            if isinstance(payload, dict):
                return payload, None
            return {}, {
                "code": "parse_error",
                "message": "Driver xAI payload must be a JSON object.",
                "details": {"payloadType": type(payload).__name__},
            }
        details = error.get("details") if isinstance(error, dict) else None
        if isinstance(details, dict):
            last_decode_error = str(details.get("decodeError", ""))
    return {}, {
        "code": "parse_error",
        "message": "Could not parse Driver xAI JSON payload.",
        "details": {
            "raw": combined,
            "decodeError": last_decode_error or None,
        },
    }


def _extract_json_object_after_key(text: str, key: str) -> dict[str, Any] | None:
    needle = f'"{key}"'
    start = text.find(needle)
    if start < 0:
        return None
    object_start = text.find("{", start + len(needle))
    if object_start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(object_start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[object_start : index + 1]
                try:
                    payload = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


def _parse_json_text(text: str, *, marker: str) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, {
            "code": "parse_error",
            "message": f"Invalid JSON payload for {marker}.",
            "details": {"raw": text, "decodeError": str(exc)},
        }


def _normalize_error_payload(error: Any) -> dict[str, Any]:
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        details = error.get("details")
        if isinstance(code, str) and isinstance(message, str):
            return {
                "code": code,
                "message": message,
                "details": details,
            }
        return {
            "code": "driver_runtime_error",
            "message": str(message or error),
            "details": error,
        }
    return {
        "code": "driver_runtime_error",
        "message": str(error or "Driver xAI execution failed."),
        "details": None,
    }


def _chain_progress(lines: list[str], callback: Callable[[str], None] | None) -> Callable[[str], None]:
    def _record(line: str) -> None:
        lines.append(line)
        if callback is not None:
            callback(line)

    return _record


def _setup_value(setup: dict[str, Any], section: str, key: str, default: Any) -> Any:
    nested = setup.get(section)
    if isinstance(nested, dict) and key in nested:
        return nested[key]
    if key in setup:
        return setup[key]
    return default


def _parameter_default(value: Any) -> Any:
    if isinstance(value, dict) and "default" in value:
        return value["default"]
    return value


def _validate_allowed(
    module_id: str,
    key: str,
    value: Any,
    spec: Any,
    problems: list[dict[str, Any]],
) -> None:
    if not isinstance(spec, dict):
        return
    allowed = spec.get("allowed")
    if isinstance(allowed, list) and value not in allowed:
        problems.append({
            "code": "invalid_value",
            "message": f"{module_id} {key} must be one of {allowed}.",
            "value": value,
        })
    allowed_range = spec.get("allowed_range")
    if isinstance(allowed_range, list) and len(allowed_range) == 2 and value is not None:
        low, high = allowed_range
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            problems.append({
                "code": "invalid_value",
                "message": f"{module_id} {key} must be numeric.",
                "value": value,
            })
            return
        if numeric < float(low) or numeric > float(high):
            problems.append({
                "code": "out_of_range",
                "message": f"{module_id} {key} must be between {low} and {high}.",
                "value": value,
            })


def _missing_problem(module_id: str, key: str, label: str) -> dict[str, Any]:
    return {
        "code": "missing_setup_value",
        "message": f"{module_id} requires {label}.",
        "key": key,
    }


def _gpio_constructor_source(resolved_setup: dict[str, Any]) -> str:
    kwargs = {
        **resolved_setup.get("pins", {}),
        **resolved_setup.get("options", {}),
    }
    return f"Driver({_kwargs_source(kwargs)})"


def _i2c_constructor_source(resolved_setup: dict[str, Any]) -> str:
    bus = resolved_setup.get("bus", {})
    config = dict(resolved_setup.get("config", {}))
    bus_id = _i2c_bus_id(bus.get("name"))
    address = config.get("address")
    if isinstance(address, str):
        config["address"] = int(address, 0)
    bus_source = (
        f"I2C({bus_id}, scl=Pin({int(bus['scl'])}), "
        f"sda=Pin({int(bus['sda'])}), freq={int(bus.get('frequency_hz') or 400000)})"
    )
    return f"Driver({bus_source}, {_kwargs_source(config)})"


def _i2c_bus_id(name: Any) -> int:
    text = str(name or "i2c0").lower()
    digits = "".join(char for char in text if char.isdigit())
    return int(digits or "0")


def _method_call_source(object_name: str, method_name: str, parameters: dict[str, Any]) -> str:
    return f"{object_name}.{method_name}({_kwargs_source(parameters)})"


def _kwargs_source(values: dict[str, Any]) -> str:
    return ", ".join(f"{key}={_source_literal(value)}" for key, value in values.items())


def _source_literal(value: Any) -> str:
    return repr(value)
