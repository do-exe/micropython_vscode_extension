from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import base64
from typing import Any, Callable

from . import PersistentSession, list_detected_esp_ports
from . import arduino
from . import esp_idf
from .driver_xai.mcp_tools import DriverXaiMcpTools
from . import stlink
from . import stm_build


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "micropython-vscode-extension"
SERVER_VERSION = "0.4.0"
MCP_TOOL_SESSION_RELEASE_REASON = "mcp-tool-complete"


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MicroPythonMcpServer:
    def __init__(self) -> None:
        self._stdio_mode: str | None = None
        self._session = PersistentSession(
            emit_terminal_text=lambda _text: None,
            emit_session_state=lambda _payload: None,
        )
        self._driver_xai_tools = DriverXaiMcpTools()

    def serve(self) -> None:
        try:
            while True:
                message = self._read_message()
                if message is None:
                    return
                response = self._handle_message(message)
                if response is not None:
                    self._write_message(response)
        finally:
            self._release_device_session("mcp-server-stopped")

    def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = str(message.get("method", ""))
        request_id = message.get("id")

        if request_id is None:
            if method == "notifications/initialized":
                return None
            return None

        try:
            if method == "initialize":
                params = self._object_param(message.get("params"))
                client_protocol = self._optional_string(params, "protocolVersion")
                result = {
                    "protocolVersion": client_protocol or PROTOCOL_VERSION,
                    "capabilities": {
                        "resources": {"listChanged": False},
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                }
            elif method == "tools/list":
                result = {"tools": self._tools()}
            elif method == "tools/call":
                params = self._object_param(message.get("params"))
                name = str(params.get("name", ""))
                arguments = self._object_param(params.get("arguments"))
                result = self._call_tool(name, arguments)
            elif method == "resources/list":
                result = {"resources": self._resources()}
            elif method == "resources/read":
                params = self._object_param(message.get("params"))
                result = self._read_resource(str(params.get("uri", "")))
            elif method == "ping":
                result = {}
            else:
                raise McpError(-32601, f"Unsupported MCP method: {method}")

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except McpError as exc:
            return self._error_response(request_id, exc.code, exc.message)
        except Exception as exc:
            return self._error_response(request_id, -32603, str(exc))

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tools: dict[str, Callable[[dict[str, Any]], Any]] = {
            "micropython_device_status": self._tool_device_status,
            "micropython_sync_project": self._tool_sync_project,
            "micropython_run_and_test": self._tool_run_and_test,
            "micropython_filesystem": self._tool_filesystem,
            "micropython_soft_reset": self._tool_soft_reset,
            "stm_stlink_status": self._tool_stlink_status,
            "stm_stlink_flash": self._tool_stlink_flash,
            "stm_stlink_erase": self._tool_stlink_erase,
            "stm_build_firmware": self._tool_stm_build_firmware,
            "stm_build_and_flash": self._tool_stm_build_and_flash,
            "arduino_toolchain_status": self._tool_arduino_toolchain_status,
            "arduino_install_core": self._tool_arduino_install_core,
            "arduino_compile": self._tool_arduino_compile,
            "arduino_upload": self._tool_arduino_upload,
            "arduino_compile_and_upload": self._tool_arduino_compile_and_upload,
            "esp_idf_status": self._tool_esp_idf_status,
            "esp_idf_set_target": self._tool_esp_idf_set_target,
            "esp_idf_build": self._tool_esp_idf_build,
            "esp_idf_flash": self._tool_esp_idf_flash,
            "esp_idf_build_and_flash": self._tool_esp_idf_build_and_flash,
        }
        tool = tools.get(name)
        if tool is None and self._driver_xai_tools.has_tool(name):
            try:
                payload = self._driver_xai_tools.call_tool(
                    name,
                    arguments,
                    session=self._session,
                    resolve_port=self._resolve_port,
                )
            finally:
                if self._driver_xai_tools.needs_device_session(name):
                    self._release_device_session(MCP_TOOL_SESSION_RELEASE_REASON)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, indent=2, sort_keys=True),
                    }
                ],
                "isError": not bool(payload.get("ok", False)) if isinstance(payload, dict) else False,
            }
        if tool is None:
            raise McpError(-32602, f"Unknown MicroPython tool: {name}")

        payload = tool(arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, indent=2, sort_keys=True),
                }
            ],
            "isError": not bool(payload.get("ok", False)) if isinstance(payload, dict) else False,
        }

    def _tool_device_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        selected_port = self._optional_string(arguments, "port")
        devices = list_detected_esp_ports()
        selected_device = next((device for device in devices if device.get("port") == selected_port), None) if selected_port else None
        return {
            "ok": True,
            "selectedPort": selected_port,
            "selectedDevice": selected_device,
            "devices": devices,
            "guidance": (
                "Use micropython_run_and_test for upload/run/test workflows. "
                "Do not use mpremote, ampy, esptool, or raw serial directly unless this tool reports unsupported."
            ),
        }

    def _tool_sync_project(self, arguments: dict[str, Any]) -> dict[str, Any]:
        port = self._resolve_port(arguments)
        project_folder = self._resolve_project_folder(arguments)
        remote_root = self._normalize_remote_root(self._optional_string(arguments, "remoteRoot"))
        delete_extraneous = bool(arguments.get("deleteExtraneous", False))
        progress: list[str] = []

        try:
            result = self._session.sync_folder(
                port=port,
                local_folder=project_folder,
                remote_folder=remote_root,
                delete_extraneous=delete_extraneous,
                progress_callback=progress.append,
            )

            return {
                "ok": bool(result.get("ok")),
                "port": port,
                "projectFolder": project_folder,
                "remoteRoot": remote_root,
                "deleteExtraneous": delete_extraneous,
                "result": result,
                "progress": progress[-80:],
                "portReleasedAfterTool": True,
                "guidance": (
                    "Project sync used the MicroPython extension backend and released the serial port after the tool call. "
                    "Do not retry with mpremote, ampy, esptool, or raw serial unless this result says unsupported."
                ),
            }
        finally:
            self._release_device_session(MCP_TOOL_SESSION_RELEASE_REASON)

    def _tool_run_and_test(self, arguments: dict[str, Any]) -> dict[str, Any]:
        port = self._resolve_port(arguments)
        try:
            project_folder = self._resolve_project_folder(arguments, required=False)
            remote_root = self._normalize_remote_root(self._optional_string(arguments, "remoteRoot"))
            timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
            code = self._optional_string(arguments, "code", trim=False)
            local_file = self._resolve_local_file(arguments, project_folder)
            sync_project = bool(arguments.get("syncProject", bool(project_folder and code is None)))
            delete_extraneous = bool(arguments.get("deleteExtraneous", False))
            steps: list[dict[str, Any]] = []
            started_at = time.monotonic()

            if sync_project:
                if not project_folder:
                    return {
                        "ok": False,
                        "port": port,
                        "failedStep": "resolveProjectFolder",
                        "error": "syncProject was requested, but no projectFolder was provided.",
                        "steps": steps,
                        "portReleasedAfterTool": True,
                    }
                progress: list[str] = []
                sync_result = self._session.sync_folder(
                    port=port,
                    local_folder=project_folder,
                    remote_folder=remote_root,
                    delete_extraneous=delete_extraneous,
                    progress_callback=progress.append,
                )
                steps.append({
                    "step": "syncProject",
                    "ok": bool(sync_result.get("ok")),
                    "result": sync_result,
                    "progress": progress[-80:],
                })
                if not sync_result.get("ok"):
                    return {
                        "ok": False,
                        "port": port,
                        "failedStep": "syncProject",
                        "error": sync_result.get("error", "MicroPython project sync failed."),
                        "steps": steps,
                        "portReleasedAfterTool": True,
                    }

            temp_path: Path | None = None
            run_file = local_file
            try:
                if code is not None:
                    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", delete=False)
                    try:
                        handle.write(code)
                    finally:
                        handle.close()
                    temp_path = Path(handle.name)
                    run_file = str(temp_path)

                if not run_file:
                    return {
                        "ok": False,
                        "port": port,
                        "failedStep": "resolveRunFile",
                        "error": "No MicroPython file or code was provided, and no project main.py was found.",
                        "steps": steps,
                        "portReleasedAfterTool": True,
                    }

                stdout_lines: list[str] = []
                stderr_lines: list[str] = []
                run_result = self._session.run_file(
                    port=port,
                    local_file=run_file,
                    timeout_seconds=timeout,
                    stdout_line_callback=stdout_lines.append,
                    stderr_line_callback=stderr_lines.append,
                )
                steps.append({
                    "step": "run",
                    "ok": bool(run_result.get("ok")),
                    "result": run_result,
                    "stdoutLines": stdout_lines,
                    "stderrLines": stderr_lines,
                })

                return {
                    "ok": bool(run_result.get("ok")),
                    "port": port,
                    "localFile": local_file,
                    "usedInlineCode": code is not None,
                    "syncedProject": sync_project,
                    "durationMs": int((time.monotonic() - started_at) * 1000),
                    "stdout": run_result.get("output", ""),
                    "error": run_result.get("error"),
                    "steps": steps,
                    "portReleasedAfterTool": True,
                    "nextAction": (
                        "The MicroPython run completed and the serial port was released. Inspect stdout for test assertions or device output."
                        if run_result.get("ok")
                        else "Fix the reported MicroPython error, then call micropython_run_and_test again. Do not switch to mpremote, ampy, esptool, or raw serial."
                    ),
                }
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
        finally:
            self._release_device_session(MCP_TOOL_SESSION_RELEASE_REASON)

    def _tool_filesystem(self, arguments: dict[str, Any]) -> dict[str, Any]:
        port = self._resolve_port(arguments)
        operation = self._required_string(arguments, "operation")
        remote_path = self._normalize_remote_root(self._optional_string(arguments, "path") or "/")

        try:
            if operation == "list":
                result = self._session.workspace_list_directory(port=port, remote_path=remote_path)
            elif operation == "read":
                result = self._session.workspace_read_file(port=port, remote_path=remote_path)
                content_base64 = result.get("contentBase64")
                if isinstance(content_base64, str):
                    try:
                        result = {
                            **result,
                            "content": base64.b64decode(content_base64.encode("ascii")).decode("utf-8", errors="replace"),
                        }
                    except Exception:
                        pass
            elif operation == "write":
                content_base64 = self._optional_string(arguments, "contentBase64", trim=False)
                if content_base64 is None:
                    content = self._optional_string(arguments, "content", trim=False) or ""
                    content_base64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
                result = self._session.workspace_write_file(
                    port=port,
                    remote_path=remote_path,
                    content_base64=content_base64,
                    create=True,
                    overwrite=bool(arguments.get("overwrite", True)),
                )
            elif operation == "mkdir":
                result = self._session.workspace_create_directory(port=port, remote_path=remote_path)
            elif operation == "rename":
                new_path = self._normalize_remote_root(self._required_string(arguments, "newPath"))
                result = self._session.workspace_rename(
                    port=port,
                    old_path=remote_path,
                    new_path=new_path,
                    overwrite=bool(arguments.get("overwrite", False)),
                )
            elif operation == "delete":
                result = self._session.workspace_delete(
                    port=port,
                    remote_path=remote_path,
                    recursive=bool(arguments.get("recursive", True)),
                )
            elif operation == "stat":
                result = self._session.workspace_stat(port=port, remote_path=remote_path)
            else:
                raise McpError(-32602, f"Unsupported MicroPython filesystem operation: {operation}")

            return {
                "ok": bool(result.get("ok")),
                "port": port,
                "operation": operation,
                "path": remote_path,
                "result": result,
                "portReleasedAfterTool": True,
                "guidance": (
                    "Filesystem operation used the MicroPython extension backend and released the serial port after the tool call. "
                    "Do not retry with mpremote, ampy, esptool, or raw serial unless this result says unsupported."
                ),
            }
        finally:
            self._release_device_session(MCP_TOOL_SESSION_RELEASE_REASON)

    def _tool_soft_reset(self, arguments: dict[str, Any]) -> dict[str, Any]:
        port = self._resolve_port(arguments)
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 30.0

        try:
            result = self._session.soft_reset(port=port, timeout_seconds=timeout)
            return {
                "ok": bool(result.get("ok")),
                "port": port,
                "timeoutSeconds": timeout,
                "result": result,
                "portReleasedAfterTool": True,
                "guidance": "Soft reset used the MicroPython extension backend. Inspect output/promptSeen if the device still appears stuck.",
            }
        finally:
            self._release_device_session(MCP_TOOL_SESSION_RELEASE_REASON)

    def _tool_stlink_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 10.0
        return stlink.stlink_status(timeout)

    def _tool_stlink_flash(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._required_string(arguments, "target")
        firmware = self._required_string(arguments, "firmwarePath")
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 120.0
        return stlink.flash_firmware(
            target=target,
            firmware_path=firmware,
            verify=bool(arguments.get("verify", True)),
            reset=bool(arguments.get("reset", True)),
            timeout_seconds=timeout,
            interface=self._optional_string(arguments, "interface") or stlink.DEFAULT_INTERFACE_CONFIG,
            address=self._optional_string(arguments, "address") or stlink.DEFAULT_FLASH_ADDRESS,
        )

    def _tool_stlink_erase(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = self._required_string(arguments, "target")
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 60.0
        return stlink.erase_chip(
            target=target,
            timeout_seconds=timeout,
            interface=self._optional_string(arguments, "interface") or stlink.DEFAULT_INTERFACE_CONFIG,
        )

    def _tool_stm_build_firmware(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return stm_build.build_firmware(
            project_folder=self._required_string(arguments, "projectFolder"),
            target=self._optional_string(arguments, "target") or "stm32f0",
            output_dir=self._optional_string(arguments, "outputDir"),
            toolchain_path=self._optional_string(arguments, "toolchainPath"),
            clean=bool(arguments.get("clean", True)),
            optimization=self._optional_string(arguments, "optimization") or "-Os",
        )

    def _tool_stm_build_and_flash(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 120.0
        return stm_build.build_and_flash(
            project_folder=self._required_string(arguments, "projectFolder"),
            target=self._optional_string(arguments, "target") or "stm32f0",
            output_dir=self._optional_string(arguments, "outputDir"),
            toolchain_path=self._optional_string(arguments, "toolchainPath"),
            verify=bool(arguments.get("verify", True)),
            reset=bool(arguments.get("reset", True)),
            timeout_seconds=timeout,
        )

    def _tool_arduino_toolchain_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return arduino.status(toolchain_path=self._optional_string(arguments, "toolchainPath"))

    def _tool_arduino_install_core(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 600.0
        return arduino.install_core(
            self._required_string(arguments, "package"),
            toolchain_path=self._optional_string(arguments, "toolchainPath"),
            timeout_seconds=timeout,
        )

    def _tool_arduino_compile(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 600.0
        return arduino.compile_project(
            project_folder=self._required_string(arguments, "projectFolder"),
            fqbn=self._required_string(arguments, "fqbn"),
            toolchain_path=self._optional_string(arguments, "toolchainPath"),
            output_dir=self._optional_string(arguments, "outputDir"),
            timeout_seconds=timeout,
        )

    def _tool_arduino_upload(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 600.0
        return arduino.upload_project(
            project_folder=self._required_string(arguments, "projectFolder"),
            fqbn=self._required_string(arguments, "fqbn"),
            port=self._required_string(arguments, "port"),
            toolchain_path=self._optional_string(arguments, "toolchainPath"),
            timeout_seconds=timeout,
        )

    def _tool_arduino_compile_and_upload(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds"))
        if timeout <= 0:
            timeout = 600.0
        return arduino.compile_and_upload(
            project_folder=self._required_string(arguments, "projectFolder"),
            fqbn=self._required_string(arguments, "fqbn"),
            port=self._required_string(arguments, "port"),
            toolchain_path=self._optional_string(arguments, "toolchainPath"),
            output_dir=self._optional_string(arguments, "outputDir"),
            timeout_seconds=timeout,
        )

    def _tool_esp_idf_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return esp_idf.status(
            idf_path=self._optional_string(arguments, "idfPath"),
            tools_path=self._optional_string(arguments, "toolsPath"),
        )

    def _tool_esp_idf_set_target(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds")) or 900.0
        return esp_idf.set_target(
            self._required_string(arguments, "projectFolder"),
            self._optional_string(arguments, "target") or "esp32",
            idf_path=self._optional_string(arguments, "idfPath"),
            tools_path=self._optional_string(arguments, "toolsPath"),
            timeout_seconds=timeout,
        )

    def _tool_esp_idf_build(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds")) or 900.0
        return esp_idf.build(
            self._required_string(arguments, "projectFolder"),
            idf_path=self._optional_string(arguments, "idfPath"),
            tools_path=self._optional_string(arguments, "toolsPath"),
            timeout_seconds=timeout,
        )

    def _tool_esp_idf_flash(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds")) or 900.0
        return esp_idf.flash(
            self._required_string(arguments, "projectFolder"),
            self._required_string(arguments, "port"),
            idf_path=self._optional_string(arguments, "idfPath"),
            tools_path=self._optional_string(arguments, "toolsPath"),
            timeout_seconds=timeout,
        )

    def _tool_esp_idf_build_and_flash(self, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._normalize_timeout(arguments.get("timeoutSeconds")) or 900.0
        return esp_idf.build_and_flash(
            self._required_string(arguments, "projectFolder"),
            self._required_string(arguments, "port"),
            target=self._optional_string(arguments, "target"),
            idf_path=self._optional_string(arguments, "idfPath"),
            tools_path=self._optional_string(arguments, "toolsPath"),
            timeout_seconds=timeout,
        )

    def _tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "micropython_device_status",
                "description": "Checks selected and detected MicroPython devices. Use this before asking the user to install mpremote, ampy, esptool, or manually open a serial connection.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "micropython_sync_project",
                "description": "Uploads a local MicroPython project folder to the selected device using the extension backend. Prefer this over mpremote, ampy, esptool, or raw serial.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string"},
                        "projectFolder": {"type": "string"},
                        "remoteRoot": {"type": "string", "default": "/"},
                        "deleteExtraneous": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "micropython_run_and_test",
                "description": "Use this whenever the user asks to run, test, upload, or verify MicroPython code. The extension owns device detection, sync, upload, run, output capture, and error reporting. Do not use mpremote, ampy, esptool, or raw serial directly unless this tool reports unsupported.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string"},
                        "localFile": {"type": "string"},
                        "code": {"type": "string"},
                        "projectFolder": {"type": "string"},
                        "remoteRoot": {"type": "string", "default": "/"},
                        "syncProject": {"type": "boolean"},
                        "deleteExtraneous": {"type": "boolean"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "micropython_filesystem",
                "description": "Use this for MicroPython device filesystem operations: list, read, write, mkdir, rename, delete, and stat. Prefer this over generating ad hoc os/uos filesystem code.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": ["list", "read", "write", "mkdir", "rename", "delete", "stat"],
                        },
                        "path": {"type": "string"},
                        "newPath": {"type": "string"},
                        "content": {"type": "string"},
                        "contentBase64": {"type": "string"},
                        "recursive": {"type": "boolean"},
                        "overwrite": {"type": "boolean"},
                    },
                    "required": ["operation"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "micropython_soft_reset",
                "description": "Soft resets the selected MicroPython device through the extension backend. Use after stuck loops, dirty REPL state, or before a clean verification run.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 30},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "stm_stlink_status",
                "description": "Checks bundled/system ST-Link and OpenOCD tooling and probes attached ST-Link hardware. Use before STM32 erase or flash workflows.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 10},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "stm_stlink_flash",
                "description": "Programs an STM32 firmware image through ST-Link using OpenOCD. Accepts .bin, .hex, or .elf firmware and a target family such as stm32f4.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "STM32 family key such as stm32f1, stm32f4, stm32g0, stm32h7, or an OpenOCD target/*.cfg path.",
                        },
                        "firmwarePath": {"type": "string"},
                        "interface": {"type": "string", "default": "interface/stlink.cfg"},
                        "address": {"type": "string", "default": "0x08000000"},
                        "verify": {"type": "boolean", "default": True},
                        "reset": {"type": "boolean", "default": True},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 120},
                    },
                    "required": ["target", "firmwarePath"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "stm_stlink_erase",
                "description": "Erases STM32 flash through ST-Link using OpenOCD without requiring a serial MicroPython port.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "STM32 family key such as stm32f1, stm32f4, stm32g0, stm32h7, or an OpenOCD target/*.cfg path.",
                        },
                        "interface": {"type": "string", "default": "interface/stlink.cfg"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 60},
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "stm_build_firmware",
                "description": "Builds a local STM32 firmware project with the ignored local ARM toolchain under toolchain/arm-none-eabi. Produces .elf, .bin, and .map artifacts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "target": {"type": "string", "default": "stm32f0"},
                        "outputDir": {"type": "string"},
                        "toolchainPath": {"type": "string"},
                        "clean": {"type": "boolean", "default": True},
                        "optimization": {"type": "string", "default": "-Os"},
                    },
                    "required": ["projectFolder"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "stm_build_and_flash",
                "description": "Builds a local STM32 firmware project, then flashes the generated binary through ST-Link/OpenOCD.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "target": {"type": "string", "default": "stm32f0"},
                        "outputDir": {"type": "string"},
                        "toolchainPath": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                        "reset": {"type": "boolean", "default": True},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 120},
                    },
                    "required": ["projectFolder"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "arduino_toolchain_status",
                "description": "Checks local Arduino CLI installation under toolchain/arduino without using system-wide Arduino tools.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "toolchainPath": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "arduino_install_core",
                "description": "Installs an Arduino core package into the local ignored Arduino toolchain cache, for example arduino:avr.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "package": {"type": "string"},
                        "toolchainPath": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["package"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "arduino_compile",
                "description": "Compiles an Arduino sketch using local arduino-cli and an FQBN such as arduino:avr:uno.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "fqbn": {"type": "string"},
                        "toolchainPath": {"type": "string"},
                        "outputDir": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["projectFolder", "fqbn"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "arduino_upload",
                "description": "Uploads a compiled Arduino sketch using local arduino-cli, an FQBN, and a serial port.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "fqbn": {"type": "string"},
                        "port": {"type": "string"},
                        "toolchainPath": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["projectFolder", "fqbn", "port"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "arduino_compile_and_upload",
                "description": "Compiles and uploads an Arduino sketch using local arduino-cli.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "fqbn": {"type": "string"},
                        "port": {"type": "string"},
                        "toolchainPath": {"type": "string"},
                        "outputDir": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["projectFolder", "fqbn", "port"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "esp_idf_status",
                "description": "Checks the extension-local ESP-IDF copy under toolchain/esp-idf and tools under toolchain/espressif.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "idfPath": {"type": "string"},
                        "toolsPath": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "esp_idf_set_target",
                "description": "Runs idf.py set-target for an ESP-IDF project, defaulting to esp32.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "target": {"type": "string", "default": "esp32"},
                        "idfPath": {"type": "string"},
                        "toolsPath": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["projectFolder"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "esp_idf_build",
                "description": "Builds an ESP-IDF project using the extension-local ESP-IDF toolchain.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "idfPath": {"type": "string"},
                        "toolsPath": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["projectFolder"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "esp_idf_flash",
                "description": "Flashes an already-built ESP-IDF project to a serial port.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "port": {"type": "string"},
                        "idfPath": {"type": "string"},
                        "toolsPath": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["projectFolder", "port"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "esp_idf_build_and_flash",
                "description": "Builds and flashes an ESP-IDF project using the extension-local ESP-IDF toolchain.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectFolder": {"type": "string"},
                        "port": {"type": "string"},
                        "target": {"type": "string", "default": "esp32"},
                        "idfPath": {"type": "string"},
                        "toolsPath": {"type": "string"},
                        "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 600, "default": 600},
                    },
                    "required": ["projectFolder", "port"],
                    "additionalProperties": False,
                },
            },
            *self._driver_xai_tools.tool_definitions(),
        ]

    def _resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": "micropython://agent-guide",
                "name": "MicroPython Agent Guide",
                "description": "How AI agents should run, upload, and test MicroPython code through this extension.",
                "mimeType": "text/markdown",
            },
            {
                "uri": "micropython://device-status",
                "name": "MicroPython Device Status",
                "description": "Currently detected MicroPython serial devices.",
                "mimeType": "application/json",
            },
        ]

    def _read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "micropython://agent-guide":
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/markdown",
                        "text": "\n".join([
                            "# MicroPython Agent Guide",
                            "",
                            "Use the MicroPython MCP tools provided by this extension for device workflows.",
                            "",
                            "- Call `micropython_device_status` before selecting a device strategy.",
                            "- Call `micropython_sync_project` to upload a project folder.",
                            "- Call `micropython_run_and_test` to sync, run, capture output, and report errors.",
                            "- Call `micropython_filesystem` for device file/folder list, read, write, mkdir, rename, delete, and stat operations.",
                            "- Call `micropython_soft_reset` after stuck loops, dirty REPL state, or before a clean verification run.",
                            "- MCP tools release the serial port after each sync/run call so the normal extension UI can use it next.",
                            "- Do not use `mpremote`, `ampy`, `esptool`, or raw serial directly unless a MicroPython MCP tool reports unsupported.",
                            "- If the port is busy, wait for the active tool/UI operation to finish or close the MicroPython terminal session.",
                            "- If multiple devices are detected, pass the intended serial `port` argument.",
                        ]),
                    }
                ]
            }

        if uri == "micropython://device-status":
            payload = {
                "ok": True,
                "devices": list_detected_esp_ports(),
            }
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, indent=2, sort_keys=True),
                    }
                ]
            }

        raise McpError(-32602, f"Unknown MicroPython resource: {uri}")

    def _resolve_port(self, arguments: dict[str, Any]) -> str:
        explicit = self._optional_string(arguments, "port")
        if explicit:
            return explicit
        devices = list_detected_esp_ports()
        if len(devices) == 1 and devices[0].get("port"):
            return str(devices[0]["port"])
        if len(devices) > 1:
            ports = ", ".join(str(device.get("port", "")) for device in devices)
            raise McpError(-32602, f"Multiple MicroPython devices detected ({ports}). Pass the intended port.")
        raise McpError(-32602, "No MicroPython device detected. Connect a device and try again.")

    def _resolve_project_folder(self, arguments: dict[str, Any], required: bool = True) -> str:
        value = self._optional_string(arguments, "projectFolder")
        if value:
            return str(Path(value).expanduser().resolve())

        local_file = self._optional_string(arguments, "localFile")
        if local_file:
            return str(Path(local_file).expanduser().resolve().parent)

        if required:
            raise McpError(-32602, "projectFolder is required for this MCP tool.")
        return ""

    def _resolve_local_file(self, arguments: dict[str, Any], project_folder: str | None) -> str | None:
        local_file = self._optional_string(arguments, "localFile")
        if local_file:
            return str(Path(local_file).expanduser().resolve())
        if project_folder:
            main_file = Path(project_folder) / "main.py"
            if main_file.is_file():
                return str(main_file.resolve())
        return None

    def _normalize_remote_root(self, value: str | None) -> str:
        text = (value or "/").strip().replace("\\", "/")
        if not text or text == ".":
            return "/"
        normalized = os.path.normpath(text).replace("\\", "/")
        if normalized == ".":
            return "/"
        return normalized if normalized.startswith("/") else f"/{normalized}"

    def _normalize_timeout(self, value: Any) -> float:
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(600.0, timeout))

    def _optional_string(self, arguments: dict[str, Any], key: str, trim: bool = True) -> str | None:
        value = arguments.get(key)
        if value is None:
            return None
        text = str(value)
        if trim:
            text = text.strip()
        return text if text else None

    def _required_string(self, arguments: dict[str, Any], key: str) -> str:
        value = self._optional_string(arguments, key)
        if value is None:
            raise McpError(-32602, f"{key} is required.")
        return value

    def _object_param(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise McpError(-32602, "Expected object parameters.")
        return value

    def _release_device_session(self, reason: str) -> None:
        try:
            self._session.close(emit_event=False, reason=reason)
        except Exception:
            pass

    def _read_message(self) -> dict[str, Any] | None:
        if self._stdio_mode == "lines":
            return self._read_line_message()
        if self._stdio_mode == "headers":
            return self._read_header_message()

        first = self._read_non_whitespace_byte()
        if not first:
            return None

        if first == b"{":
            self._stdio_mode = "lines"
            return self._read_line_message(first)

        self._stdio_mode = "headers"
        return self._read_header_message(first)

    def _read_non_whitespace_byte(self) -> bytes:
        while True:
            char = sys.stdin.buffer.read(1)
            if not char or char not in b" \t\r\n":
                return char

    def _read_line_message(self, first: bytes = b"") -> dict[str, Any] | None:
        line = first + sys.stdin.buffer.readline()
        if not line:
            return None
        parsed = json.loads(line.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise McpError(-32700, "MCP message must be a JSON object.")
        return parsed

    def _read_header_message(self, first: bytes = b"") -> dict[str, Any] | None:
        header_bytes = bytearray()
        header_bytes.extend(first)
        while True:
            char = sys.stdin.buffer.read(1)
            if not char:
                return None
            header_bytes.extend(char)
            if header_bytes.endswith(b"\r\n\r\n"):
                break
            if header_bytes.endswith(b"\n\n"):
                break

        header_text = header_bytes.decode("ascii", errors="replace")
        content_length = 0
        for line in header_text.replace("\r\n", "\n").split("\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break
        if content_length <= 0:
            raise McpError(-32700, "Missing MCP Content-Length header.")

        body = sys.stdin.buffer.read(content_length)
        if len(body) != content_length:
            return None
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise McpError(-32700, "MCP message must be a JSON object.")
        return parsed

    def _write_message(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self._stdio_mode == "lines":
            sys.stdout.buffer.write(body)
            sys.stdout.buffer.write(b"\n")
        else:
            sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
            sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }


def main() -> None:
    MicroPythonMcpServer().serve()


if __name__ == "__main__":
    main()
