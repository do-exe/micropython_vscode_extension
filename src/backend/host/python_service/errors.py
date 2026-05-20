from __future__ import annotations

class ControllerError(RuntimeError):
    pass

class WorkspaceOperationError(ControllerError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code

class RunCancelledError(ControllerError):
    def __init__(self, output: bytes):
        super().__init__("Run cancelled by user")
        self.output = output

class SessionAbortedError(ControllerError):
    pass

__all__ = [name for name, value in globals().items() if getattr(value, '__module__', None) == __name__]
