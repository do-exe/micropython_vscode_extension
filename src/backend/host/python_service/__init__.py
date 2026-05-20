from __future__ import annotations

from . import constants as _constants
from . import errors as _errors
from . import terminal_text as _terminal_text
from . import sync_utils as _sync_utils
from . import serial_controller as _serial_controller
from . import operations as _operations
from . import session as _session
from . import service as _service
from . import cli as _cli

_MODULES = (
    _constants,
    _errors,
    _terminal_text,
    _sync_utils,
    _serial_controller,
    _operations,
    _session,
    _service,
    _cli,
)

__all__: list[str] = []
for _module in _MODULES:
    for _name in getattr(_module, '__all__', ()):  # includes legacy private compatibility exports
        globals()[_name] = getattr(_module, _name)
        __all__.append(_name)

del _MODULES, _module, _name
