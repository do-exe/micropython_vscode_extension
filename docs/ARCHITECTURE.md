# Architecture

This repo has one backend area under `src/backend`.

## Layers

- `src/extension.ts` activates the extension and owns only lifecycle handoff.
- `src/controller` orchestrates VS Code commands, status, device selection, workspace flows, and UI state.
- `src/ui` contains tree views, the REPL pseudoterminal, and the `micropython` filesystem provider.
- `src/backend/host/extension` is the VS Code backend host. It starts and talks to the Python service over stdio.
- `src/backend/host/python_service` is the packaged Python service and MCP adapter that runs on the user's computer.
- `src/backend/device` contains generated MicroPython snippets sent to the device.
- `src/ai` registers VS Code language model tools and Codex MCP configuration.
- `src/core` contains shared TypeScript constants and protocol/result types.

## Python Backend

The Python backend is intentionally split by responsibility:

- `__main__.py` and `cli.py`: module entrypoint and command-line dispatch.
- `service.py`: persistent stdio service used by the VS Code extension.
- `mcp_server.py`: MCP stdio adapter.
- `serial_controller.py`, `session.py`, and `operations.py`: serial controller, persistent session state, and command operations.
- `device_detection.py`: serial device discovery.
- `sync_core.py` and `sync_utils.py`: sync planning and host-side wrappers.
- `src/backend/device/micropython.py`: MicroPython source snippets sent to the device.

External command names and JSON payloads are compatibility surfaces. Keep names such as `session.open`, `run-file`, `workspace.read-file`, and `sync-folder` stable.

## Public Interfaces

Preserve:

- VS Code command IDs in `package.json`
- `micropython` filesystem URI scheme
- MCP tool names listed in `EXTENSION_FEATURES.md`
- user settings under the `micropython` namespace
- backend service command names and result shapes

## Packaging

The VSIX package includes compiled TypeScript from `out/**`, runtime files from `runtime/**`, media assets, host Python service files from `src/backend/host/python_service/**`, and device snippets from `src/backend/device/**`.
