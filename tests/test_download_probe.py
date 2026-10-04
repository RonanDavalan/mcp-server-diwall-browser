"""The read of a downloaded file by the tests: a file the browser renames meanwhile is not a failure of the probe.

The checks are written as functions of the implementation, so that each is also run on a probe
without the protection and shown to fail there.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fixtures.download_probe import holds

Probe = Callable[[Path, Callable[[Path], bool]], bool]


def holds_without_the_protection(path: Path, check: Callable[[Path], bool]) -> bool:
    return path.is_file() and check(path)


def probe_problems(probe: Probe, directory: Path) -> list[str]:
    """What a probe gets wrong; empty when it is right. An exception counts as wrong, not as a pass."""
    whole = directory / "whole"
    whole.write_bytes(b"abcd")
    short = directory / "short"
    short.write_bytes(b"ab")
    gone = directory / "gone"
    gone.write_bytes(b"abcd")

    def vanishes_before_the_read(path: Path) -> bool:
        path.unlink()  # the browser renames the file between the listing and the read
        return path.read_bytes() == b"abcd"

    cases: list[tuple[str, Callable[[], bool], bool]] = [
        ("a file of the right size", lambda: probe(whole, lambda file: file.stat().st_size == 4), True),
        ("a file of the right content", lambda: probe(whole, lambda file: file.read_bytes() == b"abcd"), True),
        ("a file of the wrong size", lambda: probe(short, lambda file: file.stat().st_size == 4), False),
        ("a file of the wrong content", lambda: probe(short, lambda file: file.read_bytes() == b"abcd"), False),
        ("a file that is not there", lambda: probe(directory / "absent", lambda file: file.stat().st_size == 4), False),
        ("a directory", lambda: probe(directory, lambda file: file.stat().st_size == 4), False),
        ("a file that goes away between the listing and the read", lambda: probe(gone, vanishes_before_the_read), False),
    ]
    wrong = []
    for name, run, expected in cases:
        try:
            got = run()
        except OSError as exc:
            wrong.append(f"{name}: raised {type(exc).__name__}")
            continue
        if got is not expected:
            wrong.append(f"{name}: gave {got}")
    return wrong


def test_a_file_that_goes_away_between_the_listing_and_the_read_is_false_and_does_not_raise(tmp_path: Path) -> None:
    assert probe_problems(holds, tmp_path) == []


def test_the_probe_check_fails_on_a_probe_without_the_protection(tmp_path: Path) -> None:
    assert probe_problems(holds_without_the_protection, tmp_path) == [
        "a file that goes away between the listing and the read: raised FileNotFoundError"
    ]
