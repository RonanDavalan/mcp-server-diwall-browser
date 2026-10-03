"""Reading a file anywhere under the volume, and creating a file whole or not at all."""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import FAKE_EUID, OTHER_DEV, FakeStat, fake_mountinfo, make_volume

from mcp_server_diwall_browser import mount
from mcp_server_diwall_browser.mount import Volume, VolumeFileError
from mcp_server_diwall_browser.output import ToolError

Reader = Callable[[Volume, str], bytes]


def private_file(path: Path, data: bytes = b"data", mode: int = 0o600) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, mode)
    return path


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Path, Volume, mount.MountChecker]:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point)
    return tmp_path, paths.mount_point, Volume(fake.checker, paths.mount_point), fake.checker


def real_reader(volume: Volume, path: str) -> bytes:
    return volume.read_file_at(path, max_bytes=1024).data


def refusal(volume: Volume, path: str, reader: Reader = real_reader) -> str | None:
    """The reason the volume gives for refusing ``path``, or None when it gave the data."""
    try:
        reader(volume, path)
    except VolumeFileError as exc:
        return exc.reason
    return None


# --- read_file_at ---------------------------------------------------------------------------------


def test_a_private_file_beside_the_project_directory_is_read(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    _tmp, mount_point, volume, _checker = setup
    path = private_file(mount_point / "old" / "a.json", b"content")
    assert volume.read_file_at(path, max_bytes=1024).data == b"content"
    # A path written with dots is read as it leads.
    assert volume.read_file_at(f"{mount_point}/old/../old/a.json", max_bytes=1024).data == b"content"


@pytest.mark.parametrize("kind", ["outside", "link_to_outside", "dotdot_outside"])
def test_a_path_that_leads_out_of_the_volume_is_refused(setup: tuple[Path, Path, Volume, mount.MountChecker], kind: str) -> None:
    tmp, mount_point, volume, _checker = setup
    elsewhere = private_file(tmp / "elsewhere" / "b.json")
    if kind == "outside":
        given = str(elsewhere)
    elif kind == "link_to_outside":
        (mount_point / "old").mkdir(mode=0o700)
        os.symlink(elsewhere, mount_point / "old" / "link.json")
        given = str(mount_point / "old" / "link.json")
    else:
        given = f"{mount_point}/../elsewhere/b.json"
    assert refusal(volume, given) == "outside_volume"


def test_a_link_followed_below_the_volume_directory_is_refused(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    _tmp, mount_point, volume, _checker = setup
    real = private_file(mount_point / "old" / "a.json")
    os.symlink(real, mount_point / "old" / "last.json")
    os.symlink(mount_point / "old", mount_point / "middle")
    assert refusal(volume, str(mount_point / "old" / "last.json")) == "link"
    assert refusal(volume, str(mount_point / "middle" / "a.json")) == "link"
    assert refusal(volume, str(real)) is None


def test_the_volume_directory_may_be_reached_through_a_link_above_it(tmp_path: Path) -> None:
    # The operator wrote the profile with a link in the path to the volume: that is not below it.
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point)
    through = tmp_path / "shortcut"
    os.symlink(paths.mount_point, through)
    volume = Volume(fake.checker, through)
    path = private_file(paths.mount_point / "old" / "a.json")
    assert volume.read_file_at(through / "old" / "a.json", max_bytes=1024).data == b"data"
    assert refusal(volume, str(path)) is None


def test_a_file_on_another_device_than_the_volume_is_refused(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    nested = private_file(paths.mount_point / "nested" / "a.json")
    fake = fake_mountinfo(paths.mount_point)
    fake.checker.stat_fn = FakeStat(paths.mount_point, {os.path.realpath(nested): OTHER_DEV})  # type: ignore[assignment]
    assert refusal(Volume(fake.checker, paths.mount_point), str(nested)) == "outside_volume"


@pytest.mark.parametrize(
    ("mode", "reason"),
    [(0o644, "bad_mode"), (0o640, "bad_mode"), (0o660, "bad_mode"), (0o606, "bad_mode"), (0o700, None), (0o400, None)],
)
def test_the_file_must_have_no_right_for_group_or_others(
    setup: tuple[Path, Path, Volume, mount.MountChecker], mode: int, reason: str | None
) -> None:
    _tmp, mount_point, volume, _checker = setup
    path = private_file(mount_point / "old" / "a.json", mode=mode)
    assert refusal(volume, str(path)) == reason


def test_the_file_must_belong_to_the_user_of_the_server(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    path = private_file(paths.mount_point / "old" / "a.json")
    other = fake_mountinfo(paths.mount_point, user_id=FAKE_EUID + 1, euid=FAKE_EUID + 1)
    assert refusal(Volume(other.checker, paths.mount_point), str(path)) == "bad_owner"


def test_other_files_than_regular_ones_a_missing_one_and_a_large_one_are_refused(
    setup: tuple[Path, Path, Volume, mount.MountChecker],
) -> None:
    _tmp, mount_point, volume, _checker = setup
    (mount_point / "old").mkdir(mode=0o700)
    os.mkfifo(mount_point / "old" / "pipe", 0o600)
    big = private_file(mount_point / "old" / "big", b"x" * 2000)
    assert refusal(volume, str(mount_point / "old")) == "not_regular_file"
    assert refusal(volume, str(mount_point / "old" / "pipe")) == "not_regular_file"
    assert refusal(volume, str(mount_point / "old" / "absent")) == "missing"
    assert refusal(volume, str(big)) == "too_large"


def test_a_relative_path_and_a_closed_volume_are_refused(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    tmp, mount_point, volume, _checker = setup
    private_file(mount_point / "old" / "a.json")
    with pytest.raises(ValueError):
        volume.read_file_at("old/a.json", max_bytes=10)
    closed = fake_mountinfo(mount_point, lines=["22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw"])
    with pytest.raises(ToolError) as caught:
        Volume(closed.checker, mount_point).read_file_at(mount_point / "old" / "a.json", max_bytes=10)
    assert caught.value.code == "encrypted_dir_undetermined"


def test_read_check_catches_a_reader_that_judges_the_path_as_written(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    tmp, mount_point, volume, _checker = setup
    elsewhere = private_file(tmp / "elsewhere" / "b.json")
    (mount_point / "old").mkdir(mode=0o700)
    link = mount_point / "old" / "link.json"
    os.symlink(elsewhere, link)

    def by_the_text_of_the_path(_volume: Volume, path: str) -> bytes:
        # Defective: it looks only at where the path begins, and follows what it finds.
        if not path.startswith(str(mount_point)):
            raise VolumeFileError("outside_volume")
        with open(path, "rb") as handle:
            return handle.read()

    assert refusal(volume, str(link)) == "outside_volume"
    assert refusal(volume, str(link), by_the_text_of_the_path) is None


# --- publish_new ---------------------------------------------------------------------------------------


def listing(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def test_a_file_is_published_whole_in_mode_600_and_nothing_else_stays(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    _tmp, mount_point, volume, _checker = setup
    volume.publish_new("credentials", "a.json", data=b"whole")
    directory = mount_point / "mcp-server-diwall-browser" / "credentials"
    assert listing(directory) == ["a.json"]
    assert (directory / "a.json").read_bytes() == b"whole"
    assert (directory / "a.json").stat().st_mode & 0o777 == 0o600


def test_a_name_that_is_taken_is_never_replaced(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    _tmp, mount_point, volume, _checker = setup
    directory = mount_point / "mcp-server-diwall-browser" / "credentials"
    private_file(directory / "a.json", b"first")
    with pytest.raises(FileExistsError):
        volume.publish_new("credentials", "a.json", data=b"second")
    assert (directory / "a.json").read_bytes() == b"first" and listing(directory) == ["a.json"]


def test_a_link_at_the_name_is_refused_and_leads_nowhere(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    tmp, mount_point, volume, _checker = setup
    directory = mount_point / "mcp-server-diwall-browser" / "credentials"
    victim = private_file(tmp / "elsewhere" / "victim", b"victim")
    os.symlink(victim, directory / "a.json")
    with pytest.raises(ToolError) as caught:
        volume.publish_new("credentials", "a.json", data=b"second")
    assert caught.value.code == "volume_path_refused"
    assert victim.read_bytes() == b"victim" and listing(directory) == ["a.json"]


def test_a_write_that_fails_leaves_no_file(setup: tuple[Path, Path, Volume, mount.MountChecker], monkeypatch: pytest.MonkeyPatch) -> None:
    _tmp, mount_point, volume, _checker = setup
    directory = mount_point / "mcp-server-diwall-browser" / "credentials"

    def disk_full(fd: int) -> None:
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(mount.os, "fsync", disk_full)
    with pytest.raises(OSError):
        volume.publish_new("credentials", "a.json", data=b"whole")
    assert listing(directory) == []


def test_publish_check_catches_a_write_that_leaves_its_half_file(
    setup: tuple[Path, Path, Volume, mount.MountChecker], monkeypatch: pytest.MonkeyPatch
) -> None:
    _tmp, mount_point, volume, _checker = setup
    directory = mount_point / "mcp-server-diwall-browser" / "credentials"

    def without_cleaning(self: Volume, path: Path, data: bytes, *, remove_on_failure: bool = True) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.write(fd, data[: len(data) // 2])
        os.close(fd)
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(Volume, "_write_exclusive", without_cleaning)
    with pytest.raises(OSError):
        volume.publish_new("credentials", "a.json", data=b"whole")
    assert listing(directory) != []


def test_a_file_system_that_cannot_link_still_gets_the_file_whole_and_never_replaces(
    setup: tuple[Path, Path, Volume, mount.MountChecker], monkeypatch: pytest.MonkeyPatch
) -> None:
    _tmp, mount_point, volume, _checker = setup
    directory = mount_point / "mcp-server-diwall-browser" / "credentials"

    def cannot_link(source: object, target: object) -> None:
        raise OSError(errno.EPERM, "no links here")

    monkeypatch.setattr(mount.os, "link", cannot_link)
    volume.publish_new("credentials", "a.json", data=b"whole")
    assert listing(directory) == ["a.json"] and (directory / "a.json").read_bytes() == b"whole"
    with pytest.raises(FileExistsError):
        volume.publish_new("credentials", "a.json", data=b"second")
    assert (directory / "a.json").read_bytes() == b"whole" and listing(directory) == ["a.json"]


Publisher = Callable[[Volume, bytes], None]


def replaces_a_taken_name(setup: tuple[Path, Path, Volume, mount.MountChecker], publish: Publisher) -> bool:
    """Whether publishing under a name that is taken changes what the name holds."""
    _tmp, mount_point, volume, _checker = setup
    directory = mount_point / "mcp-server-diwall-browser" / "credentials"
    private_file(directory / "a.json", b"first")
    try:
        publish(volume, b"second")
    except FileExistsError:
        pass
    return (directory / "a.json").read_bytes() != b"first"


def test_a_taken_name_is_not_replaced_by_the_real_publication(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    assert not replaces_a_taken_name(setup, lambda volume, data: volume.publish_new("credentials", "a.json", data=data))


def test_publish_check_catches_a_publication_that_renames_over_the_name(setup: tuple[Path, Path, Volume, mount.MountChecker]) -> None:
    directory = setup[1] / "mcp-server-diwall-browser" / "credentials"

    def by_rename(volume: Volume, data: bytes) -> None:
        # Defective: the hidden file takes the name by renaming, whatever is there.
        hidden = directory / ".a.json.tmp"
        private_file(hidden, data)
        os.rename(hidden, directory / "a.json")

    assert replaces_a_taken_name(setup, by_rename)
