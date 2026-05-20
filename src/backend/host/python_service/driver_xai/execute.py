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
                "durationMs": int((time.monotonic() - started_at) * 1000),
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

        return {
            "ok": bool(run_result.get("ok")) and parsed.get("ok", False),
            "failedStep": None if bool(run_result.get("ok")) and parsed.get("ok", False) else "run",
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
            "durationMs": int((time.monotonic() - started_at) * 1000),
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
        "    print('DRIVER_XAI_OK')",
        "    print(json.dumps({'metadata': METADATA, 'result': response.get('result'), 'response': response}))",
        f"    HOLD_MS = {max(0, int(hold_ms))}",
        "    if HOLD_MS:",
        "        time.sleep_ms(HOLD_MS)",
        "except Exception as exc:",
        "    print('DRIVER_XAI_ERROR')",
        "    print(json.dumps({'metadata': METADATA, 'error': repr(exc)}))",
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
        if text == "DRIVER_XAI_OK":
            payload = json.loads(lines[index + 1]) if index + 1 < len(lines) else {}
            return {
                "ok": True,
                "result": payload.get("result"),
                "metadata": payload.get("metadata"),
            }
        if text == "DRIVER_XAI_ERROR":
            payload = json.loads(lines[index + 1]) if index + 1 < len(lines) else {}
            return {
                "ok": False,
                "error": payload,
            }
        if text.startswith("DRIVER_XAI_RESULT:"):
            return json.loads(text.removeprefix("DRIVER_XAI_RESULT:"))
        if text.startswith("DRIVER_XAI_ERROR:"):
            payload = json.loads(text.removeprefix("DRIVER_XAI_ERROR:"))
            return {
                "ok": False,
                "error": payload,
            }
    return {
        "ok": False,
        "error": {
            "message": "Driver xAI result marker was not found in device output.",
            "output": output,
        },
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
