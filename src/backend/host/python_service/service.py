from __future__ import annotations

import json
import queue
import sys
import threading
from dataclasses import dataclass
from typing import Any

from .constants import COMMAND_PRIORITY, DEFAULT_RUN_TIMEOUT_SEC, EVENT_SESSION, EVENT_TERMINAL_OUTPUT
from .session import PersistentSession
from .sync_utils import list_detected_esp_ports

_service_write_lock = threading.Lock()


def _service_emit(message: dict[str, Any]) -> None:
    wire = json.dumps(message, ensure_ascii=False)
    with _service_write_lock:
        print(wire, flush=True)

def _service_emit_terminal_output(data: str) -> None:
    if not data:
        return
    _service_emit({"type": "event", "event": EVENT_TERMINAL_OUTPUT, "data": data})

def _service_emit_session_state(payload: dict[str, Any]) -> None:
    _service_emit({"type": "event", "event": EVENT_SESSION, "payload": payload})

@dataclass(order=True)
class ServiceJob:
    priority: int
    seq: int
    request_id: str
    command: str
    args: dict[str, Any]
    stream: bool

class JobDispatcher:
    def __init__(self):
        self._queue: "queue.PriorityQueue[ServiceJob]" = queue.PriorityQueue()
        self._stop = threading.Event()
        self._seq = 0
        self._lock = threading.Lock()
        self._active_run_lock = threading.Lock()
        self._active_run_request_id: str | None = None
        self._active_run_cancel: threading.Event | None = None
        self._session = PersistentSession(_service_emit_terminal_output, _service_emit_session_state)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="MicroPythonBackendDispatcher")
        self._worker.start()

    def submit(self, request_id: str, command: str, args: dict[str, Any], stream: bool) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq
        priority = COMMAND_PRIORITY.get(command, 80)
        self._queue.put(ServiceJob(priority, seq, request_id, command, args, stream))

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            self._seq += 1
            seq = self._seq
        self._queue.put(ServiceJob(999, seq, "", "shutdown", {}, False))
        self._worker.join(timeout=2.0)
        self._session.close(emit_event=False, reason="shutdown")

    def cancel_active_run(self) -> dict[str, Any]:
        with self._active_run_lock:
            if self._active_run_cancel is None or self._active_run_request_id is None:
                return {"ok": True, "active": False, "cancelled": False}
            self._active_run_cancel.set()
            return {
                "ok": True,
                "active": True,
                "cancelled": True,
                "requestId": self._active_run_request_id,
            }

    def abort_session_activity(self, reason: str = "aborted") -> dict[str, Any]:
        cancel_result = self.cancel_active_run()
        self._session.abort(reason=reason)
        return {
            "ok": True,
            "connected": False,
            "port": None,
            "reason": reason,
            "activeRunCancelled": bool(cancel_result.get("cancelled")),
        }

    def _register_active_run(self, request_id: str, cancel_event: threading.Event) -> None:
        with self._active_run_lock:
            self._active_run_request_id = request_id
            self._active_run_cancel = cancel_event

    def _clear_active_run(self, request_id: str) -> None:
        with self._active_run_lock:
            if self._active_run_request_id != request_id:
                return
            self._active_run_request_id = None
            self._active_run_cancel = None

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if job.command == "shutdown":
                self._queue.task_done()
                continue

            payload = self._execute_job(job)
            _service_emit({"id": job.request_id, "type": "result", "payload": payload})
            self._queue.task_done()

    def _execute_job(self, job: ServiceJob) -> dict[str, Any]:
        args = job.args or {}
        if args.get("enabled") is False:
            return {"ok": True, "skipped": True, "reason": "disabled", "command": job.command}

        try:
            if job.command == "scan":
                return {"ok": True, "devices": list_detected_esp_ports()}
            if job.command == "session.open":
                return self._session.open(port=str(args["port"]))
            if job.command == "session.close":
                reason = str(args.get("reason") or "closed-by-command")
                return self._session.close(reason=reason)
            if job.command == "session.state":
                return {"ok": True, **self._session.state()}
            if job.command == "terminal.write":
                return self._session.terminal_write(data=str(args.get("data", "")))
            if job.command == "soft-reset":
                return self._session.soft_reset(
                    port=_optional_arg_string(args, "port"),
                    timeout_seconds=float(args.get("timeout", 5.0)),
                )
            if job.command == "run-file":
                cancel_event = threading.Event()
                self._register_active_run(job.request_id, cancel_event)
                try:
                    if job.stream:
                        return self._session.run_file(
                            port=_optional_arg_string(args, "port"),
                            local_file=str(args["localFile"]),
                            timeout_seconds=float(args.get("timeout", DEFAULT_RUN_TIMEOUT_SEC)),
                            stdout_line_callback=lambda line, req_id=job.request_id: _service_emit(
                                {"id": req_id, "type": "stream", "stream": "stdout", "line": line}
                            ),
                            stderr_line_callback=lambda line, req_id=job.request_id: _service_emit(
                                {"id": req_id, "type": "stream", "stream": "stderr", "line": line}
                            ),
                            cancel_event=cancel_event,
                        )
                    return self._session.run_file(
                        port=_optional_arg_string(args, "port"),
                        local_file=str(args["localFile"]),
                        timeout_seconds=float(args.get("timeout", DEFAULT_RUN_TIMEOUT_SEC)),
                        cancel_event=cancel_event,
                        )
                finally:
                    self._clear_active_run(job.request_id)
            if job.command == "run-file-interactive":
                return self._session.run_file_interactive(
                    port=_optional_arg_string(args, "port"),
                    local_file=str(args["localFile"]),
                )
            if job.command == "sync-folder":
                return self._session.sync_folder(
                    port=_optional_arg_string(args, "port"),
                    local_folder=str(args["localFolder"]),
                    remote_folder=str(args["remoteFolder"]),
                    delete_extraneous=bool(args.get("deleteExtraneous", False)),
                    progress_callback=(
                        lambda line, req_id=job.request_id: _service_emit(
                            {"id": req_id, "type": "stream", "stream": "stdout", "line": line}
                        )
                    ) if job.stream else None,
                )
            if job.command == "clear-all-files":
                return self._session.clear_all_files(
                    port=_optional_arg_string(args, "port"),
                    progress_callback=(
                        lambda line, req_id=job.request_id: _service_emit(
                            {"id": req_id, "type": "stream", "stream": "stdout", "line": line}
                        )
                    ) if job.stream else None,
                )
            if job.command == "workspace.scan-tree":
                return self._session.workspace_scan_tree(
                    port=_optional_arg_string(args, "port"),
                )
            if job.command == "workspace.list-directory":
                return self._session.workspace_list_directory(
                    port=_optional_arg_string(args, "port"),
                    remote_path=str(args["remotePath"]),
                )
            if job.command == "workspace.stat":
                return self._session.workspace_stat(
                    port=_optional_arg_string(args, "port"),
                    remote_path=str(args["remotePath"]),
                )
            if job.command == "workspace.statvfs":
                return self._session.workspace_statvfs(
                    port=_optional_arg_string(args, "port"),
                    remote_path=str(args["remotePath"]),
                )
            if job.command == "workspace.read-file":
                return self._session.workspace_read_file(
                    port=_optional_arg_string(args, "port"),
                    remote_path=str(args["remotePath"]),
                )
            if job.command == "workspace.write-file":
                return self._session.workspace_write_file(
                    port=_optional_arg_string(args, "port"),
                    remote_path=str(args["remotePath"]),
                    content_base64=str(args["contentBase64"]),
                    create=bool(args.get("create", False)),
                    overwrite=bool(args.get("overwrite", False)),
                )
            if job.command == "workspace.create-directory":
                return self._session.workspace_create_directory(
                    port=_optional_arg_string(args, "port"),
                    remote_path=str(args["remotePath"]),
                )
            if job.command == "workspace.delete":
                return self._session.workspace_delete(
                    port=_optional_arg_string(args, "port"),
                    remote_path=str(args["remotePath"]),
                    recursive=bool(args.get("recursive", False)),
                )
            if job.command == "workspace.rename":
                return self._session.workspace_rename(
                    port=_optional_arg_string(args, "port"),
                    old_path=str(args["oldPath"]),
                    new_path=str(args["newPath"]),
                    overwrite=bool(args.get("overwrite", False)),
                )
            if job.command == "workspace.sync":
                return self._session.workspace_sync_filesystem(
                    port=_optional_arg_string(args, "port"),
                )
            if job.command == "workspace.import":
                return self._session.import_workspace(
                    port=_optional_arg_string(args, "port"),
                    local_folder=str(args["localFolder"]),
                    remote_paths=_optional_arg_string_list(args, "remotePaths"),
                    progress_callback=(
                        lambda line, req_id=job.request_id: _service_emit(
                            {"id": req_id, "type": "stream", "stream": "stdout", "line": line}
                        )
                    ) if job.stream else None,
                )
            return {"ok": False, "error": f"Unsupported command: {job.command}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

def _optional_arg_string(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def _optional_arg_string_list(args: dict[str, Any], key: str) -> list[str] | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = value
    elif isinstance(value, set):
        items = sorted(value, key=str)
    else:
        items = [value]
    normalized = [str(item).strip() for item in items if str(item).strip()]
    return normalized or None

def serve_loop() -> int:
    dispatcher = JobDispatcher()
    _service_emit({"type": "ready"})
    try:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue

            request_id: str | None = None
            try:
                request = json.loads(line)
                request_id = str(request.get("id")) if request.get("id") is not None else ""
                command = str(request.get("command", ""))
                args = dict(request.get("args") or {})
                stream = bool(request.get("stream"))
            except Exception as exc:
                _service_emit({
                    "id": request_id,
                    "type": "result",
                    "payload": {"ok": False, "error": f"Invalid request: {exc}"},
                })
                continue

            if command == "shutdown":
                _service_emit({"id": request_id, "type": "result", "payload": {"ok": True}})
                break
            if command == "scan":
                _service_emit({"id": request_id, "type": "result", "payload": {"ok": True, "devices": list_detected_esp_ports()}})
                continue
            if command == "run.cancel":
                _service_emit({"id": request_id, "type": "result", "payload": dispatcher.cancel_active_run()})
                continue
            if command == "session.abort":
                _service_emit({
                    "id": request_id,
                    "type": "result",
                    "payload": dispatcher.abort_session_activity(reason=str(args.get("reason") or "aborted")),
                })
                continue

            dispatcher.submit(request_id, command, args, stream)
    finally:
        dispatcher.shutdown()
    return 0

__all__ = [name for name, value in globals().items() if getattr(value, '__module__', None) == __name__]
