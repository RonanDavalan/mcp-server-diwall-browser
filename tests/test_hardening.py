from __future__ import annotations

import os
import resource
from pathlib import Path

import pytest

from mcp_server_diwall_browser import hardening


def test_disable_core_dumps_sets_both_limits_to_zero_and_is_idempotent() -> None:
    hardening.disable_core_dumps()
    hardening.disable_core_dumps()
    assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)


def test_disable_core_dumps_fails_closed_when_the_kernel_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object) -> None:
        raise OSError("refused")

    monkeypatch.setattr(resource, "setrlimit", refuse)
    with pytest.raises(hardening.HardeningError):
        hardening.disable_core_dumps()


def test_disable_core_dumps_fails_closed_when_the_limit_did_not_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(resource, "getrlimit", lambda _which: (1024, 1024))
    with pytest.raises(hardening.HardeningError):
        hardening.disable_core_dumps()


def test_restrict_umask_makes_new_files_private(tmp_path: Path) -> None:
    previous = hardening.restrict_umask()
    try:
        path = tmp_path / "created"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o666)
        os.close(fd)
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(previous)
