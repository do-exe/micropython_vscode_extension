# MicroPython for VS Code

MicroPython for VS Code provides a general workflow for MicroPython devices focused on two core capabilities:

- Persistent terminal and REPL access
- Remote device workspace file management

## Highlights

- Persistent backend-owned REPL session inside VS Code
- Integrated MicroPython terminal backed by a VS Code `Pseudoterminal`
- Device selection and reconnect-aware session handling
- Remote workspace browsing and editing
- Create, rename, delete, upload, and download files and folders on the device
- Mount device workspace in Explorer through the `micropython:` file system provider

## Current support

- Linux host runtime for the current release
- Packaged runtime targets the Linux architecture it was built for, such as `linux-x64`

## Requirements

- Visual Studio Code `1.85.0` or newer
- USB/serial permissions on the host machine
- A connected MicroPython-compatible device

## Build

```bash
npm run build
```

This stages the bundled runtime under `runtime/<platform>` and compiles the extension.

## Getting started

1. Install the extension.
2. Connect your MicroPython device over USB.
3. Open the MicroPython view container from the activity bar.
4. Run `MicroPython: Select Device`.
5. Run `MicroPython: Open Terminal`.
6. Use workspace commands from the sidebar to manage device files.

## Main commands

- `MicroPython: Select Device`
- `MicroPython: Open Terminal`
- `MicroPython: Soft Reset Device`
- `MicroPython: Run Non-Interactive File`
- `MicroPython: Run Interactive File`
- `MicroPython: Refresh Workspace`
- `MicroPython: New Workspace File`
- `MicroPython: New Workspace Folder`
- `MicroPython: Rename Workspace Entry`
- `MicroPython: Delete Workspace Entry`
- `MicroPython: Upload Into Workspace`
- `MicroPython: Download Workspace Entry`
- `MicroPython: Mount Workspace In Explorer`
- `MicroPython: Clear All Files`

## Settings

- `micropython.resetTimeoutSeconds`: timeout waiting for prompt after soft reset
- `micropython.runTimeoutSeconds`: timeout for non-interactive file execution, set `0` to disable
- `micropython.autoConnectOnDetect`: auto-open session when selected device is detected
- `micropython.autoScanWorkspace`: auto-scan workspace when the view opens

## Support

- Website: https://micropython.io
- Email: mailto:contact@micropython.io
