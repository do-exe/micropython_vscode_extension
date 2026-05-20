from __future__ import annotations

import re
from typing import Callable

from .constants import FRIENDLY_REPL_PROMPTS
from .errors import SessionAbortedError


class RawLineSink:
    def __init__(self, emit: Callable[[str], None]):
        self._emit = emit
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._buf.extend(chunk)
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                return
            line = bytes(self._buf[:idx]).decode("utf-8", errors="replace").rstrip("\r")
            del self._buf[: idx + 1]
            self._emit(line)

    def flush(self) -> None:
        if not self._buf:
            return
        line = bytes(self._buf).decode("utf-8", errors="replace").rstrip("\r")
        self._buf.clear()
        self._emit(line)


def _has_friendly_prompt(data: bytes) -> bool:
    tail = bytes(data[-128:])
    parts = re.split(br"[\r\n]+", tail)
    last_line = parts[-1] if parts else tail
    stripped = last_line.lstrip()
    for prompt in FRIENDLY_REPL_PROMPTS:
        if stripped.startswith(prompt):
            return True
        if prompt in stripped and stripped.rstrip().endswith(prompt):
            return True
    return False


def _join_non_empty_text(parts: list[str]) -> str:
    return "".join(part for part in parts if part)


def _normalize_friendly_paste_source(source: str | bytes) -> bytes:
    if isinstance(source, bytes):
        text = source.decode("utf-8")
    else:
        text = source
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _is_disconnect_error_text(error_text: str) -> bool:
    lowered = error_text.lower()
    needles = (
        "input/output error",
        "could not open port",
        "no such file or directory",
        "attempting to use a port that is not open",
        "device reports readiness to read but returned no data",
        "device disconnected",
        "session aborted",
    )
    return any(needle in lowered for needle in needles)


def _should_abort_for_exception(exc: Exception) -> bool:
    return isinstance(exc, SessionAbortedError) or _is_disconnect_error_text(str(exc))


def _strip_repl_prompt_prefix(text: str) -> str:
    return re.sub(r"^(?:(?:.*?\s)?>>>|\.\.\.)\s*", "", text)


def _is_prompt_only_fragment(text: str) -> bool:
    fragment = text.replace("\r", "").strip()
    if not fragment:
        return False
    cleaned = _strip_repl_prompt_prefix(fragment)
    if cleaned:
        return False
    return _has_friendly_prompt(fragment.encode("utf-8", errors="ignore"))


__all__ = [name for name, value in globals().items() if getattr(value, "__module__", None) == __name__]
