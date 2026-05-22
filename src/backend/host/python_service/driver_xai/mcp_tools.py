from __future__ import annotations

from typing import Any, Callable

from .bundle import prepare_bundle
from .catalog import DriverXaiCatalog, DriverXaiError
from .deploy import deploy_bundle
from .execute import DEFAULT_EXECUTE_TIMEOUT_SECONDS, execute_module
from .validator import validate_catalog


class DriverXaiMcpTools:
    def has_tool(self, name: str) -> bool:
        return name in self.handlers

    def needs_device_session(self, name: str) -> bool:
        return name in {"driver_xai_deploy_bundle", "driver_xai_execute"}

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        session: Any = None,
        resolve_port: Callable[[dict[str, Any]], str] | None = None,
    ) -> dict[str, Any]:
        try:
            handler = self.handlers[name]
            return handler(arguments, session=session, resolve_port=resolve_port)
        except Exception as exc:
            response = {
                "ok": False,
                "tool": name,
                "error": str(exc),
            }
            if isinstance(exc, DriverXaiError):
                if exc.code:
                    response["code"] = exc.code
                response.update(exc.details)
            return response

    @property
    def handlers(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {
            "driver_xai_validate": self._validate,
            "driver_xai_search": self._search,
            "driver_xai_registry": self._registry,
            "driver_xai_info": self._info,
            "driver_xai_inspect": self._inspect,
            "driver_xai_get": self._get,
            "driver_xai_prepare_bundle": self._prepare_bundle,
            "driver_xai_deploy_bundle": self._deploy_bundle,
            "driver_xai_execute": self._execute,
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "driver_xai_validate",
                "description": "Validates the Driver xAI catalog structure, module identities, registries, and required files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "catalogRoot": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_search",
                "description": "Searches Driver xAI modules by module id or module name using protocol/interface registries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "catalogRoot": {"type": "string"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_registry",
                "description": "Reads Driver xAI protocol or interface registries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["protocol", "interface"]},
                        "name": {"type": "string"},
                        "catalogRoot": {"type": "string"},
                    },
                    "required": ["type"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_info",
                "description": "Reads Driver xAI module identity from info.json.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "moduleId": {"type": "string"},
                        "catalogRoot": {"type": "string"},
                    },
                    "required": ["moduleId"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_inspect",
                "description": "Lists available Driver xAI module files, drivers, examples, readable keys, and commands.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "moduleId": {"type": "string"},
                        "hasVariant": {"type": "boolean"},
                        "variantName": {"type": "string"},
                        "catalogRoot": {"type": "string"},
                    },
                    "required": ["moduleId"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_get",
                "description": "Reads Driver xAI module JSON data or driver source from the catalog.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "moduleId": {"type": "string"},
                        "hasVariant": {"type": "boolean"},
                        "variantName": {"type": "string"},
                        "source": {
                            "type": "string",
                            "description": "For JSON use info, parameters, or commands. For drivers use an exact driver filename from driver_xai_inspect, or aliases such as driver, micropython, py, c, or rust.",
                        },
                        "fileType": {"type": "string", "enum": ["json", "driver"]},
                        "key": {"type": "string", "default": "all"},
                        "catalogRoot": {"type": "string"},
                    },
                    "required": ["moduleId", "source", "fileType"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_prepare_bundle",
                "description": "Generates a mini MicroPython project folder from selected Driver xAI modules. This does not mutate the Driver xAI catalog.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "modules": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "outputDir": {"type": "string"},
                        "language": {"type": "string", "enum": ["micropython"], "default": "micropython"},
                        "parameters": {"type": "object"},
                        "includeExamples": {"type": "boolean"},
                        "catalogRoot": {"type": "string"},
                    },
                    "required": ["modules", "outputDir"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_deploy_bundle",
                "description": "Uploads a generated Driver xAI bundle to a MicroPython device using the extension backend.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string"},
                        "bundleDir": {"type": "string"},
                        "remoteRoot": {"type": "string", "default": "/"},
                        "deleteExtraneous": {"type": "boolean"},
                    },
                    "required": ["bundleDir"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "driver_xai_execute",
                "description": "Builds, deploys, and runs a Driver xAI module command on a MicroPython device through the extension backend. Returns hardware flow, resolved setup, stdout, and problem details for AI reasoning.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string"},
                        "moduleId": {"type": "string"},
                        "setup": {"type": "object"},
                        "command": {"type": "string"},
                        "parameters": {"type": "object"},
                        "catalogRoot": {"type": "string"},
                        "outputDir": {"type": "string"},
                        "remoteRoot": {"type": "string", "default": "/"},
                        "deleteExtraneous": {"type": "boolean"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600},
                        "holdMs": {"type": "integer", "minimum": 0, "maximum": 600000},
                    },
                    "required": ["moduleId", "setup", "command"],
                    "additionalProperties": False,
                },
            },
        ]

    def _catalog(self, arguments: dict[str, Any]) -> DriverXaiCatalog:
        return DriverXaiCatalog(_optional_string(arguments, "catalogRoot"))

    def _validate(self, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        return validate_catalog(_optional_string(arguments, "catalogRoot"))

    def _search(self, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        catalog = self._catalog(arguments)
        return {
            "ok": True,
            "catalogRoot": str(catalog.root),
            "matches": catalog.search(_required_string(arguments, "query")),
        }

    def _registry(self, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        catalog = self._catalog(arguments)
        return {
            "ok": True,
            "catalogRoot": str(catalog.root),
            "registry": catalog.registry(_required_string(arguments, "type"), _optional_string(arguments, "name")),
        }

    def _info(self, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        catalog = self._catalog(arguments)
        module_id = _required_string(arguments, "moduleId")
        return {
            "ok": True,
            "catalogRoot": str(catalog.root),
            "info": catalog.info(module_id),
        }

    def _inspect(self, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        catalog = self._catalog(arguments)
        return {
            "ok": True,
            "inspect": catalog.inspect(
                _required_string(arguments, "moduleId"),
                bool(arguments.get("hasVariant", False)),
                _optional_string(arguments, "variantName"),
            ),
        }

    def _get(self, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        catalog = self._catalog(arguments)
        return {
            "ok": True,
            "catalogRoot": str(catalog.root),
            "value": catalog.get(
                _required_string(arguments, "moduleId"),
                bool(arguments.get("hasVariant", False)),
                _optional_string(arguments, "variantName"),
                _required_string(arguments, "source"),
                _required_string(arguments, "fileType"),
                _optional_string(arguments, "key") or "all",
            ),
        }

    def _prepare_bundle(self, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        modules = arguments.get("modules")
        if not isinstance(modules, list) or not all(isinstance(item, str) for item in modules):
            raise ValueError("modules must be a list of module ids")
        parameters = arguments.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
        return prepare_bundle(
            modules,
            _required_string(arguments, "outputDir"),
            catalog_root=_optional_string(arguments, "catalogRoot"),
            language=_optional_string(arguments, "language") or "micropython",
            parameters=parameters,
            include_examples=bool(arguments.get("includeExamples", False)),
        )

    def _deploy_bundle(
        self,
        arguments: dict[str, Any],
        *,
        session: Any = None,
        resolve_port: Callable[[dict[str, Any]], str] | None = None,
    ) -> dict[str, Any]:
        if session is None or resolve_port is None:
            raise ValueError("driver_xai_deploy_bundle requires a MicroPython backend session")
        progress: list[str] = []
        payload = deploy_bundle(
            session,
            port=resolve_port(arguments),
            bundle_dir=_required_string(arguments, "bundleDir"),
            remote_root=_optional_string(arguments, "remoteRoot") or "/",
            delete_extraneous=bool(arguments.get("deleteExtraneous", False)),
            progress_callback=progress.append,
        )
        return {
            **payload,
            "progress": progress[-80:],
            "portReleasedAfterTool": True,
            "guidance": "Driver xAI bundle deployment used the MicroPython extension backend and released the serial port after upload.",
        }

    def _execute(
        self,
        arguments: dict[str, Any],
        *,
        session: Any = None,
        resolve_port: Callable[[dict[str, Any]], str] | None = None,
    ) -> dict[str, Any]:
        if session is None or resolve_port is None:
            raise ValueError("driver_xai_execute requires a MicroPython backend session")
        setup = arguments.get("setup")
        if not isinstance(setup, dict):
            raise ValueError("setup must be an object")
        parameters = arguments.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
        progress: list[str] = []
        timeout = _optional_number(arguments, "timeoutSeconds") or DEFAULT_EXECUTE_TIMEOUT_SECONDS
        payload = execute_module(
            session,
            port=resolve_port(arguments),
            module_id=_required_string(arguments, "moduleId"),
            setup=setup,
            command=_required_string(arguments, "command"),
            command_parameters=parameters,
            catalog_root=_optional_string(arguments, "catalogRoot"),
            output_dir=_optional_string(arguments, "outputDir"),
            remote_root=_optional_string(arguments, "remoteRoot") or "/",
            timeout_seconds=timeout,
            delete_extraneous=bool(arguments.get("deleteExtraneous", False)),
            hold_ms=int(arguments.get("holdMs") or 0),
            progress_callback=progress.append,
        )
        return {
            **payload,
            "progress": payload.get("progress", progress[-80:]),
            "portReleasedAfterTool": True,
            "guidance": (
                "Driver xAI executed through catalog metadata, generated bundle code, and the MicroPython extension backend. "
                "Use hardwareFlow and aiContext to understand wiring, assumptions, setup values, and the driver command path."
            ),
        }


def _optional_string(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = _optional_string(arguments, key)
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_number(arguments: dict[str, Any], key: str) -> float | None:
    value = arguments.get(key)
    if value is None:
        return None
    return float(value)
