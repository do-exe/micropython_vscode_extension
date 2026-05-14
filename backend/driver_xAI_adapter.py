from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DriverXAIError(Exception):
    pass


HARDWARE_CONFIG_ENV = "MICROPYTHON_DRIVER_XAI_CONFIG"
HARDWARE_PROFILE_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_identifier(value: str, *, label: str = "identifier") -> str:
    text = re.sub(r"\s+", "_", str(value).strip().lower())
    text = re.sub(r"[^a-z0-9_.-]", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    if not text:
        raise DriverXAIError(f"{label} is required.")
    return text


class DriverXAIAdapter:
    def __init__(self, root: str | Path | None = None, config_path: str | Path | None = None) -> None:
        extension_root = Path(__file__).resolve().parents[1]
        self.root = Path(root).expanduser().resolve() if root else extension_root / "driver_xAI"
        self.modules_root = self.root / "modules"
        self.config_path = self._resolve_config_path(config_path, extension_root)

    def catalog(self, module_type: str | None = None) -> dict[str, Any]:
        modules = self.list_modules()
        if module_type:
            normalized = normalize_identifier(module_type, label="moduleType")
            module = next((entry for entry in modules if entry["type"] == normalized), None)
            if module is None:
                raise DriverXAIError(f"Unknown Driver xAI module type: {normalized}")
            return {"ok": True, "driverXAIRoot": str(self.root), "modules": [module]}
        return {"ok": True, "driverXAIRoot": str(self.root), "modules": modules}

    def list_modules(self) -> list[dict[str, Any]]:
        if not self.modules_root.is_dir():
            raise DriverXAIError(f"Driver xAI modules folder not found: {self.modules_root}")

        modules: list[dict[str, Any]] = []
        for module_dir in sorted(path for path in self.modules_root.iterdir() if path.is_dir()):
            if module_dir.name.startswith("."):
                continue
            modules.append(self.module_info(module_dir.name))
        return modules

    def module_info(self, module_type: str) -> dict[str, Any]:
        normalized = normalize_identifier(module_type, label="moduleType")
        module_dir = self.modules_root / normalized
        if not module_dir.is_dir():
            raise DriverXAIError(f"Unknown Driver xAI module type: {normalized}")

        metadata = self._load_optional_json(module_dir / "info.json")
        if metadata is None:
            metadata = self._load_optional_json(module_dir / "base.json") or {}

        setup_template = self._load_optional_json(module_dir / "setup.template.json") or {}
        commands_payload = self._load_optional_json(module_dir / "commands.json") or {"commands": []}
        commands = commands_payload.get("commands", [])
        if not isinstance(commands, list):
            commands = []

        driver_rel = self._driver_relative_path(metadata)
        driver_path = (module_dir / driver_rel).resolve()

        return {
            "type": normalized,
            "name": metadata.get("name", normalized),
            "displayName": metadata.get("display_name", normalized),
            "version": metadata.get("version"),
            "contractVersion": metadata.get("contract_version"),
            "description": metadata.get("description") or metadata.get("summary"),
            "protocols": metadata.get("protocols", []),
            "interfaces": metadata.get("interfaces", []),
            "fixedInterface": metadata.get("fixed_interface", []),
            "setupTemplate": setup_template,
            "commands": commands,
            "modulePath": str(module_dir),
            "driverPath": str(driver_path) if driver_path.is_file() else None,
        }

    def validate_instance(
        self,
        *,
        module_id: str,
        module_type: str,
        pins: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        info = self.module_info(module_type)
        setup_template = info.get("setupTemplate", {})
        setup_fields = self._validate_setup_fields(setup_template, pins or {}, options or {})
        now = utc_now()
        return {
            "id": normalize_identifier(module_id, label="moduleId"),
            "type": info["type"],
            "displayName": info.get("displayName"),
            **setup_fields,
            "createdAt": now,
            "updatedAt": now,
        }

    def hardware_catalog(self, module_id: str | None = None) -> dict[str, Any]:
        profile = self.load_hardware_profile()
        hardware = profile.get("hardware", [])
        if module_id:
            normalized = normalize_identifier(module_id, label="moduleId")
            hardware = [entry for entry in hardware if entry.get("id") == normalized]
            if not hardware:
                raise DriverXAIError(f"Unknown configured hardware module: {normalized}")

        return {
            "ok": True,
            "configPath": str(self.config_path),
            "version": profile.get("version", HARDWARE_PROFILE_VERSION),
            "updatedAt": profile.get("updatedAt"),
            "hardware": [self.describe_hardware_instance(entry) for entry in hardware],
        }

    def load_hardware_profile(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            return {
                "version": HARDWARE_PROFILE_VERSION,
                "updatedAt": None,
                "hardware": [],
            }

        try:
            parsed = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DriverXAIError(f"Invalid hardware config JSON in {self.config_path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise DriverXAIError(f"Expected JSON object in hardware config: {self.config_path}")

        raw_hardware = parsed.get("hardware", parsed.get("peripherals", []))
        if not isinstance(raw_hardware, list):
            raise DriverXAIError("Hardware config field 'hardware' must be a list.")

        hardware = [self._normalize_saved_instance(entry) for entry in raw_hardware]
        return {
            "version": int(parsed.get("version", HARDWARE_PROFILE_VERSION)),
            "updatedAt": parsed.get("updatedAt"),
            "hardware": hardware,
        }

    def save_hardware_profile(self, hardware: list[dict[str, Any]]) -> dict[str, Any]:
        profile = {
            "version": HARDWARE_PROFILE_VERSION,
            "updatedAt": utc_now(),
            "hardware": hardware,
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return profile

    def add_hardware(
        self,
        *,
        module_id: str,
        module_type: str,
        pins: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        replace: bool = True,
    ) -> dict[str, Any]:
        profile = self.load_hardware_profile()
        hardware = list(profile.get("hardware", []))
        instance = self.validate_instance(
            module_id=module_id,
            module_type=module_type,
            pins=pins,
            options=options,
        )
        existing_index = next((index for index, entry in enumerate(hardware) if entry.get("id") == instance["id"]), None)
        if existing_index is not None:
            if not replace:
                raise DriverXAIError(f"Hardware module already configured: {instance['id']}")
            instance["createdAt"] = hardware[existing_index].get("createdAt", instance["createdAt"])
            hardware[existing_index] = instance
        else:
            hardware.append(instance)

        saved = self.save_hardware_profile(hardware)
        return {
            "ok": True,
            "action": "add" if existing_index is None else "update",
            "configPath": str(self.config_path),
            "hardware": self.describe_hardware_instance(instance),
            "profile": {
                "version": saved["version"],
                "updatedAt": saved["updatedAt"],
                "count": len(saved["hardware"]),
            },
        }

    def remove_hardware(self, module_id: str) -> dict[str, Any]:
        normalized = normalize_identifier(module_id, label="moduleId")
        profile = self.load_hardware_profile()
        hardware = list(profile.get("hardware", []))
        remaining = [entry for entry in hardware if entry.get("id") != normalized]
        if len(remaining) == len(hardware):
            raise DriverXAIError(f"Unknown configured hardware module: {normalized}")

        saved = self.save_hardware_profile(remaining)
        return {
            "ok": True,
            "action": "remove",
            "moduleId": normalized,
            "configPath": str(self.config_path),
            "profile": {
                "version": saved["version"],
                "updatedAt": saved["updatedAt"],
                "count": len(saved["hardware"]),
            },
        }

    def get_hardware_instance(self, module_id: str) -> dict[str, Any]:
        normalized = normalize_identifier(module_id, label="moduleId")
        profile = self.load_hardware_profile()
        for entry in profile.get("hardware", []):
            if entry.get("id") == normalized:
                return entry
        raise DriverXAIError(f"Unknown configured hardware module: {normalized}")

    def describe_hardware_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_saved_instance(instance)
        info = self.module_info(str(normalized["type"]))
        return {
            **normalized,
            "displayName": normalized.get("displayName") or info.get("displayName"),
            "description": info.get("description"),
            "commands": info.get("commands", []),
            "setupTemplate": info.get("setupTemplate", {}),
        }

    def generate_run_code(self, project: dict[str, Any], peripheral: dict[str, Any], command: str, inputs: dict[str, Any]) -> str:
        module_type = str(peripheral.get("type", ""))
        info = self.module_info(module_type)
        if not info.get("driverPath"):
            raise DriverXAIError(f"Driver xAI module {module_type} does not provide a MicroPython driver.")

        command_name = str(command).strip()
        command_names = {str(item.get("name", "")) for item in info.get("commands", []) if isinstance(item, dict)}
        if command_name not in command_names:
            raise DriverXAIError(f"Unsupported command for {module_type}: {command_name}")

        setup_template = info.get("setupTemplate", {})
        setup_kind = self._setup_kind(setup_template)

        base_source = (self.modules_root / "base.py").read_text(encoding="utf-8")
        driver_source = Path(str(info["driverPath"])).read_text(encoding="utf-8")
        driver_source = self._strip_module_base_import(driver_source)

        setup_kwargs = self._setup_kwargs(setup_template, peripheral)
        safe_inputs = self._object(inputs)
        payload = {
            "project": project.get("name"),
            "moduleId": peripheral.get("id"),
            "moduleType": module_type,
            "command": command_name,
            "setup": setup_kwargs,
            "inputs": safe_inputs,
        }

        return "\n".join([
            "import json",
            "",
            "PERIPHERAL_RESULT_PREFIX = '__MICROPYTHON_PERIPHERAL_RESULT__'",
            "",
            "# Driver xAI ModuleBase",
            base_source,
            "",
            "# Driver xAI module driver",
            driver_source,
            "",
            f"_payload = json.loads({json.dumps(json.dumps(payload, sort_keys=True))})",
            "",
            "try:",
            *self._driver_init_lines(setup_kind),
            "    _result = _driver.run(_payload['command'], **_payload['inputs'])",
            "    print(PERIPHERAL_RESULT_PREFIX + json.dumps({",
            "        'ok': True,",
            "        'project': _payload['project'],",
            "        'moduleId': _payload['moduleId'],",
            "        'moduleType': _payload['moduleType'],",
            "        'command': _payload['command'],",
            "        'result': _result,",
            "    }))",
            "except Exception as exc:",
            "    print(PERIPHERAL_RESULT_PREFIX + json.dumps({",
            "        'ok': False,",
            "        'project': _payload.get('project'),",
            "        'moduleId': _payload.get('moduleId'),",
            "        'moduleType': _payload.get('moduleType'),",
            "        'command': _payload.get('command'),",
            "        'errorType': type(exc).__name__,",
            "        'error': str(exc),",
            "    }))",
            "",
        ])

    def parse_peripheral_result(self, output: str) -> dict[str, Any] | None:
        prefix = "__MICROPYTHON_PERIPHERAL_RESULT__"
        for line in str(output or "").splitlines():
            if prefix not in line:
                continue
            payload = line.split(prefix, 1)[1].strip()
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _driver_relative_path(self, metadata: dict[str, Any]) -> str:
        files = metadata.get("files")
        if isinstance(files, dict) and isinstance(files.get("driver"), str):
            return files["driver"]
        drivers = metadata.get("drivers")
        if isinstance(drivers, dict) and isinstance(drivers.get("micropython"), str):
            return drivers["micropython"]
        return "driver.py"

    def _validate_setup_fields(
        self,
        setup_template: dict[str, Any],
        pins: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        setup_kind = self._setup_kind(setup_template)
        if setup_kind == "gpio":
            return {
                "pins": self._validate_pins(setup_template, pins),
                "options": self._validate_options(setup_template, options),
            }
        if setup_kind == "i2c":
            return {
                "protocol": "i2c",
                "pins": self._validate_i2c_pins(setup_template, pins),
                "options": self._validate_i2c_options(setup_template, options),
            }
        raise DriverXAIError(f"Unsupported Driver xAI setup template for {setup_template.get('module', 'module')}.")

    def _setup_kind(self, setup_template: dict[str, Any]) -> str:
        if setup_template.get("interface") == "gpio":
            return "gpio"
        if setup_template.get("protocol") == "i2c":
            return "i2c"
        return ""

    def _setup_kwargs(self, setup_template: dict[str, Any], peripheral: dict[str, Any]) -> dict[str, Any]:
        setup_kind = self._setup_kind(setup_template)
        pins = self._object(peripheral.get("pins"))
        options = self._object(peripheral.get("options"))
        if setup_kind == "gpio":
            return {
                **self._validate_pins(setup_template, pins),
                **self._validate_options(setup_template, options),
            }
        if setup_kind == "i2c":
            normalized_pins = self._validate_i2c_pins(setup_template, pins)
            normalized_options = self._validate_i2c_options(setup_template, options)
            return {
                **normalized_pins,
                **normalized_options,
                "bus_id": self._i2c_bus_id(str(normalized_options["bus"])),
            }
        raise DriverXAIError(f"Unsupported Driver xAI setup template for {setup_template.get('module', 'module')}.")

    def _driver_init_lines(self, setup_kind: str) -> list[str]:
        if setup_kind == "gpio":
            return ["    _driver = Driver(**_payload['setup'])"]
        if setup_kind == "i2c":
            return [
                "    from machine import Pin, I2C",
                "    _setup = _payload['setup']",
                "    _i2c = I2C(int(_setup['bus_id']), sda=Pin(int(_setup['sda'])), scl=Pin(int(_setup['scl'])), freq=int(_setup['frequency_hz']))",
                "    _driver = Driver(_i2c, address=int(_setup['address']), gain=_setup['gain'], data_rate_sps=int(_setup['data_rate_sps']))",
            ]
        raise DriverXAIError("Unsupported Driver xAI setup kind.")

    def _validate_pins(self, setup_template: dict[str, Any], pins: dict[str, Any]) -> dict[str, int]:
        pin_template = setup_template.get("pins")
        if not isinstance(pin_template, dict):
            return {}

        normalized: dict[str, int] = {}
        for name, default in pin_template.items():
            value = pins.get(name, default)
            if value is None:
                raise DriverXAIError(f"Pin '{name}' is required for {setup_template.get('module', 'module')}.")
            normalized[name] = self._as_int(value, f"pin '{name}'")

        unknown = sorted(set(pins) - set(pin_template))
        if unknown:
            raise DriverXAIError(f"Unknown pin name(s): {', '.join(unknown)}")
        return normalized

    def _validate_options(self, setup_template: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        option_template = setup_template.get("options")
        if not isinstance(option_template, dict):
            return {}

        normalized: dict[str, Any] = {}
        for name, schema in option_template.items():
            schema_obj = schema if isinstance(schema, dict) else {}
            value = options.get(name, schema_obj.get("default"))
            if "allowed" in schema_obj and value not in schema_obj["allowed"]:
                allowed = ", ".join(str(item) for item in schema_obj["allowed"])
                raise DriverXAIError(f"Option '{name}' must be one of: {allowed}")
            if "allowed_range" in schema_obj:
                lower, upper = schema_obj["allowed_range"]
                number = self._as_int(value, f"option '{name}'")
                if number < int(lower) or number > int(upper):
                    raise DriverXAIError(f"Option '{name}' must be between {lower} and {upper}.")
                value = number
            normalized[name] = value

        unknown = sorted(set(options) - set(option_template))
        if unknown:
            raise DriverXAIError(f"Unknown option name(s): {', '.join(unknown)}")
        return normalized

    def _validate_i2c_pins(self, setup_template: dict[str, Any], pins: dict[str, Any]) -> dict[str, int]:
        bus_template = setup_template.get("bus")
        if not isinstance(bus_template, dict):
            raise DriverXAIError(f"I2C bus template is required for {setup_template.get('module', 'module')}.")

        normalized: dict[str, int] = {}
        for name in ("sda", "scl"):
            value = pins.get(name, bus_template.get(name))
            if value is None:
                raise DriverXAIError(f"Pin '{name}' is required for {setup_template.get('module', 'module')}.")
            normalized[name] = self._as_int(value, f"pin '{name}'")

        unknown = sorted(set(pins) - {"sda", "scl"})
        if unknown:
            raise DriverXAIError(f"Unknown I2C pin name(s): {', '.join(unknown)}")
        return normalized

    def _validate_i2c_options(self, setup_template: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        bus_template = setup_template.get("bus") if isinstance(setup_template.get("bus"), dict) else {}
        config_template = setup_template.get("config") if isinstance(setup_template.get("config"), dict) else {}
        allowed = {"address", "bus", "bus_name", "frequency_hz", "gain", "data_rate_sps", "mode"}
        unknown = sorted(set(options) - allowed)
        if unknown:
            raise DriverXAIError(f"Unknown I2C option name(s): {', '.join(unknown)}")

        bus_name = options.get("bus", options.get("bus_name", bus_template.get("name", "i2c0")))
        normalized = {
            "address": self._as_int_auto(options.get("address", setup_template.get("address", 0x48)), "option 'address'"),
            "bus": str(bus_name),
            "frequency_hz": self._as_int(options.get("frequency_hz", bus_template.get("frequency_hz", 400000)), "option 'frequency_hz'"),
            "gain": options.get("gain", config_template.get("gain", 1)),
            "data_rate_sps": self._as_int(options.get("data_rate_sps", config_template.get("data_rate_sps", 128)), "option 'data_rate_sps'"),
            "mode": options.get("mode", config_template.get("mode", "single_shot")),
        }
        if normalized["frequency_hz"] <= 0:
            raise DriverXAIError("Option 'frequency_hz' must be greater than 0.")
        return normalized

    def _i2c_bus_id(self, bus_name: str) -> int:
        text = str(bus_name).strip().lower()
        match = re.search(r"(\d+)$", text)
        if match:
            return int(match.group(1))
        return self._as_int_auto(text, "option 'bus'")

    def _as_int(self, value: Any, label: str) -> int:
        if isinstance(value, bool):
            raise DriverXAIError(f"{label} must be an integer.")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise DriverXAIError(f"{label} must be an integer.") from exc

    def _as_int_auto(self, value: Any, label: str) -> int:
        if isinstance(value, bool):
            raise DriverXAIError(f"{label} must be an integer.")
        try:
            if isinstance(value, str):
                return int(value, 0)
            return int(value)
        except (TypeError, ValueError) as exc:
            raise DriverXAIError(f"{label} must be an integer.") from exc

    def _object(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise DriverXAIError("Expected an object.")
        return value

    def _load_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DriverXAIError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise DriverXAIError(f"Expected JSON object in {path}")
        return parsed

    def _strip_module_base_import(self, source: str) -> str:
        pattern = (
            "try:\n"
            "    from modules.base import ModuleBase\n"
            "except ImportError:\n"
            "    from base import ModuleBase\n\n"
        )
        return source.replace(pattern, "")

    def _normalize_saved_instance(self, entry: Any) -> dict[str, Any]:
        if not isinstance(entry, dict):
            raise DriverXAIError("Hardware config entries must be objects.")
        module_id = normalize_identifier(entry.get("id") or "", label="moduleId")
        module_type = normalize_identifier(entry.get("type") or "", label="moduleType")
        info = self.module_info(module_type)
        setup_template = info.get("setupTemplate", {})
        setup_fields = self._validate_setup_fields(
            setup_template,
            self._object(entry.get("pins")),
            self._object(entry.get("options")),
        )
        return {
            "id": module_id,
            "type": info["type"],
            "displayName": entry.get("displayName") or info.get("displayName"),
            **setup_fields,
            "createdAt": entry.get("createdAt") or utc_now(),
            "updatedAt": entry.get("updatedAt") or utc_now(),
        }

    def _resolve_config_path(self, config_path: str | Path | None, extension_root: Path) -> Path:
        explicit = config_path or os.environ.get(HARDWARE_CONFIG_ENV)
        if explicit:
            return Path(explicit).expanduser().resolve()
        return (extension_root / ".micropython" / "driver_xAI_hardware.json").resolve()
