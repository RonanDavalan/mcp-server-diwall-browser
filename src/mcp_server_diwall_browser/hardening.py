"""Put the process into a safe state before any secret is loaded.

Why this module exists:
    Clearing a Python string after use does not remove its bytes from memory,
    so a core dump written to disk after a crash would hold every resolved
    secret. The only reliable defence is to forbid core dumps before the
    first secret is read, and to refuse to read one if that cannot be done.
"""

from __future__ import annotations

import os
import resource


class HardeningError(RuntimeError):
    """The process could not be put into the state required to hold secrets."""


def disable_core_dumps() -> None:
    """Set both core-dump limits to zero; raise if the kernel refuses.

    Calling it again is harmless: lowering an already-zero limit to zero is
    accepted, and the limit is re-read so a silent failure cannot pass.
    """
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    except (ValueError, OSError) as exc:
        raise HardeningError("core dumps could not be disabled") from exc
    if soft != 0 or hard != 0:
        raise HardeningError("core dump limit is not zero after setting it")


def restrict_umask() -> int:
    """Make every file the process creates private by default.

    Returns the previous mask so the caller can log it if needed.
    """
    return os.umask(0o077)
