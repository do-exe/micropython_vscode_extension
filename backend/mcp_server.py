from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable

import micropython_backend as backend


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "micropython-vscode-extension"
SERVER_VERSION = "0.2.0"


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MicroPythonMcpServer:
    def __init__(self) -> None:
        self._stdio_mode: str | None = None
        self._session = backend.PersistentSession(
            emit_terminal_text=lambda _text: None,
            emit_session_state=lambda _payload: None,
            emit_hybrid_event=lambda _payload: None,
        )

    def serve(self) -> None:
        while True:
            message = self._read_message()
            if message is None:
                return
            response = self._handle_message(message)
            if response is not None:
                self._write_message(response)

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
        }
        tool = tools.get(name)
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
        devices = backend.list_detected_esp_ports()
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
            "guidance": (
                "Project sync used the MicroPython extension backend. "
                "Do not retry with mpremote, ampy, esptool, or raw serial unless this result says unsupported."
            ),
        }

    def _tool_run_and_test(self, arguments: dict[str, Any]) -> dict[str, Any]:
        port = self._resolve_port(arguments)
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
                "nextAction": (
                    "The MicroPython run completed. Inspect stdout for test assertions or device output."
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
                            "- Do not use `mpremote`, `ampy`, `esptool`, or raw serial directly unless a MicroPython MCP tool reports unsupported.",
                            "- If multiple devices are detected, pass the intended serial `port` argument.",
                        ]),
                    }
                ]
            }

        if uri == "micropython://device-status":
            payload = {
                "ok": True,
                "devices": backend.list_detected_esp_ports(),
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
        devices = backend.list_detected_esp_ports()
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

    def _object_param(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise McpError(-32602, "Expected object parameters.")
        return value

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
