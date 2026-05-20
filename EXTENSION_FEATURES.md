# MicroPython Extension — Features (Complete List)

1. **AI-assisted MicroPython device bridge** (connects AI workflows to a MicroPython microcontroller runtime).
2. **Select Device** (quick-pick of detected MicroPython boards/serial ports).
3. **Soft Reset Device** (restart MicroPython runtime on the selected board without unplugging).
4. **Run Current File — Non-Interactive** (runs active `.py` and captures output in VS Code Output panel).
5. **Run Current File — Interactive** (runs active `.py` while keeping REPL terminal attached for input).
6. **Open MicroPython REPL Terminal** (creates/presents a persistent REPL terminal inside VS Code).
7. **Persistent active device selection** (remembers selected port in extension global state).
8. **Auto-detect device scanning loop** (polls for connected MicroPython devices periodically).
9. **On-demand session opening** (opens serial session only when needed; maintains status).
10. **Optional auto-connect on detect** (`micropython.autoConnectOnDetect`).
11. **Optional auto-scan workspace on connect actions** (`micropython.autoScanWorkspace`).
12. **Graceful disconnect handling** (detects device disconnect, aborts reader/session activity, clears selection).
13. **Session & operation concurrency safety** (blocks/settles when run/reset/terminal operations are in-flight).
14. **Run timeout configuration** (`micropython.runTimeoutSeconds`).
15. **Reset timeout configuration** (`micropython.resetTimeoutSeconds`).
16. **MicroPython Workspace view (tree)** in the sidebar.
17. **Refresh Workspace** (reloads device filesystem tree).
18. **Scan device filesystem to build workspace tree** (folders/files, sizes for files, expandable/collapsible folders).
19. **Workspace selection state management** (manual checkbox mode for bulk operations).
20. **Fetch workspace — full** (`MicroPython: Fetch All Files`).
21. **Fetch workspace — partial selection** (checkbox selection of paths).
22. **Download selected now (partial fetch confirmation)**.
23. **Clear/Cancel partial download selection**.
24. **Delete selected now (partial delete confirmation)**.
25. **Clear/Cancel partial delete selection**.
26. **Delete selected files/folders** from the device.
27. **Delete all files workflow** (“clear all”) that also restores an empty `boot.py`.
28. **Clear all files confirmation modal** (danger warning).
29. **New File on device** (creates empty file in chosen device folder).
30. **New Folder on device** (creates folder on the device).
31. **Upload File/Folder to device** (upload chosen files or folders into selected device workspace directory).
32. **Upload multiple files** (multi-select for files).
33. **Upload multiple folders** (optimized sync per selected folder).
34. **Mirror sync / upload-only modes** for folder sync (`deleteExtraneous` flag).
35. **Link Folder** (choose a local folder; mirror sync to device with ongoing auto-sync).
36. **Linked-folder auto-sync via filesystem watcher** (detects create/change/delete in local folder).
37. **Auto-sync delay/debouncing** for linked folder (`LINKED_FOLDER_SYNC_DELAY_MS`).
38. **Linked-folder sync retry/backoff** on busy/unavailable states (`LINKED_FOLDER_SYNC_RETRY_DELAY_MS`).
39. **Sync status indicators in status bar** (pending/syncing/synced/paused-warn).
40. **Auto-sync state tracking for editor changes** (documents in `micropython` scheme and linked-folder files).
41. **Text editor save triggers linked folder sync** (saves local linked files then syncs).
42. **MicroPython Workspace mounts as Explorer tree** (mount device workspace into VS Code Explorer).
43. **MicroPython custom file system provider** (`micropython` URI scheme).
44. **Browse device filesystem through standard VS Code file operations** (tree + filesystem provider integration).
45. **Open MicroPython files from workspace** (standard editor open for device files).
46. **In-editor save → sync to device** (writes through workspace filesystem provider).
47. **Download Selected File(s)** from device to local folder.
48. **Upload changes when saving within linked or device-mounted workspace**.
49. **Copy workspace entry** (copy selected device file/folder into internal clipboard).
50. **Paste workspace entry** (paste into another folder on same device; avoids self-nesting).
51. **Paste conflict-safe naming** (adds “copy”, “copy 2”… up to 999).
52. **Rename workspace entry** (device-side rename; disallows renaming root; prevents cross-device rename).
53. **Delete workspace entry from context menu** (device-side delete for selected file/folder).
54. **Show Workspace Entry Properties** (modal details: size, counts for directories, recursive summary, and storage usage at root).
55. **Recursive folder summary** for properties (counts files/directories and total size).
56. **Storage usage VFS stats** for device root (total/used/free) shown in properties.
57. **Checkbox-driven multi-select UX** in workspace view (manual checkbox handling).
58. **Selection-mode aware operations** (fetch vs delete selections tracked separately).
59. **Selection snapshot building from remote tree entries** (stores checkboxes per remote path).
60. **Preserve view loading state on refresh** (reloads without losing loaded state when requested).
61. **Error placeholder nodes** in workspace tree when scanning fails.
62. **Empty/placeholder “Loading/Refresh” workspace states**.
63. **Output channels for runs & operations**:
    - Run Non-Interactive output
    - Folder Sync output
    - Clear All output
    - Workspace output (scan)
    - Workspace Fetch output
64. **Streaming output for non-interactive runs** (incremental line capture into Output channel).
65. **Cancelable progress UI for non-interactive run** (withProgress and cancel token).
66. **Non-interactive run cancellation behavior** (shows cancelled notification).
67. **Interactive run keeps session attached** (terminal remains available for user I/O).
68. **Background filesystem sync best-effort after soft reset**.
69. **Workspace auto-refresh after successful operations** (sync/upload/clear/delete may invalidate and optionally refresh tree).
70. **Device sync folder history** (remembers recent local folders used for sync).
71. **Sync folder history limit** (`MAX_SYNC_FOLDER_HISTORY`).
72. **Configurable history-based sync folder picker** when linking/syncing.
73. **MicroPython AI MCP commands (workspace/files + repl + reset)**:
    73.1. **AI Agent MCP Status**.
    73.2. **Configure AI Agent MCP Access**.
    73.3. **AI List Files**.
    73.4. **AI Run Code**.
    73.5. **AI Upload File**.
    73.6. **AI Download File**.
    73.7. **AI Create Directory**.
    73.8. **AI Delete File/Dir**.
    73.9. **AI Read File**.
    73.10. **AI Write File**.
    73.11. **AI File Stats**.
    73.12. **AI Send REPL Command**.
    73.13. **AI Soft Reset**.
74. **Language Model Tools integration** (VS Code language model toolReferenceName bindings):
    74.1. `micropython_device_status`.
    74.2. `micropython_sync_project`.
    74.3. `micropython_run_and_test`.
    74.4. `micropython_filesystem`.
    74.5. `micropython_soft_reset`.
75. **Bundled MCP stdio server** capability for device status, project sync, run/test, and tools.
76. **Codex MCP config auto-registration on activation** (plus config.toml refresh support).
77. **AI safety default** for deleteExtraneous in AI sync tools (default false).
78. **No-ext-tools usage guidance (AI “use this extension backend” modelDescription)**.
79. **Runtime staging support** (bundled runtime support so extension can run on multiple platforms).
80. **Build/packaging scripts** for runtime staging and compiling (`stage-runtime`, `compile`, `package:vsix`).
