from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .constants import (
    RUN_FAILURE_FRIENDLY_REPL_TIMEOUT_SEC,
    RUN_FAILURE_SOFT_RESET_TIMEOUT_SEC,
    SYNC_DIR_COMMAND_TIMEOUT_SEC,
)
from .serial_controller import MicroPythonController
from .terminal_text import _join_non_empty_text
from .sync_utils import _load_local_text_file

def _recover_after_run_failure(controller: MicroPythonController) -> dict[str, Any]:
    try:
        friendly_repl = controller.recover_friendly_repl(RUN_FAILURE_FRIENDLY_REPL_TIMEOUT_SEC)
    except Exception as exc:
        friendly_repl = {
            "ok": False,
            "promptSeen": False,
            "port": controller.port,
            "output": "",
            "error": str(exc),
        }

    if friendly_repl.get("ok"):
        return {
            "ok": True,
            "mode": "friendly-repl",
            "port": controller.port,
            "output": friendly_repl.get("output", ""),
            "friendlyRepl": friendly_repl,
        }

    try:
        soft_reset = controller.soft_reset(RUN_FAILURE_SOFT_RESET_TIMEOUT_SEC)
    except Exception as exc:
        soft_reset = {
            "ok": False,
            "promptSeen": False,
            "rebootSeen": False,
            "port": controller.port,
            "output": "",
            "error": str(exc),
        }

    if soft_reset.get("ok"):
        return {
            "ok": True,
            "mode": "soft-reset",
            "port": controller.port,
            "output": _join_non_empty_text([
                str(friendly_repl.get("output", "")),
                str(soft_reset.get("output", "")),
            ]),
            "friendlyRepl": friendly_repl,
            "softReset": soft_reset,
        }

    return {
        "ok": False,
        "mode": "failed",
        "port": controller.port,
        "output": _join_non_empty_text([
            str(friendly_repl.get("output", "")),
            str(soft_reset.get("output", "")),
        ]),
        "friendlyRepl": friendly_repl,
        "softReset": soft_reset,
        "error": soft_reset.get("error")
        or friendly_repl.get("error")
        or "Failed to recover friendly REPL after run.",
    }

def _safe_sync_filesystem(controller: MicroPythonController) -> bool:
    sync_filesystem = getattr(controller, "sync_filesystem", None)
    if not callable(sync_filesystem):
        return False
    try:
        return bool(sync_filesystem(timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC))
    except Exception:
        return False

def run_soft_reset(port: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        controller = MicroPythonController(port, exclusive=False)
    except Exception as exc:
        return {
            "ok": False,
            "promptSeen": False,
            "rebootSeen": False,
            "port": port,
            "output": "",
            "error": str(exc),
        }

    try:
        return controller.soft_reset(timeout_seconds)
    except Exception as exc:
        return {
            "ok": False,
            "promptSeen": False,
            "rebootSeen": False,
            "port": port,
            "output": "",
            "error": str(exc),
        }
    finally:
        controller.close()

def run_file(
    port: str,
    local_file: str,
    timeout_seconds: float,
    stdout_line_callback: Callable[[str], None] | None = None,
    stderr_line_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    try:
        local_path, source = _load_local_text_file(local_file)
    except Exception as exc:
        return {
            "ok": False,
            "port": port,
            "localFile": str(Path(local_file).expanduser().resolve()),
            "output": "",
            "error": str(exc),
        }

    try:
        controller = MicroPythonController(port, exclusive=False)
    except Exception as exc:
        return {
            "ok": False,
            "port": port,
            "localFile": str(local_path),
            "output": "",
            "error": str(exc),
        }

    payload: dict[str, Any]
    recovery_payload: dict[str, Any] | None = None
    try:
        stdout_bytes, stderr_bytes = controller.exec_source(
            source,
            timeout_seconds,
            line_callback=stdout_line_callback,
        )
        output = stdout_bytes.decode("utf-8", errors="replace")
        error_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if error_text:
            if stderr_line_callback is not None:
                for line in error_text.splitlines():
                    stderr_line_callback(line)
            payload = {
                "ok": False,
                "port": port,
                "localFile": str(local_path),
                "output": output,
                "error": error_text,
            }
        else:
            payload = {
                "ok": True,
                "port": port,
                "localFile": str(local_path),
                "output": output,
            }
    except Exception as exc:
        payload = {
            "ok": False,
            "port": port,
            "localFile": str(local_path),
            "output": "",
            "error": str(exc),
        }
        recovery_payload = _recover_after_run_failure(controller)
    finally:
        controller.close()

    if recovery_payload and not recovery_payload.get("ok"):
        payload["ok"] = False
        existing_error = payload.get("error")
        restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after run"
        if existing_error:
            payload["error"] = f"{existing_error} | restore failed: {restore_error}"
        else:
            payload["error"] = f"restore failed: {restore_error}"
    if recovery_payload is not None:
        payload["restoreDetail"] = {
            "ok": bool(recovery_payload.get("ok")),
            "port": port,
            "recovery": recovery_payload,
        }
    return payload

__all__ = [name for name, value in globals().items() if getattr(value, '__module__', None) == __name__]
