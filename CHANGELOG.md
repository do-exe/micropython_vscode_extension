# Changelog

## 0.5.0 - 2026-05-12

- Added bundled Windows x64 runtime support so the extension can run on Windows without depending on user-installed Python packages.
- Added platform-aware runtime staging and backend launch support for Linux x64, Linux arm64, Windows x64, and Windows arm64.
- Added Codex MCP config auto-registration on extension activation, plus direct Codex `config.toml` refresh support.
- Added Driver xAI module catalog support through the bundled MCP server.
- Added CLI and MCP hardware-profile support for saving connected Driver xAI modules and running their commands.
- Added Windows runtime build handoff notes for repeatable packaging.
- Trimmed bundled Linux runtime files that are not needed at extension runtime.

## 0.4.0 - 2026-05-11

- Fixed MCP device tool sessions so the serial port is released after tool calls.
- Improved reliability for agent-driven run/test workflows that need repeated device access.

## 0.3.0 - 2026-05-11

- Added native VS Code Language Model tools for MicroPython device operations.
- Added a bundled MCP stdio server with device status, project sync, and run/test tools.
- Added AI agent MCP status and configuration commands.
- Improved workspace upload behavior and empty file creation.

## 0.2.0 - 2026-05-10

- Prepared the extension for the 0.2.0 release.
- Stabilized extension metadata and packaging after the first public release.

## 0.1.0 - 2026-03-20

- First public Marketplace release of the MicroPython VS Code extension.
- Added persistent MicroPython REPL and in-extension terminal support.
- Added interactive and non-interactive MicroPython execution from the active editor.
- Added workspace sync, workspace browsing, and device file cleanup flows.
