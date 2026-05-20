# Development Guide

## Requirements

- Node.js and npm
- Python 3
- A MicroPython board for manual device smoke tests

## Install

```bash
npm install
```

## Build And Test

```bash
npm run compile -- --pretty false
python3 -m pytest tests/python -q
npm run py:compile
```

## Runtime Staging

```bash
npm run stage-runtime
```

Runtime staging builds the Python runtime payload used by packaged extension installs. If staging cannot find source packages, set `MICROPYTHON_SOURCE_SITE_PACKAGES` or `MICROPYTHON_SOURCE_PYENV`.

## Backend Layout

- `src/backend/host/extension`: VS Code TypeScript code that starts and talks to the backend service.
- `src/backend/host/python_service`: local Python service code that runs on the user's computer.
- `src/backend/device`: generated MicroPython snippets sent to the board.

## Package

```bash
npm run package:vsix
```

## Manual Smoke Tests

With a board connected:

- select device
- open terminal
- run non-interactive file
- run interactive file
- soft reset
- scan workspace
- read and write a mounted workspace file
- sync a local folder
- call MCP tools list and one simple tool

## Feature Contract

When changing behavior, update tests and verify the behavior still maps to [EXTENSION_FEATURES.md](../EXTENSION_FEATURES.md).
