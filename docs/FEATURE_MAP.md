# Feature Map

`EXTENSION_FEATURES.md` is the full behavior contract. This file maps major feature groups to implementation areas.

| Feature group | Implementation area |
| --- | --- |
| Device selection, polling, session state | `src/controller`, `src/backend/host/extension`, `src/backend/host/python_service/service.py` |
| REPL terminal | `src/ui/replTerminal.ts`, `src/backend/host/python_service/session.py` |
| Run and soft reset commands | `src/controller`, `src/backend/host/extension`, `src/backend/host/python_service/operations.py` |
| Workspace tree and mounted filesystem | `src/ui/workspaceView.ts`, `src/ui/workspaceFileSystemProvider.ts`, `src/controller` |
| Device filesystem operations | `src/backend/host/python_service/session.py`, `src/backend/device/micropython.py` |
| Folder sync | `src/controller`, `src/backend/host/python_service/sync_core.py`, `src/backend/device/micropython.py` |
| AI language model tools | `src/ai/commands.ts` |
| MCP adapter | `src/backend/host/python_service/mcp_server.py` |
| Runtime staging and packaging | `scripts/stage_runtime.py`, `package.json`, `.vscodeignore` |

Code not mapped here or in `EXTENSION_FEATURES.md` should be treated as suspect until proven required.
