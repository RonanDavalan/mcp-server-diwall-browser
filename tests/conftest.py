"""Test factories: a simulated encrypted volume, mount table, credentials and profile.

Why this module exists:
    A gocryptfs volume cannot be mounted where the tests run. The tests need
    a directory tree, a fake mount table and a fake device map that behave
    like a mounted volume, built the same way every time and never shipped
    with the production code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from mcp_server_diwall_browser.mount import PROJECT_DIR_NAME, MountChecker, Volume

FAKE_EUID = os.geteuid()
PARENT_DEV = 100
MOUNT_DEV = 200
OTHER_DEV = 300

FAKE_PASSWORD = "test-secret-value-1"
FAKE_USERNAME = "alice-test-user"


@dataclass
class VolumePaths:
    tmp: Path
    mount_point: Path
    project: Path
    credentials: Path


def make_volume(tmp_path: Path) -> VolumePaths:
    mount_point = tmp_path / "vol"
    project = mount_point / PROJECT_DIR_NAME
    credentials = project / "credentials"
    for directory in (mount_point, project, credentials):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    return VolumePaths(tmp=tmp_path, mount_point=mount_point, project=project, credentials=credentials)


def mountinfo_line(
    mount_point: str | os.PathLike[str],
    fs_type: str = "fuse.gocryptfs",
    user_id: int | None = None,
    extra_options: str = "",
    mount_id: int = 412,
) -> str:
    escaped = (
        os.fspath(mount_point)
        .replace("\\", "\\134")
        .replace(" ", "\\040")
        .replace("\t", "\\011")
        .replace("\n", "\\012")
    )
    uid = FAKE_EUID if user_id is None else user_id
    options = f"rw,nosuid,nodev,relatime,user_id={uid},group_id={uid},max_read=1048576"
    if extra_options:
        options += "," + extra_options
    return (
        f"{mount_id} 28 0:56 / {escaped} rw,nosuid,nodev,relatime shared:230 - "
        f"{fs_type} /srv/plasma-vault/Main.enc {options}"
    )


ROOT_LINE = "22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw,errors=remount-ro"


class FakeStat:
    """os.stat that reports simulated devices: parent, mount point (and below), rest."""

    def __init__(self, mount_point: Path, overrides: dict[str, int] | None = None) -> None:
        self.mount_point = os.path.realpath(mount_point)
        self.parent = os.path.dirname(self.mount_point)
        self.overrides = overrides or {}

    def device_of(self, path: str) -> int:
        real = os.path.realpath(path)
        if real in self.overrides:
            return self.overrides[real]
        if real == self.mount_point or real.startswith(self.mount_point + os.sep):
            return MOUNT_DEV
        if real == self.parent:
            return PARENT_DEV
        return OTHER_DEV

    def __call__(self, path: str) -> os.stat_result:
        real = os.stat(path)
        fields = list(real)
        fields[2] = self.device_of(os.fspath(path))
        return os.stat_result(fields)


@dataclass
class FakeMount:
    text: str
    path: Path
    checker: MountChecker
    stat_fn: FakeStat

    def rewrite(self, text: str) -> None:
        self.path.write_text(text)
        self.text = text


def fake_mountinfo(
    mount_point: str | os.PathLike[str],
    fs_type: str = "fuse.gocryptfs",
    user_id: int | None = None,
    extra_options: str = "",
    *,
    euid: int = FAKE_EUID,
    lines: list[str] | None = None,
) -> FakeMount:
    mount_point = Path(mount_point)
    body = [ROOT_LINE, mountinfo_line(mount_point, fs_type, user_id, extra_options)]
    if lines is not None:
        body = lines
    text = "\n".join(body) + "\n"
    path = mount_point.parent / "mountinfo"
    path.write_text(text)
    stat_fn = FakeStat(mount_point)
    checker = MountChecker(mountinfo_path=os.fspath(path), euid=euid, stat_fn=stat_fn)
    return FakeMount(text=text, path=path, checker=checker, stat_fn=stat_fn)


def mounted_volume(tmp_path: Path) -> tuple[VolumePaths, FakeMount, Volume]:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point)
    return paths, fake, Volume(fake.checker, paths.mount_point)


_DELETE = object()


def write_credential(volume: VolumePaths, name: str, **fields: Any) -> Path:
    content: dict[str, Any] = {
        "allowed_origins": ["https://example.invalid"],
        "username": FAKE_USERNAME,
        "password": FAKE_PASSWORD,
    }
    for key, value in fields.items():
        if value is _DELETE:
            content.pop(key, None)
        else:
            content[key] = value
    path = volume.credentials / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(content, handle)
    os.chmod(path, 0o600)
    return path


def default_profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "schema_version": 1,
        "secrets_dir": "/srv/vaults/Main",
        "allowed_origins": ["https://example.invalid"],
        "browser": {"family": "chromium", "profile_dir": "/srv/vaults/Main/browser-profile"},
    }
    for key, value in overrides.items():
        if value is _DELETE:
            profile.pop(key, None)
        else:
            profile[key] = value
    return profile


def write_profile(tmp_path: Path, **overrides: Any) -> Path:
    directory = tmp_path / "config" / "mcp-server-diwall-browser"
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "profile.json"
    raw = overrides.pop("_raw", None)
    text = raw if raw is not None else json.dumps(default_profile(**overrides))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)
    return path


DELETE = _DELETE


@pytest.fixture
def volume_setup(tmp_path: Path) -> tuple[VolumePaths, FakeMount, Volume]:
    return mounted_volume(tmp_path)


@pytest.fixture(autouse=True)
def fresh_secret_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each test starts with an empty registry so that a one-character secret
    # registered by one test does not redact the expectations of the next.
    from mcp_server_diwall_browser import output

    monkeypatch.setattr(output, "_registry", output._SecretRegistry())
