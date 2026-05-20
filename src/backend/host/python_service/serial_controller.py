from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import serial

try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover - Windows runtime
    fcntl = None
    termios = None

from .constants import *
from .errors import *
from .terminal_text import RawLineSink, _has_friendly_prompt, _normalize_friendly_paste_source
from .sync_utils import *

class MicroPythonController:
    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, *, exclusive: bool = False):
        self.port = port
        self._baudrate = baudrate
        self._exclusive = exclusive
        self._in_raw_repl = False
        self._aborted = False
        self._write_lock = threading.Lock()
        self._conn = self._open_connection()

    def abort(self) -> None:
        self._aborted = True
        self.close()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def _ensure_active(self) -> None:
        if self._aborted:
            raise SessionAbortedError("MicroPython device disconnected.")

    def _open_connection(self) -> serial.Serial:
        self._ensure_active()
        conn = serial.Serial()
        conn.port = self.port
        conn.baudrate = self._baudrate
        conn.timeout = 0.01
        conn.write_timeout = 1.0
        try:
            conn.exclusive = self._exclusive
        except Exception:
            pass
        conn.dsrdtr = False
        conn.rtscts = False
        try:
            conn.dtr = False
            conn.rts = False
        except Exception:
            pass
        conn.open()
        self._enable_kernel_exclusive_lock(conn, self._exclusive)
        time.sleep(PORT_OPEN_SETTLE_SEC)
        return conn

    def _enable_kernel_exclusive_lock(self, conn: serial.Serial, exclusive: bool) -> None:
        if not exclusive or fcntl is None or termios is None:
            return

        ioctl_code = getattr(termios, "TIOCEXCL", None)
        if ioctl_code is None:
            return

        try:
            fcntl.ioctl(conn.fileno(), ioctl_code)
        except OSError as exc:
            try:
                conn.close()
            except Exception:
                pass
            raise ControllerError(f"Could not exclusively lock port {self.port}: {exc}") from exc

    def sync_reconnect(self, delay_seconds: float = SYNC_FILE_RETRY_RECONNECT_DELAY_SEC) -> None:
        self._ensure_active()
        with self._write_lock:
            try:
                self._conn.dtr = False
                self._conn.rts = True
                self._conn.dtr = True
                self._conn.rts = False
            except Exception:
                pass

            try:
                self._conn.close()
            except Exception:
                pass

            self._in_raw_repl = False
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            self._ensure_active()
            self._conn = self._open_connection()

    def write_terminal(self, data: bytes) -> None:
        if not data:
            return
        self._write_bytes(data, flush=True)

    def read_terminal_chunk(self) -> bytes:
        self._ensure_active()
        waiting = int(getattr(self._conn, "in_waiting", 0) or 0)
        return self._conn.read(waiting if waiting > 0 else 1)

    def drain_terminal_available(self) -> bytes:
        self._ensure_active()
        drained = bytearray()
        while True:
            self._ensure_active()
            waiting = int(getattr(self._conn, "in_waiting", 0) or 0)
            if waiting <= 0:
                break
            chunk = self._conn.read(waiting)
            if not chunk:
                break
            drained.extend(chunk)
            time.sleep(0.005)
        return bytes(drained)

    def _write_bytes(self, data: bytes, flush: bool = True) -> None:
        if not data:
            return
        self._ensure_active()
        last_exc: Exception | None = None
        for attempt in range(BACKEND_WRITE_RETRIES):
            try:
                self._ensure_active()
                with self._write_lock:
                    sent = 0
                    view = memoryview(data)
                    while sent < len(view):
                        self._ensure_active()
                        wrote = self._conn.write(view[sent:])
                        if wrote is None:
                            wrote = 0
                        if wrote <= 0:
                            raise serial.SerialTimeoutException("Serial write stalled")
                        sent += int(wrote)
                    if flush:
                        self._conn.flush()
                return
            except (serial.SerialException, serial.SerialTimeoutException, OSError, ValueError) as exc:
                last_exc = exc
                time.sleep(0.04 * (attempt + 1))
        if last_exc is not None:
            raise last_exc

    def _drain_serial_input(self) -> None:
        self._ensure_active()
        try:
            self._conn.reset_input_buffer()
            self._conn.reset_output_buffer()
            return
        except Exception:
            pass

        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            self._ensure_active()
            chunk = self.read_terminal_chunk()
            if not chunk:
                break

    def _raw_read_exact(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + max(0.05, timeout)
        out = bytearray()
        while len(out) < size and time.monotonic() < deadline:
            self._ensure_active()
            chunk = self._conn.read(size - len(out))
            if chunk:
                out.extend(chunk)
                continue
            time.sleep(0.005)
        return bytes(out)

    def _raw_read_until(
        self,
        ending: bytes,
        timeout: float | None = 1.0,
        timeout_overall: float | None = None,
        data_consumer: Callable[[bytes], None] | None = None,
        cancel_event: threading.Event | None = None,
        cancel_handler: Callable[[], None] | None = None,
    ) -> bytes:
        data = bytearray()
        begin_overall = begin_char = time.monotonic()
        cancel_deadline: float | None = None
        cancel_triggered = False
        while True:
            self._ensure_active()
            if data.endswith(ending):
                return bytes(data)

            if cancel_event is not None and cancel_event.is_set() and not cancel_triggered:
                cancel_triggered = True
                cancel_deadline = time.monotonic() + RAW_REPL_CANCEL_TIMEOUT_SEC
                if cancel_handler is not None:
                    cancel_handler()

            chunk = self._conn.read(1)
            if chunk:
                if data_consumer is not None:
                    data_consumer(chunk)
                data.extend(chunk)
                begin_char = time.monotonic()
                continue

            now = time.monotonic()
            if timeout is not None and now >= begin_char + timeout:
                return bytes(data)
            if timeout_overall is not None and now >= begin_overall + timeout_overall:
                return bytes(data)
            if cancel_deadline is not None and now >= cancel_deadline:
                return bytes(data)
            time.sleep(0.005)

    def _enter_raw_repl(self, timeout_overall: float = RAW_REPL_ENTER_TIMEOUT_SEC) -> None:
        self._write_bytes(b"\r\x03", flush=True)
        time.sleep(0.05)
        self._drain_serial_input()

        self._write_bytes(b"\r\x01", flush=True)
        data = self._raw_read_until(RAW_REPL_BANNER, timeout=1.0, timeout_overall=timeout_overall)
        if RAW_REPL_BANNER not in data:
            raise ControllerError(f"could not enter raw REPL: {data!r}")

        after_banner = data.split(RAW_REPL_BANNER, 1)[1]
        if b">" not in after_banner:
            prompt = self._raw_read_until(b">", timeout=0.5, timeout_overall=1.0)
            if not prompt.endswith(b">"):
                raise ControllerError(f"raw prompt missing after banner: {prompt!r}")

        self._in_raw_repl = True

    def _exit_raw_repl(self) -> None:
        self._write_bytes(b"\r\x02", flush=True)
        prompt_seen, _ = self._read_until_friendly_prompt(RAW_REPL_EXIT_TIMEOUT_SEC)
        if not prompt_seen:
            raise ControllerError("friendly REPL prompt missing after leaving raw REPL")
        self._in_raw_repl = False

    def _exec_raw_no_follow(self, source: str | bytes) -> None:
        source_bytes = source if isinstance(source, bytes) else source.encode("utf-8")
        for start in range(0, len(source_bytes), RAW_REPL_CHUNK_BYTES):
            chunk = source_bytes[start : start + RAW_REPL_CHUNK_BYTES]
            self._write_bytes(chunk, flush=False)
            time.sleep(RAW_REPL_CHUNK_DELAY_SEC)

        self._write_bytes(b"\x04", flush=True)
        response = self._raw_read_exact(2, timeout=1.0)
        if response != b"OK":
            raise ControllerError(f"could not exec command (response: {response!r})")

    def _raw_follow(
        self,
        timeout: float | None,
        line_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bytes, bytes, bool]:
        sink = RawLineSink(line_callback) if line_callback is not None else None
        interrupted = False
        read_timeout = timeout if timeout is not None and timeout > 0 else None

        def feed_stdout(chunk: bytes) -> None:
            if sink is None or chunk == b"\x04":
                return
            sink.feed(chunk)

        def interrupt_running_code() -> None:
            nonlocal interrupted
            if interrupted:
                return
            interrupted = True
            self._write_bytes(b"\x03", flush=True)

        normal = self._raw_read_until(
            b"\x04",
            timeout=read_timeout,
            timeout_overall=read_timeout,
            data_consumer=feed_stdout if sink is not None else None,
            cancel_event=cancel_event,
            cancel_handler=interrupt_running_code,
        )
        if not normal.endswith(b"\x04"):
            if interrupted:
                raise ControllerError("Run cancel did not reach raw REPL stdout terminator")
            if read_timeout is None:
                raise ControllerError("raw REPL stdout terminator missing after command")
            raise ControllerError(f"run timed out after {read_timeout:g}s waiting for raw REPL stdout terminator")
        normal = normal[:-1]
        if sink is not None:
            sink.flush()

        post_timeout = RAW_REPL_CANCEL_TIMEOUT_SEC if interrupted else read_timeout
        error = self._raw_read_until(b"\x04", timeout=post_timeout, timeout_overall=post_timeout)
        if not error.endswith(b"\x04"):
            if interrupted:
                raise ControllerError("Run cancel did not reach raw REPL stderr terminator")
            if post_timeout is None:
                raise ControllerError("raw REPL stderr terminator missing after command")
            raise ControllerError(f"run timed out after {post_timeout:g}s waiting for raw REPL stderr terminator")
        error = error[:-1]

        prompt_timeout = RAW_REPL_CANCEL_TIMEOUT_SEC if interrupted else 1.0
        prompt = self._raw_read_until(b">", timeout=prompt_timeout, timeout_overall=prompt_timeout)
        if not prompt.endswith(b">"):
            raise ControllerError("raw REPL prompt missing after command")
        return normal, error, interrupted

    def _read_until_friendly_prompt(self, timeout_seconds: float) -> tuple[bool, bytes]:
        output_chunks: list[bytes] = []
        deadline = time.monotonic() + max(0.2, timeout_seconds)

        while time.monotonic() < deadline:
            chunk = self.read_terminal_chunk()
            if not chunk:
                time.sleep(0.05)
                continue
            output_chunks.append(chunk)
            if _has_friendly_prompt(b"".join(output_chunks[-8:])):
                return True, b"".join(output_chunks)

        return False, b"".join(output_chunks)

    def exec_friendly_source_start(self, source: str | bytes) -> dict[str, bytes]:
        pending = self.drain_terminal_available()
        self._write_bytes(b"\r\x03", flush=True)
        time.sleep(SOFT_RESET_BREAK_DELAY_SEC)
        interrupt_output = self.drain_terminal_available()
        if interrupt_output:
            pending += interrupt_output

        self._write_bytes(b"\r\x05", flush=True)
        banner = self._raw_read_until(
            FRIENDLY_PASTE_PROMPT,
            timeout=0.5,
            timeout_overall=FRIENDLY_PASTE_ENTER_TIMEOUT_SEC,
        )
        if not banner.endswith(FRIENDLY_PASTE_PROMPT):
            raise ControllerError(f"friendly paste prompt missing after enter: {banner!r}")

        source_bytes = _normalize_friendly_paste_source(source)
        for start in range(0, len(source_bytes), FRIENDLY_PASTE_CHUNK_BYTES):
            chunk = source_bytes[start : start + FRIENDLY_PASTE_CHUNK_BYTES]
            self._write_bytes(chunk, flush=False)
            time.sleep(FRIENDLY_PASTE_CHUNK_DELAY_SEC)
        self._write_bytes(b"\x04", flush=True)

        return {
            "pending": pending,
        }

    def exec_source(
        self,
        source: str,
        timeout_seconds: float,
        line_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bytes, bytes]:
        self._enter_raw_repl()
        try:
            self._exec_raw_no_follow(source)
            output, error, interrupted = self._raw_follow(
                timeout_seconds if timeout_seconds > 0 else None,
                line_callback=line_callback,
                cancel_event=cancel_event,
            )
        except Exception:
            # On command failure or timeout the device may still be running user code,
            # so raw->friendly recovery is handled by the outer recovery path.
            self._in_raw_repl = False
            raise

        try:
            self._exit_raw_repl()
        finally:
            self._in_raw_repl = False

        if interrupted:
            raise RunCancelledError(output)

        return output, error

    def exec_source_in_raw_repl(
        self,
        source: str,
        timeout_seconds: float,
        line_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bytes, bytes]:
        if not self._in_raw_repl:
            raise ControllerError("raw REPL is not active")
        try:
            self._exec_raw_no_follow(source)
            output, error, interrupted = self._raw_follow(
                timeout_seconds if timeout_seconds > 0 else None,
                line_callback=line_callback,
                cancel_event=cancel_event,
            )
        except Exception:
            self._in_raw_repl = False
            raise

        if interrupted:
            raise RunCancelledError(output)

        return output, error

    def sync_enter_friendly_repl(self) -> None:
        self._write_bytes(b"\x03\x03", flush=True)
        time.sleep(SYNC_REPL_DELAY_SEC)
        self._sync_reset_input_buffer()
        self._write_bytes(b"\x01", flush=True)
        time.sleep(SYNC_REPL_DELAY_SEC)
        self._sync_reset_input_buffer()
        self._write_bytes(b"\x02", flush=True)
        time.sleep(SYNC_REPL_DELAY_SEC)
        self._sync_reset_input_buffer()
        self._in_raw_repl = False

    def sync_enter_raw_repl(self) -> None:
        self._write_bytes(b"\x03\x03", flush=True)
        time.sleep(SYNC_REPL_DELAY_SEC)
        self._sync_reset_input_buffer()
        self._write_bytes(b"\x01", flush=True)
        time.sleep(SYNC_REPL_DELAY_SEC)
        self._sync_reset_input_buffer()
        self._in_raw_repl = True

    def sync_exit_raw_repl(self) -> None:
        self._write_bytes(b"\x03\x03", flush=True)
        time.sleep(SYNC_REPL_DELAY_SEC)
        self._write_bytes(b"\x02", flush=True)
        time.sleep(SYNC_REPL_DELAY_SEC)
        self._sync_reset_input_buffer()
        self._in_raw_repl = False

    def sync_exec_raw_and_read(self, code: str, timeout: float = 5.0) -> str:
        opened_here = False
        if not self._in_raw_repl:
            self._enter_raw_repl(timeout_overall=max(RAW_REPL_ENTER_TIMEOUT_SEC, timeout))
            opened_here = True
        try:
            self._exec_raw_no_follow(code)
            output, error, interrupted = self._raw_follow(
                timeout if timeout > 0 else None,
                line_callback=None,
                cancel_event=None,
            )
        except Exception:
            # Existing raw session might be out of sync after a command failure.
            if not opened_here:
                self._in_raw_repl = False
            raise
        finally:
            if opened_here:
                try:
                    self._exit_raw_repl()
                except Exception:
                    self._in_raw_repl = False
                    raise

        if interrupted:
            raise ControllerError("Sync command interrupted")

        stderr_text = error.decode("utf-8", errors="replace").strip()
        if stderr_text:
            raise ControllerError(stderr_text)

        result = output.decode(errors="ignore")
        if "Traceback" in result:
            raise ControllerError(result)
        return result

    def sync_exec_friendly_and_read(self, code: str, timeout: float = 5.0) -> str:
        payload = self.exec_friendly_source_start(code)
        pending = payload.get("pending", b"")
        prompt_seen, output = self._read_until_friendly_prompt(timeout)
        text = pending.decode("utf-8", errors="replace") + output.decode("utf-8", errors="replace")
        if not prompt_seen:
            snippet = text.strip()
            if snippet:
                raise ControllerError(f"friendly REPL prompt missing after sync command: {snippet[:200]}")
            raise ControllerError("friendly REPL prompt missing after sync command")
        if "Traceback" in text:
            raise ControllerError(text)
        return text

    def sync_get_file_sizes(self, remote_root: str, timeout: float = SYNC_SCAN_COMMAND_TIMEOUT_SEC) -> dict[str, int]:
        code = _device_list_file_sizes_script(remote_root)
        raw = ""
        last_error: Exception | None = None
        stream_code = _device_list_file_sizes_stream_script(remote_root)
        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break
        if last_error is not None:
            try:
                stream_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_sizes_stream_output(stream_raw)
            except Exception as friendly_exc:
                raise ControllerError(f"{last_error} | friendly scan failed: {friendly_exc}") from friendly_exc

        try:
            return _parse_device_sizes_output(raw)
        except ControllerError as exc:
            # Fallback to line-stream parser when large dict repr output is truncated.
            error_text = str(exc)
            if "Device size scan marker missing" not in error_text and "Device size scan returned no output" not in error_text:
                raise

            try:
                stream_raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
            except Exception:
                stream_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
            return _parse_device_sizes_stream_output(stream_raw)

    def sync_get_file_signatures(
        self,
        remote_paths: list[str],
        timeout: float = SYNC_SIGNATURE_SCAN_TIMEOUT_SEC,
    ) -> dict[str, str | None]:
        code = _device_list_file_signatures_script(remote_paths)
        stream_code = _device_list_file_signatures_stream_script(remote_paths)
        raw = ""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break
        if last_error is not None:
            try:
                stream_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_signatures_stream_output(stream_raw)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly signature scan failed: {friendly_exc}"
                ) from friendly_exc

        try:
            return _parse_device_signatures_output(raw)
        except ControllerError as exc:
            # Fallback to line-stream parser when large dict repr output is truncated.
            error_text = str(exc)
            if (
                "Device signature scan marker missing" not in error_text
                and "Device signature scan returned no output" not in error_text
            ):
                raise

            try:
                stream_raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
            except Exception:
                stream_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
            return _parse_device_signatures_stream_output(stream_raw)

    def sync_get_selected_file_sizes(
        self,
        remote_paths: list[str],
        timeout: float = SYNC_TARGETED_VERIFY_TIMEOUT_SEC,
    ) -> dict[str, int | None]:
        if not remote_paths:
            return {}

        result: dict[str, int | None] = {}
        path_batches = _chunk_remote_paths_for_targeted_scan(remote_paths)
        for batch_paths in path_batches:
            batch_timeout = max(2.0, min(timeout, 1.2 + (len(batch_paths) * 0.05)))
            result.update(MicroPythonController._sync_get_selected_file_sizes_batch(self, batch_paths, timeout=batch_timeout))
        return result

    def _sync_get_selected_file_sizes_batch(
        self,
        remote_paths: list[str],
        timeout: float,
    ) -> dict[str, int | None]:
        code = _device_selected_file_sizes_script(remote_paths)
        stream_code = _device_selected_file_sizes_stream_script(remote_paths)

        raw = ""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(code, timeout=timeout)
                try:
                    return _parse_device_selected_sizes_output(friendly_raw)
                except ControllerError:
                    friendly_stream_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                    return _parse_device_selected_sizes_stream_output(friendly_stream_raw)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly targeted size scan failed: {friendly_exc}"
                ) from friendly_exc

        try:
            return _parse_device_selected_sizes_output(raw)
        except ControllerError as exc:
            error_text = str(exc)
            if (
                "Device targeted size scan marker missing" not in error_text
                and "Device targeted size scan returned no output" not in error_text
            ):
                raise

            try:
                stream_raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
            except Exception:
                stream_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
            return _parse_device_selected_sizes_stream_output(stream_raw)

    def sync_scan_tree(
        self,
        remote_root: str,
        timeout: float = SYNC_SCAN_COMMAND_TIMEOUT_SEC,
    ) -> tuple[list[str], dict[str, int]]:
        stream_code = _device_scan_tree_stream_script(remote_root)
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_tree_stream_output(friendly_raw)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly tree scan failed: {friendly_exc}"
                ) from friendly_exc

        return _parse_device_tree_stream_output(raw)

    def sync_read_file_bytes(
        self,
        remote_path: str,
        timeout: float,
    ) -> bytes:
        stream_code = _device_read_file_hex_stream_script(
            remote_path,
            chunk_bytes=WORKSPACE_READ_FILE_CHUNK_BYTES,
        )
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_file_hex_output(friendly_raw, remote_path=remote_path)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly file read failed for {remote_path}: {friendly_exc}"
                ) from friendly_exc

        return _parse_device_file_hex_output(raw, remote_path=remote_path)

    def sync_read_file_text(
        self,
        remote_path: str,
        timeout: float,
    ) -> str:
        stream_code = _device_read_text_file_stream_script(
            remote_path,
            chunk_chars=WORKSPACE_READ_TEXT_CHUNK_CHARS,
        )
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_text_file_output(friendly_raw, remote_path=remote_path)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly file read failed for {remote_path}: {friendly_exc}"
                ) from friendly_exc

        return _parse_device_text_file_output(raw, remote_path=remote_path)

    def sync_stat_path(
        self,
        remote_path: str,
        timeout: float = SYNC_DIR_COMMAND_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        stream_code = _device_stat_path_script(remote_path)
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_stat_output(friendly_raw, remote_path=remote_path)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly stat failed for {remote_path}: {friendly_exc}"
                ) from friendly_exc

        return _parse_device_stat_output(raw, remote_path=remote_path)

    def sync_list_directory(
        self,
        remote_path: str,
        timeout: float = SYNC_DIR_COMMAND_TIMEOUT_SEC,
    ) -> list[dict[str, Any]]:
        stream_code = _device_list_directory_stream_script(remote_path)
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_list_directory_output(friendly_raw)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly directory listing failed for {remote_path}: {friendly_exc}"
                ) from friendly_exc

        return _parse_device_list_directory_output(raw)

    def sync_statvfs_path(
        self,
        remote_path: str,
        timeout: float = SYNC_DIR_COMMAND_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        stream_code = _device_statvfs_script(remote_path)
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_statvfs_output(friendly_raw, remote_path=remote_path)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly statvfs failed for {remote_path}: {friendly_exc}"
                ) from friendly_exc

        return _parse_device_statvfs_output(raw, remote_path=remote_path)

    def sync_filesystem(self, timeout: float = SYNC_DIR_COMMAND_TIMEOUT_SEC) -> bool:
        stream_code = _device_sync_script()
        raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
        return _parse_device_sync_output(raw)

    def sync_mkdir(self, path: str) -> bool:
        target = json.dumps(path)
        code = (
            "import os\r\n"
            "try:\r\n"
            f"    os.mkdir({target})\r\n"
            "except:\r\n"
            "    pass\r\n"
            "try:\r\n"
            f"    os.stat({target})\r\n"
            "    print('EXISTS')\r\n"
            "except:\r\n"
            "    print('MISSING')\r\n"
        )
        result = self.sync_exec_raw_and_read(code, timeout=1.0)
        return "EXISTS" in result

    def sync_mkdir_recursive(self, path: str, timeout: float = SYNC_DIR_COMMAND_TIMEOUT_SEC) -> bool:
        code = _device_mkdir_script(path)
        result = self.sync_exec_raw_and_read(code, timeout=timeout)
        if "Traceback" in result:
            raise ControllerError(result.strip())
        try:
            stat = self.sync_stat_path(path, timeout=timeout)
        except Exception:
            return False
        return stat.get("kind") == "directory"

    def sync_delete_file(self, path: str) -> bool:
        target = json.dumps(path)
        code = (
            "import os\r\n"
            "try:\r\n"
            f"    os.remove({target})\r\n"
            "    print('DELETED')\r\n"
            "except Exception as e:\r\n"
            "    print('ERROR:' + str(e))\r\n"
        )
        result = self.sync_exec_raw_and_read(code, timeout=3.0)
        return "DELETED" in result

    def sync_delete_path(
        self,
        remote_path: str,
        recursive: bool,
        timeout: float = SYNC_DIR_COMMAND_TIMEOUT_SEC,
    ) -> str:
        stream_code = _device_delete_path_script(remote_path, recursive=recursive)
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                return _parse_device_delete_path_output(friendly_raw, remote_path=remote_path)
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly delete failed for {remote_path}: {friendly_exc}"
                ) from friendly_exc

        return _parse_device_delete_path_output(raw, remote_path=remote_path)

    def sync_rename_path(
        self,
        old_path: str,
        new_path: str,
        timeout: float = SYNC_DIR_COMMAND_TIMEOUT_SEC,
    ) -> None:
        stream_code = _device_rename_path_script(old_path, new_path)
        raw = ""
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                raw = self.sync_exec_raw_and_read(stream_code, timeout=timeout)
                last_error = None
                break
            except ControllerError as exc:
                last_error = exc
                if attempt == 0:
                    self.sync_enter_friendly_repl()
                    continue
                break

        if last_error is not None:
            try:
                friendly_raw = self.sync_exec_friendly_and_read(stream_code, timeout=timeout)
                _parse_device_rename_path_output(friendly_raw, old_path=old_path, new_path=new_path)
                return
            except Exception as friendly_exc:
                raise ControllerError(
                    f"{last_error} | friendly rename failed for {old_path} -> {new_path}: {friendly_exc}"
                ) from friendly_exc

        _parse_device_rename_path_output(raw, old_path=old_path, new_path=new_path)

    def sync_clear_all(self, timeout: float = SYNC_CLEAR_ALL_TIMEOUT_SEC) -> str:
        return self.sync_exec_raw_and_read(_device_clear_all_script(), timeout=timeout)

    def sync_put_content(self, remote_path: str, data: bytes, timeout: float | None = None) -> None:
        code = _device_put_file_script(remote_path, data)
        result = self.sync_exec_raw_and_read(
            code,
            timeout=timeout if timeout is not None else _estimate_sync_source_timeout(
                code,
                minimum_seconds=SYNC_FILE_UPLOAD_TIMEOUT_SEC,
            ),
        )
        if "Traceback" in result:
            raise ControllerError(result.strip())
        for raw_line in result.replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if line.startswith("PUTERR:"):
                raise _parse_workspace_error_payload(line[len("PUTERR:") :])
        if "OK" not in result:
            preview = result.strip()
            raise ControllerError(f"No OK confirmation: {preview[:200]}")

    def sync_put_raw(self, local_path: Path, remote_path: str) -> None:
        if not self._in_raw_repl:
            raise ControllerError("raw REPL is not active")

        data = local_path.read_bytes()
        total_len = len(data)
        num_chunks = (total_len + SYNC_FILE_SCRIPT_CHUNK_BYTES - 1) // SYNC_FILE_SCRIPT_CHUNK_BYTES

        self._sync_reset_input_buffer()

        lines = [
            "import os",
            "def _emit_err(_prefix, _exc):",
            "    _errno = getattr(_exc, 'errno', None)",
            "    if _errno is None:",
            "        try:",
            "            _errno = int(_exc.args[0]) if getattr(_exc, 'args', None) else None",
            "        except:",
            "            _errno = None",
            "    print(_prefix + ':' + ('' if _errno is None else str(_errno)) + ':' + str(_exc))",
            "try:",
            "    try:",
            f"        os.remove({json.dumps(remote_path)})",
            "    except OSError:",
            "        pass",
            f"    f = open({json.dumps(remote_path)}, \"wb\")",
            "    try:",
        ]
        for index in range(num_chunks):
            chunk = data[index * SYNC_FILE_SCRIPT_CHUNK_BYTES : (index + 1) * SYNC_FILE_SCRIPT_CHUNK_BYTES]
            lines.append(f"        f.write({repr(chunk)})")
        if num_chunks == 0:
            lines.append("        pass")
        lines.extend([
            "    finally:",
            "        f.close()",
            '    print("OK")',
            "except Exception as _exc:",
            "    _emit_err('PUTERR', _exc)",
        ])

        code = "\r\n".join(lines) + "\r\n"
        self._exec_raw_no_follow(code)
        output, error, interrupted = self._raw_follow(
            SYNC_FILE_UPLOAD_TIMEOUT_SEC,
            line_callback=None,
            cancel_event=None,
        )
        if interrupted:
            raise ControllerError("Sync upload interrupted")

        stderr_text = error.decode("utf-8", errors="replace").strip()
        if stderr_text:
            raise ControllerError(stderr_text)

        stdout_text = output.decode("utf-8", errors="replace")
        if "Traceback" in stdout_text:
            raise ControllerError(stdout_text.strip())
        for raw_line in stdout_text.replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if line.startswith("PUTERR:"):
                raise _parse_workspace_error_payload(line[len("PUTERR:") :])
        if "OK" not in stdout_text:
            preview = stdout_text.strip()
            raise ControllerError(f"No OK confirmation: {preview[:200]}")

    def _sync_reset_input_buffer(self) -> None:
        try:
            self._conn.reset_input_buffer()
        except Exception:
            pass

    def recover_friendly_repl(self, timeout_seconds: float) -> dict[str, Any]:
        self._drain_serial_input()
        self._write_bytes(b"\x03\x03", flush=True)
        time.sleep(SOFT_RESET_BREAK_DELAY_SEC)
        self._write_bytes(b"\r\x02\r", flush=True)
        prompt_seen, output_bytes = self._read_until_friendly_prompt(timeout_seconds)
        output = output_bytes.decode("utf-8", errors="replace")
        payload = {
            "ok": prompt_seen,
            "promptSeen": prompt_seen,
            "port": self.port,
            "output": output,
        }
        if not prompt_seen:
            payload["error"] = "Friendly REPL prompt not detected after run recovery."
        return payload

    def recover_friendly_prompt(self, timeout_seconds: float) -> dict[str, Any]:
        self._write_bytes(b"\x03\r", flush=True)
        prompt_seen, output_bytes = self._read_until_friendly_prompt(timeout_seconds)
        output = output_bytes.decode("utf-8", errors="replace")
        payload = {
            "ok": prompt_seen,
            "promptSeen": prompt_seen,
            "port": self.port,
            "output": output,
        }
        if not prompt_seen:
            payload["error"] = "Friendly REPL prompt not detected after interactive run recovery."
        return payload

    def soft_reset(self, timeout_seconds: float) -> dict[str, Any]:
        output_chunks: list[bytes] = []
        prompt_seen = False
        reboot_seen = False

        def collect(deadline: float) -> None:
            nonlocal prompt_seen, reboot_seen
            while time.monotonic() < deadline:
                chunk = self.read_terminal_chunk()
                if not chunk:
                    time.sleep(0.05)
                    continue
                output_chunks.append(chunk)
                merged = b"".join(output_chunks[-8:])
                if any(marker in merged for marker in SOFT_RESET_REBOOT_MARKERS):
                    reboot_seen = True
                if _has_friendly_prompt(merged):
                    prompt_seen = True
                    reboot_seen = True
                    return

        self._drain_serial_input()
        self._write_bytes(b"\x03\x03", flush=True)
        time.sleep(SOFT_RESET_BREAK_DELAY_SEC)
        self._drain_serial_input()
        self._write_bytes(b"\x04", flush=True)
        collect(time.monotonic() + max(0.2, timeout_seconds))

        if not reboot_seen and not prompt_seen:
            self._write_bytes(b"\x03\x03", flush=True)
            time.sleep(0.05)
            self._write_bytes(b"\x02", flush=True)
            time.sleep(0.03)
            self._write_bytes(b"\x04", flush=True)
            collect(time.monotonic() + SOFT_RESET_TIMEOUT_FALLBACK_SEC)

        return {
            "ok": bool(prompt_seen or reboot_seen),
            "promptSeen": prompt_seen,
            "rebootSeen": reboot_seen,
            "port": self.port,
            "output": b"".join(output_chunks).decode("utf-8", errors="replace"),
        }

__all__ = ['MicroPythonController']
