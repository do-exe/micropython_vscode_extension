# Contributing

Thanks for helping improve MicroPython Extension.

## Before You Start

- Read [EXTENSION_FEATURES.md](EXTENSION_FEATURES.md). It is the behavior contract.
- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before moving code between layers.
- Keep public command IDs, MCP tool names, backend command names, and the `micropython` filesystem scheme compatible.

## Local Checks

Run these before opening a pull request:

```bash
npm run compile -- --pretty false
python3 -m pytest tests/python -q
npm run py:compile
```

For packaging changes, also run:

```bash
npm run package:vsix
```

## Code Guidelines

- Prefer small focused modules over large mixed-responsibility files.
- Keep backend layers separated: VS Code TypeScript in `src/backend/host/extension`, local Python host service code in `src/backend/host/python_service`, and generated device-side MicroPython snippets in `src/backend/device`.
- Remove dead code only when it is unmapped from `EXTENSION_FEATURES.md` and unreferenced.
- Keep device-facing behavior conservative; serial and filesystem operations should fail with clear errors.
- Add or update tests for backend protocol, sync planning, workspace filesystem operations, and MCP behavior.
- Do not commit local caches, generated VSIX files, virtual environments, or runtime staging temp files.

## Pull Requests

Describe:

- user-visible behavior changes
- public interface changes
- tests run
- manual device smoke tests, if relevant
