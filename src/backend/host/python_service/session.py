from __future__ import annotations

import base64
import codecs
import posixpath
import threading
import time
from pathlib import Path
from typing import Any, Callable

import serial

from .constants import *
from .errors import *
from .operations import _recover_after_run_failure, _safe_sync_filesystem
from .serial_controller import MicroPythonController
from .sync_utils import *
from .terminal_text import _should_abort_for_exception

class PersistentSession:
    def __init__(
        self,
        emit_terminal_text: Callable[[str], None],
        emit_session_state: Callable[[dict[str, Any]], None],
    ):
        self._emit_terminal_text = emit_terminal_text
        self._emit_session_state = emit_session_state
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._abort_requested = threading.Event()
        self._controller: MicroPythonController | None = None
        self._port: str | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._reader_pause_requested = threading.Event()
        self._reader_paused = threading.Event()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._build_state_locked()

    def _raise_if_abort_requested(self) -> None:
        if self._abort_requested.is_set():
            raise SessionAbortedError("MicroPython device disconnected.")

    def open(self, port: str) -> dict[str, Any]:
        if not port:
            return {"ok": False, "connected": False, "port": None, "error": "No port provided."}

        with self._lock:
            if self._controller is not None and self._port == port:
                return {"ok": True, **self._build_state_locked()}

        self.close(emit_event=False, reason="switching")

        try:
            controller = MicroPythonController(port, exclusive=True)
        except Exception as exc:
            error = str(exc)
            self._emit_session_state_event(error=error, reason="open-failed")
            return {"ok": False, "connected": False, "port": None, "error": error}

        with self._lock:
            self._attach_session_locked(controller)
            payload = {"ok": True, **self._build_state_locked()}

        self._emit_session_state_event(reason="opened")
        return payload

    def close(self, emit_event: bool = True, reason: str = "closed") -> dict[str, Any]:
        detached = self._detach_session()
        self._teardown_detached(detached)
        payload = {"ok": True, "connected": False, "port": None}
        if emit_event:
            self._emit_session_state_event(reason=reason)
        return payload

    def abort(self, reason: str = "aborted") -> dict[str, Any]:
        self._abort_requested.set()
        with self._lock:
            controller = self._controller
        if controller is not None:
            controller.abort()
        return self.close(reason=reason)

    def terminal_write(self, data: str) -> dict[str, Any]:
        if not data:
            return {"ok": True}

        self._raise_if_abort_requested()
        with self._lock:
            controller = self._controller
        if controller is None:
            return {"ok": False, "error": "No open MicroPython session."}

        try:
            with self._operation_lock:
                controller.write_terminal(data.encode("utf-8"))
            return {"ok": True}
        except Exception as exc:
            self._handle_reader_failure(controller, str(exc))
            return {"ok": False, "error": str(exc)}

    def soft_reset(self, port: str | None, timeout_seconds: float) -> dict[str, Any]:
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "promptSeen": False,
                    "rebootSeen": False,
                    "port": port,
                    "output": "",
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "promptSeen": False,
                    "rebootSeen": False,
                    "port": port or "",
                    "output": "",
                    "error": str(exc),
                }

            try:
                payload = controller.soft_reset(timeout_seconds)
            except Exception as exc:
                payload = {
                    "ok": False,
                    "promptSeen": False,
                    "rebootSeen": False,
                    "port": controller.port,
                    "output": "",
                    "error": str(exc),
                }
            finally:
                self._end_exclusive_operation(pause_requested)

        if payload.get("output"):
            self._emit_terminal_text(str(payload["output"]))
        return payload

    def run_file(
        self,
        port: str | None,
        local_file: str,
        timeout_seconds: float,
        stdout_line_callback: Callable[[str], None] | None = None,
        stderr_line_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        try:
            local_path, source = _load_local_text_file(local_file)
        except Exception as exc:
            return {
                "ok": False,
                "port": port or "",
                "localFile": str(Path(local_file).expanduser().resolve()),
                "output": "",
                "error": str(exc),
            }

        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "localFile": str(local_path),
                    "output": "",
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "localFile": str(local_path),
                    "output": "",
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                try:
                    stdout_bytes, stderr_bytes = controller.exec_source(
                        source,
                        timeout_seconds,
                        line_callback=stdout_line_callback,
                        cancel_event=cancel_event,
                    )
                    output = stdout_bytes.decode("utf-8", errors="replace")
                    error_text = stderr_bytes.decode("utf-8", errors="replace").strip()

                    if error_text:
                        if stderr_line_callback is not None:
                            for line in error_text.splitlines():
                                stderr_line_callback(line)
                        payload = {
                            "ok": False,
                            "port": controller.port,
                            "localFile": str(local_path),
                            "output": output,
                            "error": error_text,
                        }
                    else:
                        payload = {
                            "ok": True,
                            "port": controller.port,
                            "localFile": str(local_path),
                            "output": output,
                        }
                except RunCancelledError as exc:
                    payload = {
                        "ok": False,
                        "cancelled": True,
                        "port": controller.port,
                        "localFile": str(local_path),
                        "output": exc.output.decode("utf-8", errors="replace"),
                        "error": "Run cancelled by user",
                    }
                except Exception as exc:
                    payload = {
                        "ok": False,
                        "port": controller.port,
                        "localFile": str(local_path),
                        "output": "",
                        "error": str(exc),
                    }
                    if not _should_abort_for_exception(exc):
                        recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

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
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }
        return payload

    def run_file_interactive(
        self,
        port: str | None,
        local_file: str,
    ) -> dict[str, Any]:
        try:
            local_path, source = _load_local_text_file(local_file)
        except Exception as exc:
            return {
                "ok": False,
                "port": port or "",
                "localFile": str(Path(local_file).expanduser().resolve()),
                "error": str(exc),
            }

        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "localFile": str(local_path),
                    "error": opened.get("error", "Failed to open session."),
                }

        pending_bytes = b""
        recovery_payload: dict[str, Any] | None = None

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "localFile": str(local_path),
                    "error": str(exc),
                }

            try:
                start_payload = controller.exec_friendly_source_start(source)
                pending_bytes = start_payload.get("pending", b"")
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "localFile": str(local_path),
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "localFile": str(local_path),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = controller.recover_friendly_prompt(FRIENDLY_PASTE_RECOVERY_TIMEOUT_SEC)
            finally:
                self._end_exclusive_operation(pause_requested)

        if pending_bytes:
            self._emit_terminal_text(pending_bytes.decode("utf-8", errors="replace"))

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after interactive run"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def sync_folder(
        self,
        port: str | None,
        local_folder: str,
        remote_folder: str,
        delete_extraneous: bool = False,
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        try:
            local_root, directories, files = _scan_local_folder(local_folder, remote_folder)
        except Exception as exc:
            return {
                "ok": False,
                "port": port or "",
                "localFolder": str(Path(local_folder).expanduser().resolve()),
                "remoteFolder": str(remote_folder),
                "error": str(exc),
            }

        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "localFolder": str(local_root),
                    "remoteFolder": directories[0],
                    "error": opened.get("error", "Failed to open session."),
                }

        def report(line: str) -> None:
            if progress_callback is not None:
                progress_callback(line)

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "localFolder": str(local_root),
                    "remoteFolder": directories[0],
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                # Fast path: keep one raw REPL session open across sync operations
                # instead of enter/exit for every scan/dir/delete command.
                try:
                    enter_raw = getattr(controller, "_enter_raw_repl", None)
                    if callable(enter_raw):
                        enter_raw(timeout_overall=RAW_REPL_ENTER_TIMEOUT_SEC)
                except Exception:
                    pass

                remote_root = directories[0]
                report(f"Scanning {remote_root} on device…")
                remote_sizes: dict[str, int] = {}
                remote_scan_error: Exception | None = None

                for attempt in range(2):
                    self._raise_if_abort_requested()
                    try:
                        remote_sizes = _read_remote_file_sizes(controller, remote_root)
                        report(f"Device has {len(remote_sizes)} file(s)")
                        remote_scan_error = None
                        break
                    except ControllerError as exc:
                        remote_scan_error = exc
                        if _should_abort_for_exception(exc):
                            raise
                        if attempt == 0:
                            scan_recovery = _recover_after_run_failure(controller)
                            if scan_recovery.get("output"):
                                self._emit_terminal_text(str(scan_recovery["output"]))
                            if not scan_recovery.get("ok"):
                                restore_error = scan_recovery.get("error") or "Unknown recovery failure"
                                raise ControllerError(f"{exc} | scan recovery failed: {restore_error}") from exc
                            report(f"Remote scan failed ({exc}). Retrying after REPL recovery…")
                            continue
                        break
                    except Exception as exc:
                        remote_scan_error = exc
                        if attempt == 0:
                            report(f"Remote scan unavailable ({exc}). Retrying once…")
                            continue
                        break

                if remote_scan_error is not None:
                    raise remote_scan_error

                unchanged, to_upload, to_delete, extra_remote = _build_sync_plan(
                    files,
                    remote_sizes,
                    delete_extraneous=delete_extraneous,
                )
                unchanged_count = len(unchanged)

                report("─── Sync comparison ───")
                report(f"  Unchanged : {unchanged_count} file(s)")
                report(f"  To upload : {len(to_upload)} file(s)")
                report(f"  To delete : {len(to_delete)} file(s)")
                if not delete_extraneous and extra_remote:
                    report(f"  Extra remote kept : {len(extra_remote)} file(s)")
                report("───────────────────────")

                deleted_count = 0
                delete_failures: list[str] = []
                if to_delete:
                    report(f"Deleting {len(to_delete)} stale file(s)…")
                    for index, remote_path in enumerate(to_delete, start=1):
                        self._raise_if_abort_requested()
                        try:
                            if controller.sync_delete_file(remote_path):
                                deleted_count += 1
                                report(f"[{index}/{len(to_delete)}] Deleted: {remote_path}")
                            else:
                                delete_failures.append(remote_path)
                                report(f"[{index}/{len(to_delete)}] Failed: {remote_path}")
                        except Exception as exc:
                            if _should_abort_for_exception(exc):
                                raise
                            delete_failures.append(remote_path)
                            report(f"[{index}/{len(to_delete)}] Failed: {remote_path} ({exc})")

                directory_failures: list[str] = []
                required_dirs = directories
                if to_upload:
                    device_dirs = [
                        device_dir
                        for device_dir in (_sync_device_relative_path(remote_dir) for remote_dir in required_dirs)
                        if device_dir
                    ]
                    report("Creating folder structure…")
                    for device_dir in device_dirs:
                        self._raise_if_abort_requested()
                        try:
                            if controller.sync_mkdir(device_dir):
                                report(f"  + {device_dir}")
                            else:
                                directory_failures.append(device_dir)
                                report(f"  ! {device_dir} (failed)")
                        except Exception as exc:
                            if _should_abort_for_exception(exc):
                                raise
                            directory_failures.append(device_dir)
                            report(f"  ! {device_dir} ({exc})")
                    report("Folder structure synced ✓")

                    synced_bytes = 0
                    uploaded_count = 0
                    upload_failures: list[str] = []
                    raw_upload_open = bool(getattr(controller, "_in_raw_repl", False))
                    report(f"Uploading {len(to_upload)} file(s)…")

                    try:
                        for index, file_info in enumerate(sorted(to_upload, key=lambda item: str(item["remote_path"])), start=1):
                            self._raise_if_abort_requested()
                            local_path = Path(file_info["local_path"])
                            relative_path = str(file_info["relative_path"])
                            remote_path = str(file_info["remote_path"])
                            remote_write_path = _sync_device_relative_path(remote_path)
                            file_size = int(file_info["size_bytes"])
                            uploaded = False

                            for attempt in range(SYNC_FILE_RETRY_COUNT):
                                self._raise_if_abort_requested()
                                try:
                                    if not raw_upload_open:
                                        controller.sync_enter_raw_repl()
                                        raw_upload_open = True
                                    controller.sync_put_raw(local_path, remote_write_path)
                                    synced_bytes += file_size
                                    uploaded_count += 1
                                    report(f"[{index}/{len(to_upload)}] Uploaded: {remote_path} ({file_size} bytes)")
                                    uploaded = True
                                    break
                                except Exception as exc:
                                    if _should_abort_for_exception(exc):
                                        raise SessionAbortedError(str(exc)) from exc
                                    if raw_upload_open:
                                        try:
                                            controller.sync_exit_raw_repl()
                                        except Exception:
                                            pass
                                        raw_upload_open = False
                                    if attempt + 1 < SYNC_FILE_RETRY_COUNT:
                                        retry_detail = ""
                                        reconnect = getattr(controller, "sync_reconnect", None)
                                        if callable(reconnect):
                                            try:
                                                reconnect()
                                                retry_detail = " after connection reset"
                                            except Exception as reconnect_exc:
                                                if _should_abort_for_exception(reconnect_exc):
                                                    raise SessionAbortedError(str(reconnect_exc)) from reconnect_exc
                                                retry_detail = f" after failed connection reset ({reconnect_exc})"
                                        report(f"[{index}/{len(to_upload)}] Retry: {relative_path} ({exc}){retry_detail}")
                                        time.sleep(SYNC_FILE_RETRY_DELAY_SEC)
                                        continue
                                    upload_failures.append(remote_path)
                                    report(f"[{index}/{len(to_upload)}] Failed: {remote_path} ({exc})")

                            if not uploaded:
                                continue
                    finally:
                        if raw_upload_open:
                            controller.sync_exit_raw_repl()
                else:
                    synced_bytes = 0
                    uploaded_count = 0
                    upload_failures = []
                    if required_dirs:
                        device_dirs = [
                            device_dir
                            for device_dir in (_sync_device_relative_path(remote_dir) for remote_dir in required_dirs)
                            if device_dir
                        ]
                        report("Creating folder structure…")
                        for device_dir in device_dirs:
                            self._raise_if_abort_requested()
                            try:
                                if controller.sync_mkdir(device_dir):
                                    report(f"  + {device_dir}")
                                else:
                                    directory_failures.append(device_dir)
                                    report(f"  ! {device_dir} (failed)")
                            except Exception as exc:
                                if _should_abort_for_exception(exc):
                                    raise
                                directory_failures.append(device_dir)
                                report(f"  ! {device_dir} ({exc})")
                        report("Folder structure synced ✓")
                    if not to_delete and not required_dirs:
                        report("Everything is already in sync")

                directory_warning_count = 0
                if directory_failures and not upload_failures:
                    directory_warning_count = len(directory_failures)
                    report(
                        f"Directory creation reported {directory_warning_count} issue(s), "
                        "but uploads succeeded. Treating as warning."
                    )
                    directory_failures = []

                if deleted_count > 0 or uploaded_count > 0 or required_dirs:
                    _safe_sync_filesystem(controller)

                ok = not upload_failures and not delete_failures and not directory_failures
                error_summary = ""
                if ok:
                    report(
                        f"Sync complete: {uploaded_count} uploaded, {deleted_count} deleted, "
                        f"{unchanged_count} skipped, {synced_bytes} bytes -> {remote_root}"
                    )
                else:
                    error_summary = (
                        f"Sync finished with {len(upload_failures)} upload failure(s), "
                        f"{len(delete_failures)} delete failure(s), {len(directory_failures)} directory failure(s)."
                    )
                    report(error_summary)

                payload = {
                    "ok": ok,
                    "port": controller.port,
                    "localFolder": str(local_root),
                    "remoteFolder": remote_root,
                    "filesSynced": uploaded_count,
                    "filesDeleted": deleted_count,
                    "filesFailed": len(upload_failures),
                    "filesSkipped": unchanged_count,
                    "filesTotal": len(files),
                    "directoriesEnsured": len(required_dirs),
                    "directoriesFailed": len(directory_failures),
                    "directoriesWarnings": directory_warning_count,
                    "bytesSynced": synced_bytes,
                    "error": error_summary or None,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "localFolder": str(local_root),
                    "remoteFolder": directories[0],
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                try:
                    if bool(getattr(controller, "_in_raw_repl", False)):
                        exit_raw = getattr(controller, "sync_exit_raw_repl", None)
                        if callable(exit_raw):
                            exit_raw()
                except Exception:
                    pass
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after folder sync"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def clear_all_files(
        self,
        port: str | None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "filesDeleted": 0,
                    "directoriesDeleted": 0,
                    "warningsReported": 0,
                    "bootCreated": False,
                    "error": opened.get("error", "Failed to open session."),
                }

        def report(line: str) -> None:
            if progress_callback is not None:
                progress_callback(line)

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "filesDeleted": 0,
                    "directoriesDeleted": 0,
                    "warningsReported": 0,
                    "bootCreated": False,
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                try:
                    enter_raw = getattr(controller, "_enter_raw_repl", None)
                    if callable(enter_raw):
                        enter_raw(timeout_overall=RAW_REPL_ENTER_TIMEOUT_SEC)
                except Exception:
                    pass

                report("Starting MicroPython workspace cleanup...")
                cleanup_output = controller.sync_clear_all(timeout=SYNC_CLEAR_ALL_TIMEOUT_SEC)
                cleanup_summary = _parse_clear_all_output(cleanup_output)

                if not cleanup_summary["doneSeen"]:
                    raise ControllerError("Cleanup timeout - operation may be incomplete")

                for file_path in cleanup_summary["filesDeleted"]:
                    report(f"Deleted file: {file_path}")
                for dir_path in cleanup_summary["directoriesDeleted"]:
                    report(f"Deleted folder: {dir_path}")
                for warning_line in cleanup_summary["warningLines"]:
                    report(f"Warning: {warning_line}")
                for extra_line in cleanup_summary["otherLines"]:
                    report(extra_line)

                report("Creating empty boot.py…")
                controller.sync_put_content("boot.py", b"")
                _safe_sync_filesystem(controller)
                report("Empty boot.py created ✓")

                warning_count = len(cleanup_summary["warningLines"])
                report(
                    "Clear complete: "
                    f"{len(cleanup_summary['filesDeleted'])} files deleted, "
                    f"{len(cleanup_summary['directoriesDeleted'])} folders deleted, "
                    f"{warning_count} warning(s)"
                )
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "filesDeleted": len(cleanup_summary["filesDeleted"]),
                    "directoriesDeleted": len(cleanup_summary["directoriesDeleted"]),
                    "warningsReported": warning_count,
                    "bootCreated": True,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "filesDeleted": 0,
                    "directoriesDeleted": 0,
                    "warningsReported": 0,
                    "bootCreated": False,
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                try:
                    if bool(getattr(controller, "_in_raw_repl", False)):
                        exit_raw = getattr(controller, "sync_exit_raw_repl", None)
                        if callable(exit_raw):
                            exit_raw()
                except Exception:
                    pass
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after clear-all"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_scan_tree(self, port: str | None) -> dict[str, Any]:
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "entries": [],
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "entries": [],
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                remote_dirs, remote_files = controller.sync_scan_tree("/", timeout=SYNC_SCAN_COMMAND_TIMEOUT_SEC)
                entries = [
                    {"path": remote_dir, "kind": "directory"}
                    for remote_dir in remote_dirs
                ]
                entries.extend(
                    {
                        "path": remote_path,
                        "kind": "file",
                        "size": int(file_size),
                    }
                    for remote_path, file_size in sorted(remote_files.items())
                )
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "entries": entries,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "entries": [],
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after workspace scan"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_list_directory(self, port: str | None, remote_path: str) -> dict[str, Any]:
        normalized_remote_path = _sync_device_absolute_path(remote_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "remotePath": normalized_remote_path,
                    "entries": [],
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "remotePath": normalized_remote_path,
                    "entries": [],
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                entries = controller.sync_list_directory(
                    normalized_remote_path,
                    timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC,
                )
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "entries": entries,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "entries": [],
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after directory listing"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_stat(self, port: str | None, remote_path: str) -> dict[str, Any]:
        normalized_remote_path = _sync_device_absolute_path(remote_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "remotePath": normalized_remote_path,
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                stat = controller.sync_stat_path(
                    normalized_remote_path,
                    timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC,
                )
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "stat": stat,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after stat"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_statvfs(self, port: str | None, remote_path: str) -> dict[str, Any]:
        normalized_remote_path = _sync_device_absolute_path(remote_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "remotePath": normalized_remote_path,
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                statvfs = controller.sync_statvfs_path(
                    normalized_remote_path,
                    timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC,
                )
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "statvfs": statvfs,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after statvfs"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_sync_filesystem(self, port: str | None) -> dict[str, Any]:
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "supported": False,
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "supported": False,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                supported = controller.sync_filesystem(timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "supported": supported,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "supported": False,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after filesystem sync"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_read_file(self, port: str | None, remote_path: str) -> dict[str, Any]:
        normalized_remote_path = _sync_device_absolute_path(remote_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "remotePath": normalized_remote_path,
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "remotePath": normalized_remote_path,
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                stat = controller.sync_stat_path(
                    normalized_remote_path,
                    timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC,
                )
                if stat.get("kind") != "file":
                    raise WorkspaceOperationError(f"Path is a directory: {normalized_remote_path}", code="EISDIR")

                content_bytes = controller.sync_read_file_bytes(
                    normalized_remote_path,
                    timeout=SYNC_SCAN_COMMAND_TIMEOUT_SEC,
                )
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "size": len(content_bytes),
                    "contentBase64": base64.b64encode(content_bytes).decode("ascii"),
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after file read"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_write_file(
        self,
        port: str | None,
        remote_path: str,
        content_base64: str,
        create: bool,
        overwrite: bool,
    ) -> dict[str, Any]:
        normalized_remote_path = _sync_device_absolute_path(remote_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "remotePath": normalized_remote_path,
                    "error": opened.get("error", "Failed to open session."),
                }

        try:
            content_bytes = base64.b64decode(content_base64.encode("ascii"), validate=True)
        except Exception as exc:
            return {
                "ok": False,
                "port": port or "",
                "remotePath": normalized_remote_path,
                "code": "EINVAL",
                "error": f"Invalid file content encoding: {exc}",
            }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                if normalized_remote_path == "/":
                    raise WorkspaceOperationError("Cannot write to the device root.", code="EISDIR")

                parent_path = posixpath.dirname(normalized_remote_path) or "/"
                parent_stat = controller.sync_stat_path(parent_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                if parent_stat.get("kind") != "directory":
                    raise WorkspaceOperationError(f"Parent path is not a directory: {parent_path}", code="ENOTDIR")

                existing_stat: dict[str, Any] | None = None
                try:
                    existing_stat = controller.sync_stat_path(normalized_remote_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                except WorkspaceOperationError as exc:
                    if exc.code != "ENOENT":
                        raise

                if existing_stat is None and not create:
                    raise WorkspaceOperationError(f"File not found: {normalized_remote_path}", code="ENOENT")
                if existing_stat is not None:
                    if existing_stat.get("kind") != "file":
                        raise WorkspaceOperationError(f"Path is a directory: {normalized_remote_path}", code="EISDIR")
                    if not overwrite:
                        raise WorkspaceOperationError(f"File already exists: {normalized_remote_path}", code="EEXIST")

                controller.sync_put_content(normalized_remote_path, content_bytes)
                _safe_sync_filesystem(controller)
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "size": len(content_bytes),
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after file write"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_create_directory(self, port: str | None, remote_path: str) -> dict[str, Any]:
        normalized_remote_path = _sync_device_absolute_path(remote_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "remotePath": normalized_remote_path,
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                if normalized_remote_path == "/":
                    raise WorkspaceOperationError("The device root already exists.", code="EEXIST")

                parent_path = posixpath.dirname(normalized_remote_path) or "/"
                parent_stat = controller.sync_stat_path(parent_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                if parent_stat.get("kind") != "directory":
                    raise WorkspaceOperationError(f"Parent path is not a directory: {parent_path}", code="ENOTDIR")

                try:
                    existing_stat = controller.sync_stat_path(normalized_remote_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                except WorkspaceOperationError as exc:
                    if exc.code != "ENOENT":
                        raise
                else:
                    raise WorkspaceOperationError(
                        f"Path already exists: {normalized_remote_path}",
                        code="EEXIST" if existing_stat.get("kind") == "directory" else "EEXIST",
                    )

                created = controller.sync_mkdir_recursive(normalized_remote_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                if not created:
                    raise WorkspaceOperationError(f"Directory was not created: {normalized_remote_path}", code="EINVAL")

                _safe_sync_filesystem(controller)
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after mkdir"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_delete(self, port: str | None, remote_path: str, recursive: bool) -> dict[str, Any]:
        normalized_remote_path = _sync_device_absolute_path(remote_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "remotePath": normalized_remote_path,
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                if normalized_remote_path == "/":
                    raise WorkspaceOperationError("Cannot delete the device root.", code="EPERM")

                stat = controller.sync_stat_path(normalized_remote_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                deleted_kind = controller.sync_delete_path(
                    normalized_remote_path,
                    recursive=recursive or stat.get("kind") == "file",
                    timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC,
                )
                _safe_sync_filesystem(controller)
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "kind": deleted_kind,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "remotePath": normalized_remote_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after delete"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def workspace_rename(
        self,
        port: str | None,
        old_path: str,
        new_path: str,
        overwrite: bool,
    ) -> dict[str, Any]:
        normalized_old_path = _sync_device_absolute_path(old_path)
        normalized_new_path = _sync_device_absolute_path(new_path)
        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "oldPath": normalized_old_path,
                    "newPath": normalized_new_path,
                    "error": opened.get("error", "Failed to open session."),
                }

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "oldPath": normalized_old_path,
                    "newPath": normalized_new_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                if normalized_old_path == normalized_new_path:
                    payload = {
                        "ok": True,
                        "port": controller.port,
                        "oldPath": normalized_old_path,
                        "newPath": normalized_new_path,
                    }
                else:
                    if normalized_old_path == "/" or normalized_new_path == "/":
                        raise WorkspaceOperationError("Cannot rename the device root.", code="EINVAL")

                    controller.sync_stat_path(normalized_old_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)

                    target_parent = posixpath.dirname(normalized_new_path) or "/"
                    parent_stat = controller.sync_stat_path(target_parent, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                    if parent_stat.get("kind") != "directory":
                        raise WorkspaceOperationError(f"Parent path is not a directory: {target_parent}", code="ENOTDIR")

                    try:
                        target_stat = controller.sync_stat_path(normalized_new_path, timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC)
                    except WorkspaceOperationError as exc:
                        if exc.code != "ENOENT":
                            raise
                    else:
                        if not overwrite:
                            raise WorkspaceOperationError(f"Path already exists: {normalized_new_path}", code="EEXIST")
                        controller.sync_delete_path(
                            normalized_new_path,
                            recursive=target_stat.get("kind") == "directory",
                            timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC,
                        )

                    controller.sync_rename_path(
                        normalized_old_path,
                        normalized_new_path,
                        timeout=SYNC_DIR_COMMAND_TIMEOUT_SEC,
                    )
                    _safe_sync_filesystem(controller)
                    payload = {
                        "ok": True,
                        "port": controller.port,
                        "oldPath": normalized_old_path,
                        "newPath": normalized_new_path,
                    }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "oldPath": normalized_old_path,
                    "newPath": normalized_new_path,
                    "code": _workspace_exception_code(exc),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after rename"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def import_workspace(
        self,
        port: str | None,
        local_folder: str,
        progress_callback: Callable[[str], None] | None = None,
        remote_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        local_root = Path(local_folder).expanduser().resolve()
        if local_root.exists() and not local_root.is_dir():
            return {
                "ok": False,
                "port": port or "",
                "localFolder": str(local_root),
                "error": f"Workspace import target is not a directory: {local_root}",
            }

        if port:
            opened = self.open(port)
            if not opened.get("ok"):
                return {
                    "ok": False,
                    "port": port,
                    "localFolder": str(local_root),
                    "error": opened.get("error", "Failed to open session."),
                }

        def report(line: str) -> None:
            if progress_callback is not None:
                progress_callback(line)

        with self._operation_lock:
            try:
                controller, pause_requested = self._begin_exclusive_operation()
            except Exception as exc:
                return {
                    "ok": False,
                    "port": port or "",
                    "localFolder": str(local_root),
                    "error": str(exc),
                }

            payload: dict[str, Any]
            recovery_payload: dict[str, Any] | None = None
            try:
                local_root.mkdir(parents=True, exist_ok=True)

                try:
                    enter_raw = getattr(controller, "_enter_raw_repl", None)
                    if callable(enter_raw):
                        enter_raw(timeout_overall=RAW_REPL_ENTER_TIMEOUT_SEC)
                except Exception:
                    pass

                report("Scanning MicroPython workspace...")
                remote_dirs, remote_files = controller.sync_scan_tree("/", timeout=SYNC_SCAN_COMMAND_TIMEOUT_SEC)
                selected_dirs, selected_files = _select_workspace_entries(remote_dirs, remote_files, remote_paths)
                report(f"Found {len(selected_files)} file(s) and {len(selected_dirs)} folder(s)")

                ensured_dirs = 0
                for remote_dir in selected_dirs:
                    self._raise_if_abort_requested()
                    relative_dir = _sync_device_relative_path(remote_dir)
                    if not relative_dir:
                        continue
                    (local_root / Path(relative_dir)).mkdir(parents=True, exist_ok=True)
                    ensured_dirs += 1

                bytes_imported = 0
                imported_files = 0
                sorted_files = sorted(selected_files.items())
                for index, (remote_path, file_size) in enumerate(sorted_files, start=1):
                    self._raise_if_abort_requested()
                    relative_file = _sync_device_relative_path(remote_path)
                    local_path = local_root / Path(relative_file)
                    local_path.parent.mkdir(parents=True, exist_ok=True)

                    timeout = min(
                        WORKSPACE_IMPORT_FILE_TIMEOUT_MAX_SEC,
                        max(5.0, 2.0 + (max(0, int(file_size)) / WORKSPACE_IMPORT_FILE_THROUGHPUT_BYTES_PER_SEC)),
                    )
                    content = controller.sync_read_file_bytes(remote_path, timeout=timeout)
                    local_path.write_bytes(content)
                    imported_files += 1
                    bytes_imported += len(content)
                    report(f"[{index}/{len(sorted_files)}] Imported: {remote_path}")

                report(
                    f"Workspace import complete: {imported_files} file(s), "
                    f"{ensured_dirs} folder(s), {bytes_imported} bytes"
                )
                payload = {
                    "ok": True,
                    "port": controller.port,
                    "localFolder": str(local_root),
                    "filesImported": imported_files,
                    "directoriesImported": ensured_dirs,
                    "bytesImported": bytes_imported,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "port": controller.port,
                    "localFolder": str(local_root),
                    "error": str(exc),
                }
                if not _should_abort_for_exception(exc):
                    recovery_payload = _recover_after_run_failure(controller)
            finally:
                try:
                    if bool(getattr(controller, "_in_raw_repl", False)):
                        exit_raw = getattr(controller, "sync_exit_raw_repl", None)
                        if callable(exit_raw):
                            exit_raw()
                except Exception:
                    pass
                self._end_exclusive_operation(pause_requested)

        if recovery_payload and recovery_payload.get("output"):
            self._emit_terminal_text(str(recovery_payload["output"]))

        if recovery_payload and not recovery_payload.get("ok"):
            payload["ok"] = False
            existing_error = payload.get("error")
            restore_error = recovery_payload.get("error") or "Failed to recover friendly REPL after workspace import"
            if existing_error:
                payload["error"] = f"{existing_error} | restore failed: {restore_error}"
            else:
                payload["error"] = f"restore failed: {restore_error}"

        if recovery_payload is not None:
            payload["restoreDetail"] = {
                "ok": bool(recovery_payload.get("ok")),
                "port": payload.get("port", port or ""),
                "recovery": recovery_payload,
            }

        return payload

    def _process_terminal_text(self, text: str) -> None:
        if text:
            self._emit_terminal_text(text)

    def _attach_session_locked(self, controller: MicroPythonController) -> None:
        self._abort_requested.clear()
        stop_event = threading.Event()
        pause_requested = threading.Event()
        paused_event = threading.Event()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        thread = threading.Thread(
            target=self._reader_loop,
            args=(controller, stop_event, pause_requested, paused_event, decoder),
            daemon=True,
            name=f"MicroPythonSessionReader[{controller.port}]",
        )

        self._controller = controller
        self._port = controller.port
        self._reader_stop = stop_event
        self._reader_pause_requested = pause_requested
        self._reader_paused = paused_event
        self._reader_thread = thread
        thread.start()

    def _detach_session(self) -> tuple[MicroPythonController, threading.Thread | None, threading.Event, threading.Event] | None:
        with self._lock:
            return self._detach_session_locked()

    def _detach_session_locked(
        self,
    ) -> tuple[MicroPythonController, threading.Thread | None, threading.Event, threading.Event] | None:
        if self._controller is None:
            return None

        detached = (
            self._controller,
            self._reader_thread,
            self._reader_stop,
            self._reader_pause_requested,
        )
        self._controller = None
        self._port = None
        self._reader_thread = None
        self._reader_stop = threading.Event()
        self._reader_pause_requested = threading.Event()
        self._reader_paused = threading.Event()
        return detached

    def _teardown_detached(
        self,
        detached: tuple[MicroPythonController, threading.Thread | None, threading.Event, threading.Event] | None,
    ) -> None:
        if detached is None:
            return

        controller, thread, stop_event, pause_requested = detached
        stop_event.set()
        pause_requested.clear()
        controller.close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _reader_loop(
        self,
        controller: MicroPythonController,
        stop_event: threading.Event,
        pause_requested: threading.Event,
        paused_event: threading.Event,
        decoder: Any,
    ) -> None:
        try:
            while not stop_event.is_set():
                if pause_requested.is_set():
                    paused_event.set()
                    while pause_requested.is_set() and not stop_event.is_set():
                        time.sleep(0.01)
                    paused_event.clear()
                    continue

                try:
                    chunk = controller.read_terminal_chunk()
                except (serial.SerialException, serial.SerialTimeoutException, OSError, TypeError) as exc:
                    if stop_event.is_set():
                        return
                    self._handle_reader_failure(controller, str(exc))
                    return

                if not chunk:
                    continue

                text = decoder.decode(chunk, final=False)
                if text:
                    self._process_terminal_text(text)
        finally:
            try:
                text = decoder.decode(b"", final=True)
            except Exception:
                text = ""
            if text:
                self._process_terminal_text(text)
            paused_event.set()

    def _handle_reader_failure(self, controller: MicroPythonController, error: str) -> None:
        detached = None
        self._abort_requested.set()
        with self._lock:
            if self._controller is not controller:
                return
            detached = self._detach_session_locked()
        self._teardown_detached(detached)
        self._emit_session_state_event(error=error, reason="reader-failed")

    def _begin_exclusive_operation(self) -> tuple[MicroPythonController, threading.Event]:
        self._raise_if_abort_requested()
        with self._lock:
            controller = self._controller
            pause_requested = self._reader_pause_requested
            paused_event = self._reader_paused

        if controller is None:
            raise ControllerError("No open MicroPython session.")

        pause_requested.set()
        if not paused_event.wait(timeout=READER_PAUSE_WAIT_SEC):
            pause_requested.clear()
            raise ControllerError("Session reader did not pause in time.")

        self._raise_if_abort_requested()

        # Drop any buffered async output before running an exclusive command.
        # This avoids mixing stale terminal traffic into raw REPL command output.
        controller._drain_serial_input()
        return controller, pause_requested

    def _end_exclusive_operation(self, pause_requested: threading.Event) -> None:
        pause_requested.clear()

    def _build_state_locked(self) -> dict[str, Any]:
        return {
            "connected": self._controller is not None,
            "port": self._port,
        }

    def _emit_session_state_event(self, *, error: str | None = None, reason: str | None = None) -> None:
        payload = self.state()
        if error:
            payload["error"] = error
        if reason:
            payload["reason"] = reason
        self._emit_session_state(payload)

__all__ = ['PersistentSession']
