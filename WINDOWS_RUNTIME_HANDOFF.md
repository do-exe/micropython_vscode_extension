# Windows Runtime Handoff

This branch exists to make the MicroPython VS Code extension work on Windows with the same strategy as Linux: the packaged extension carries its own Python runtime and Python packages, so normal users do not need to install Python, pip, pyserial, esptool, mpremote, or ampy.

Branch:

```text
codex/windows-runtime-support
```

## What This Branch Changes

- Adds Windows runtime selection in the extension backend launcher.
- Adds Windows runtime staging in `scripts/stage_runtime.py`.
- Adds `scripts/run_stage_runtime.js` so `npm run stage-runtime` works from Windows and Linux.
- Keeps the final installed extension self-contained.

## Important Venv Note

A venv is recommended only on the Windows build machine. It is used as a clean source for Python packages while creating `runtime/win32-x64`.

The final VSIX should include the generated runtime folder, so the extension user does not need this venv.

## Windows Build Steps

Run these in PowerShell after cloning on Windows:

```powershell
git clone https://github.com/do-exe/micropython_vscode_extension.git
cd micropython_vscode_extension
git checkout codex/windows-runtime-support
npm install
```

Create a builder venv and install the packages the backend needs:

```powershell
py -3 -m venv .venv-runtime
.\.venv-runtime\Scripts\python.exe -m pip install --upgrade pip
.\.venv-runtime\Scripts\python.exe -m pip install pyserial esptool
```

Point the staging script at that venv:

```powershell
$env:MICROPYTHON_SOURCE_PYENV = (Resolve-Path .\.venv-runtime).Path
```

Build the bundled Windows runtime:

```powershell
npm run stage-runtime
```

Expected result:

```text
runtime/win32-x64/manifest.json
runtime/win32-x64/python.exe
runtime/win32-x64/Lib/
runtime/win32-x64/DLLs/
runtime/win32-x64/Lib/site-packages/serial/
runtime/win32-x64/Lib/site-packages/esptool/
```

Then compile and package:

```powershell
npm run compile
npm run package:vsix
```

`npm run package:vsix` runs the build again, so keep `MICROPYTHON_SOURCE_PYENV` set in the same PowerShell session.

## What To Ask The AI On Windows

After opening the cloned repo on the Windows machine, ask:

```text
I am on Windows in the micropython_vscode_extension repo on branch codex/windows-runtime-support.
Please create the Windows bundled runtime, build the VSIX, and verify the extension does not depend on system Python at runtime.
Do not edit unrelated files.
```

The AI should:

1. Check `git status`.
2. Confirm it is on `codex/windows-runtime-support`.
3. Create or reuse `.venv-runtime`.
4. Install `pyserial` and `esptool` into the venv.
5. Set `MICROPYTHON_SOURCE_PYENV`.
6. Run `npm run stage-runtime`.
7. Run `npm run compile`.
8. Run `npm run package:vsix`.
9. Inspect `runtime/win32-x64/manifest.json`.
10. Test the VSIX in VS Code on Windows with a connected MicroPython board.

## User Machine Requirements

The installed extension should not require Python or pip from the user.

Windows may still need a USB serial driver for some boards, depending on the board's USB-to-serial chip. That is outside the Python runtime bundle.
