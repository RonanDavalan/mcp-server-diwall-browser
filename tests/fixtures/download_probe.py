"""Read a file the browser may rename while the test looks at it.

Why this module exists:
    A download ends in a file the browser creates under a temporary name and renames when it is
    complete. A test that lists the directory and then reads a file can lose the race: the file it
    listed is gone when it reads it, and the error belongs to the probe, not to the download.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def holds(path: Path, check: Callable[[Path], bool]) -> bool:
    """True when ``path`` is a file and ``check(path)`` is true; False when it is not, or goes away meanwhile."""
    try:
        return path.is_file() and check(path)
    except OSError:
        return False
