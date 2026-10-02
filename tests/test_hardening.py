from __future__ import annotations

import os
import resource
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import mounted_volume, write_credential

from mcp_server_diwall_browser import credentials, hardening
from mcp_server_diwall_browser.origin import Origin, parse_origin


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


Resolver = Callable[..., credentials.Secret]


def _core_dumps_disabled_before_credential_open(
    resolve_impl: Resolver, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> bool:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    events: list[str] = []

    real_disable = hardening.disable_core_dumps
    real_open = os.open

    def spy_disable() -> None:
        events.append("disable_core_dumps")
        real_disable()

    def spy_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(paths.credentials) in os.fspath(path):  # type: ignore[arg-type]
            events.append("open_credential")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hardening, "disable_core_dumps", spy_disable)
    monkeypatch.setattr(os, "open", spy_open)
    try:
        resolve_impl(volume, parse_origin("https://example.invalid"), "password")
    finally:
        monkeypatch.undo()
    assert "open_credential" in events, "the scenario must reach the credential file"
    return "disable_core_dumps" in events and events.index("disable_core_dumps") < events.index("open_credential")


def _resolve_that_opens_first(volume: object, page_origin: Origin, key: str, name: str | None = None) -> credentials.Secret:
    # Defective copy: touches the credential file before hardening the process.
    root = volume.require_mounted()  # type: ignore[attr-defined]
    fd = os.open(root / "credentials" / "service.json", os.O_RDONLY)
    os.close(fd)
    return credentials.resolve(volume, page_origin, key, name)  # type: ignore[arg-type]


def test_core_dumps_are_disabled_before_a_credential_file_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _core_dumps_disabled_before_credential_open(credentials.resolve, tmp_path, monkeypatch)


def test_core_dump_ordering_check_catches_a_resolver_that_opens_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not _core_dumps_disabled_before_credential_open(_resolve_that_opens_first, tmp_path, monkeypatch)
