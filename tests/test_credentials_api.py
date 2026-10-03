"""The parts of the credentials module that other code may call: the name rule, the schema, the directory."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FAKE_EUID, fake_mountinfo, make_volume

from mcp_server_diwall_browser import credentials
from mcp_server_diwall_browser.mount import Volume
from mcp_server_diwall_browser.origin import parse_origin
from mcp_server_diwall_browser.output import ToolError

ORIGIN = parse_origin("https://example.invalid")


@pytest.fixture
def volume(tmp_path: Path) -> tuple[Volume, Path]:
    paths = make_volume(tmp_path)
    return Volume(fake_mountinfo(paths.mount_point).checker, paths.mount_point), paths.credentials


@pytest.mark.parametrize("name", ["a", "shop-admin", "x.y_z-1", "0abc", "a" * 64, "a.json"])
def test_a_name_made_of_lowercase_letters_digits_dots_underscores_and_dashes_is_valid(name: str) -> None:
    assert credentials.valid_name(name) is True


@pytest.mark.parametrize("name", ["", "A", " a", "a ", "a/b", "../a", ".a", "-a", "a" * 65, "ünï", "a\n", None, 3, b"a", ["a"]])
def test_any_other_name_is_not(name: Any) -> None:
    assert credentials.valid_name(name) is False


def test_resolve_applies_the_same_rule_as_the_function_that_is_offered(volume: tuple[Volume, Path]) -> None:
    vol, _directory = volume
    for name in ("shop-admin", "A", "a/b", "x" * 65, ".a"):
        try:
            credentials.resolve(vol, ORIGIN, "password", name)
        except ToolError as refused:
            not_a_name = "pas un nom valide" in refused.message
        else:
            not_a_name = False
        assert not_a_name is (not credentials.valid_name(name)), name


def test_a_file_is_read_as_json_that_repeats_no_key() -> None:
    assert credentials.loads_strict(b'{"a": 1}') == {"a": 1}
    assert credentials.loads_strict(b"[1]") == [1]
    for bad in (b'{"a": 1, "a": 2}', b"{", b"\xff\xfe", b""):
        with pytest.raises(ValueError):
            credentials.loads_strict(bad)


CONTENTS = {
    "good": {"allowed_origins": ["https://example.invalid"], "username": "test-user-1", "password": "test-password-1"},
    "only a password": {"allowed_origins": ["https://example.invalid"], "password": "test-password-1"},
    "no origin": {"username": "test-user-1", "password": "test-password-1"},
    "empty origins": {"allowed_origins": [], "password": "test-password-1"},
    "bad origin": {"allowed_origins": ["example.invalid"], "password": "test-password-1"},
    "origin with a path": {"allowed_origins": ["https://example.invalid/x"], "password": "test-password-1"},
    "unknown key": {"allowed_origins": ["https://example.invalid"], "password": "test-password-1", "totp_cle": "x"},
    "nothing to carry": {"allowed_origins": ["https://example.invalid"]},
    "empty values": {"allowed_origins": ["https://example.invalid"], "username": "", "password": ""},
    "a number": {"allowed_origins": ["https://example.invalid"], "password": 3},
}


def refusal_of(call: Callable[[], object]) -> str | None:
    try:
        call()
    except ToolError as refused:
        assert refused.code == "credential_invalid"
        return str(refused.extra_server.get("reason"))
    return None


def by_validate(raw: bytes) -> str | None:
    return refusal_of(lambda: credentials.validate("a.json", raw))


def by_resolve(volume: Volume, directory: Path, raw: bytes) -> str | None:
    target = directory / "a.json"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    try:
        return refusal_of(lambda: credentials.resolve(volume, ORIGIN, "password", "a"))
    except ToolError as refused:
        assert refused.code in ("credential_not_bound", "credential_invalid")
        return "not bound" if refused.code == "credential_not_bound" else None
    finally:
        target.unlink()


@pytest.mark.parametrize("label", sorted(CONTENTS))
def test_validate_refuses_exactly_what_resolve_refuses(volume: tuple[Volume, Path], label: str) -> None:
    vol, directory = volume
    raw = json.dumps(CONTENTS[label]).encode()
    first, second = by_validate(raw), by_resolve(vol, directory, raw)
    assert first == second or (first is None and second == "key_missing")


def test_validate_refuses_text_that_is_not_json_or_repeats_a_key() -> None:
    assert by_validate(b"{") == "not_json" and by_validate(b"[1]") == "not_json"
    assert by_validate(b'{"allowed_origins": ["https://example.invalid"], "password": "a", "password": "b"}') == "not_json"


def test_validate_check_catches_a_schema_that_accepts_anything(volume: tuple[Volume, Path]) -> None:
    vol, directory = volume
    raw = json.dumps(CONTENTS["unknown key"]).encode()
    assert by_validate(raw) == "unknown_keys"

    def lax(raw: bytes) -> str | None:
        return None

    assert lax(raw) != by_resolve(vol, directory, raw)


def test_the_directory_is_returned_when_it_is_private_and_none_when_it_is_not_made(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    vol = Volume(fake_mountinfo(paths.mount_point).checker, paths.mount_point)
    assert credentials.credentials_directory(vol) == paths.credentials.resolve()
    paths.credentials.rmdir()
    assert credentials.credentials_directory(vol) is None


@pytest.mark.parametrize("fault", ["mode", "link", "file", "owner"])
def test_the_directory_is_refused_when_it_is_not_private_or_not_a_directory(tmp_path: Path, fault: str) -> None:
    paths = make_volume(tmp_path)
    euid = FAKE_EUID
    if fault == "mode":
        os.chmod(paths.credentials, 0o750)
    elif fault == "link":
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        paths.credentials.rmdir()
        os.symlink(other, paths.credentials)
    elif fault == "file":
        paths.credentials.rmdir()
        paths.credentials.write_text("x")
    else:
        euid = FAKE_EUID + 1
    fake = fake_mountinfo(paths.mount_point, user_id=euid, euid=euid)
    with pytest.raises(ToolError) as refused:
        credentials.credentials_directory(Volume(fake.checker, paths.mount_point))
    assert refused.value.code in ("credential_invalid", "volume_path_refused")
