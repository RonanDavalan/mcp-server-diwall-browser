from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import (
    FAKE_EUID,
    MOUNT_DEV,
    OTHER_DEV,
    ROOT_LINE,
    VolumePaths,
    fake_mountinfo,
    make_volume,
    mounted_volume,
    mountinfo_line,
)

from mcp_server_diwall_browser.mount import (
    MountChecker,
    MountInfoError,
    MountState,
    Volume,
    parse_mountinfo,
    safe_name,
)
from mcp_server_diwall_browser.output import ToolError

REAL_LINE = (
    "412 28 0:56 / ~/Vaults/Main rw,nosuid,nodev,relatime shared:230 - fuse.gocryptfs "
    "~/.local/share/plasma-vault/Main.enc "
    "rw,nosuid,nodev,relatime,user_id=1000,group_id=1000,max_read=1048576"
)


def test_parse_mountinfo_reads_mount_point_type_and_super_options() -> None:
    [entry] = parse_mountinfo(REAL_LINE + "\n")
    assert entry.mount_point == "~/Vaults/Main"
    assert entry.fs_type == "fuse.gocryptfs"
    assert "user_id=1000" in entry.super_options
    assert "allow_other" not in entry.super_options


def test_parse_mountinfo_decodes_octal_escapes() -> None:
    line = REAL_LINE.replace("~/Vaults/Main", "~/My\\040Vault\\011x\\012y\\134z")
    [entry] = parse_mountinfo(line)
    assert entry.mount_point == "~/My Vault\tx\ny\\z"


def test_parse_mountinfo_accepts_lines_without_optional_fields() -> None:
    [entry] = parse_mountinfo("36 35 98:0 /mnt1 /mnt2 rw,noatime - ext3 /dev/root rw,errors=continue")
    assert entry.mount_point == "/mnt2"


@pytest.mark.parametrize(
    "text",
    [
        "412 28 0:56 / /mnt rw shared:1 fuse.gocryptfs src rw",
        "412 28 0:56 / /mnt rw - fuse.gocryptfs src",
        "412 28 0:56 / /mnt rw - fuse.gocryptfs src rw extra",
        "412 28 - fuse.gocryptfs src rw",
        ROOT_LINE + "\n\n" + REAL_LINE,
    ],
)
def test_malformed_mountinfo_line_raises(text: str) -> None:
    with pytest.raises(MountInfoError):
        parse_mountinfo(text)


def test_mounted_volume_is_recognized(tmp_path: Path) -> None:
    _paths, fake, _volume = mounted_volume(tmp_path)
    verdict = fake.checker.check(tmp_path / "vol")
    assert verdict.state is MountState.MOUNTED
    assert verdict.error_code is None


def test_mount_point_with_a_space_is_recognized(tmp_path: Path) -> None:
    base = tmp_path / "with space"
    base.mkdir()
    paths = make_volume(base)
    fake = fake_mountinfo(paths.mount_point)
    assert "\\040" in fake.text
    assert fake.checker.check(paths.mount_point).state is MountState.MOUNTED


# --- prefix: the check and its counter-proof --------------------------------------

Detector = Callable[[str, str], bool]


def real_detector(mountinfo_text: str, secrets_dir: str) -> bool:
    return any(entry.mount_point == secrets_dir for entry in parse_mountinfo(mountinfo_text))


def substring_detector(mountinfo_text: str, secrets_dir: str) -> bool:
    # Defective: looks for the path anywhere in the table.
    return secrets_dir in mountinfo_text


def prefix_is_not_taken_for_the_mount(detect: Detector) -> bool:
    text = REAL_LINE.replace("~/Vaults/Main ", "~/Vaults/Main-old ")
    return detect(text, "~/Vaults/Main") is False and detect(REAL_LINE, "~/Vaults/Main") is True


def test_mount_point_name_prefix_is_not_taken_for_the_mount() -> None:
    assert prefix_is_not_taken_for_the_mount(real_detector)


def test_prefix_check_catches_a_substring_detector() -> None:
    assert not prefix_is_not_taken_for_the_mount(substring_detector)


def test_checker_does_not_take_a_prefix_mount_for_the_volume(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point, lines=[ROOT_LINE, mountinfo_line(f"{paths.mount_point}-old")])
    verdict = fake.checker.check(paths.mount_point)
    assert verdict.state is not MountState.MOUNTED
    assert verdict.state is MountState.CLOSED_OR_ABSENT


# --- refusals ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"fs_type": "tmpfs"}, "fs_type"),
        ({"fs_type": "fuse.sshfs"}, "fs_type"),
        ({"extra_options": "allow_other"}, "allow_other"),
        ({"user_id": FAKE_EUID + 1}, "user_id"),
    ],
)
def test_mount_that_is_not_a_private_gocryptfs_volume_is_refused(
    tmp_path: Path, kwargs: dict[str, object], reason: str
) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point, **kwargs)  # type: ignore[arg-type]
    verdict = fake.checker.check(paths.mount_point)
    assert verdict.state is MountState.NOT_GOCRYPTFS
    assert verdict.error_code == "mount_not_gocryptfs"
    assert verdict.reason == reason


def test_stacked_mount_uses_the_last_entry(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    lines = [ROOT_LINE, mountinfo_line(paths.mount_point), mountinfo_line(paths.mount_point, "tmpfs", mount_id=500)]
    fake = fake_mountinfo(paths.mount_point, lines=lines)
    verdict = fake.checker.check(paths.mount_point)
    assert verdict.state is MountState.NOT_GOCRYPTFS


@pytest.mark.parametrize("path_kind", ["missing", "directory"])
def test_unreadable_mountinfo_is_never_mounted(tmp_path: Path, path_kind: str) -> None:
    paths = make_volume(tmp_path)
    target = tmp_path / "absent" if path_kind == "missing" else tmp_path
    checker = MountChecker(mountinfo_path=os.fspath(target), euid=FAKE_EUID)
    verdict = checker.check(paths.mount_point, tmp_path)
    assert verdict.state is MountState.UNREADABLE
    assert verdict.error_code == "mount_unreadable"


def test_malformed_line_makes_the_table_unreadable(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point, lines=[ROOT_LINE, mountinfo_line(paths.mount_point), "garbage line"])
    assert fake.checker.check(paths.mount_point).state is MountState.UNREADABLE


def test_empty_table_is_unreadable(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point)
    fake.rewrite("")
    assert fake.checker.check(paths.mount_point).state is MountState.UNREADABLE


def test_project_root_on_another_device_is_a_device_mismatch(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point)
    fake.stat_fn.overrides[os.path.realpath(paths.project)] = OTHER_DEV
    verdict = fake.checker.check(paths.mount_point)
    assert verdict.state is MountState.NOT_GOCRYPTFS
    assert verdict.reason == "device_mismatch"


def test_mount_point_on_the_parent_device_is_a_device_mismatch(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point)
    fake.stat_fn.overrides[os.path.realpath(tmp_path)] = MOUNT_DEV
    verdict = fake.checker.check(paths.mount_point)
    assert verdict.reason == "device_mismatch"


def test_missing_project_root_is_mounted_but_refused(tmp_path: Path) -> None:
    mount_point = tmp_path / "vol"
    mount_point.mkdir(mode=0o700)
    fake = fake_mountinfo(mount_point)
    verdict = fake.checker.check(mount_point)
    assert verdict.state is MountState.MOUNTED
    assert verdict.error_code == "project_root_missing"
    with pytest.raises(ToolError) as caught:
        Volume(fake.checker, mount_point).require_mounted()
    assert caught.value.code == "project_root_missing"
    assert not (mount_point / "mcp-server-diwall-browser").exists()


def test_closed_volume_states(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point, lines=[ROOT_LINE])
    crypt = tmp_path / "Main.enc"
    crypt.mkdir()

    never = fake.checker.check(paths.mount_point, crypt)
    assert (never.state, never.error_code) == (MountState.NEVER_INITIALIZED, "encrypted_dir_undetermined")

    (crypt / "gocryptfs.conf").write_text("{}")
    closed = fake.checker.check(paths.mount_point, crypt)
    assert (closed.state, closed.error_code) == (MountState.CLOSED, "encrypted_dir_closed")

    absent = fake.checker.check(paths.mount_point)
    assert (absent.state, absent.error_code) == (MountState.CLOSED_OR_ABSENT, "encrypted_dir_undetermined")


@pytest.mark.parametrize("with_crypt_dir", [True, False])
def test_closed_volume_is_a_waiting_state_that_names_no_file(tmp_path: Path, with_crypt_dir: bool) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point, lines=[ROOT_LINE])
    crypt = tmp_path / "Main.enc"
    crypt.mkdir()
    (crypt / "gocryptfs.conf").write_text("{}")
    volume = Volume(fake.checker, paths.mount_point, crypt if with_crypt_dir else None)
    with pytest.raises(ToolError) as caught:
        volume.require_mounted()
    assert caught.value.action_expected == "operator_opens_directory"
    for forbidden in ("gocryptfs.conf", "credentials", "password", "username", ".json", str(tmp_path)):
        assert forbidden not in caught.value.message


def test_unmount_during_the_session_is_seen_on_the_next_access(tmp_path: Path) -> None:
    paths, fake, volume = mounted_volume(tmp_path)
    assert volume.require_mounted() == paths.project
    volume.ensure_dir("journal")
    fake.rewrite(ROOT_LINE + "\n")
    with pytest.raises(ToolError) as caught:
        volume.write_new("journal", "after.txt", data=b"x")
    assert caught.value.code == "encrypted_dir_undetermined"
    assert not (paths.project / "journal" / "after.txt").exists()
    fake.rewrite(ROOT_LINE + "\n" + mountinfo_line(paths.mount_point) + "\n")
    volume.write_new("journal", "after.txt", data=b"x")


# --- paths inside the volume --------------------------------------------------------


def test_symlink_inside_the_volume_to_a_directory_outside_is_refused(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (paths.project / "evidence").symlink_to(outside)
    with pytest.raises(ToolError) as caught:
        volume.resolve_inside("evidence", "capture.png")
    assert caught.value.code == "volume_path_refused"
    with pytest.raises(ToolError):
        volume.write_new("evidence", "capture.png", data=b"x")
    assert list(outside.iterdir()) == []


def test_path_inside_the_project_on_another_device_is_refused(tmp_path: Path) -> None:
    paths, fake, volume = mounted_volume(tmp_path)
    nested = paths.project / "downloads"
    nested.mkdir(mode=0o700)
    fake.stat_fn.overrides[os.path.realpath(nested)] = OTHER_DEV
    with pytest.raises(ToolError):
        volume.resolve_inside("downloads", "file.pdf")


@pytest.mark.parametrize("part", ["..", ".", "", "a/b", "x\x00"])
def test_path_parts_must_be_plain_names(tmp_path: Path, part: str) -> None:
    _paths, _fake, volume = mounted_volume(tmp_path)
    with pytest.raises(ValueError):
        volume.resolve_inside(part)


def test_ensure_dir_creates_private_levels_and_refuses_a_shared_one(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    created = volume.ensure_dir("evidence", "2000-01-01", "op")
    assert created == paths.project / "evidence" / "2000-01-01" / "op"
    for level in (paths.project / "evidence", created.parent, created):
        assert level.stat().st_mode & 0o777 == 0o700
    os.chmod(paths.project / "evidence", 0o755)
    with pytest.raises(ToolError):
        volume.ensure_dir("evidence", "2000-01-01")


def test_write_new_creates_a_private_file_and_never_overwrites(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    volume.ensure_dir("downloads")
    old_umask = os.umask(0)
    try:
        written = volume.write_new("downloads", "a.bin", data=b"abc")
    finally:
        os.umask(old_umask)
    assert written.read_bytes() == b"abc"
    assert written.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        volume.write_new("downloads", "a.bin", data=b"other")
    with pytest.raises(ValueError):
        volume.write_new("downloads", "b.bin", data=b"x", mode=0o644)


def test_open_append_refuses_a_symlink_in_place_of_the_file(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    volume.ensure_dir("journal")
    decoy = paths.project / "decoy.txt"
    decoy.write_text("")
    os.chmod(decoy, 0o600)
    (paths.project / "journal" / "operations.jsonl").symlink_to(decoy)
    with pytest.raises(ToolError) as caught:
        volume.open_append("journal", "operations.jsonl")
    assert caught.value.extra_server["mount_reason"] == "symlink"
    assert decoy.read_text() == ""


def test_write_new_refuses_a_symlinked_directory_inside_the_volume(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    real_dir = volume.ensure_dir("elsewhere")
    (paths.project / "evidence").symlink_to(real_dir)
    with pytest.raises(ToolError):
        volume.write_new("evidence", "capture.png", data=b"x")
    with pytest.raises(ToolError):
        volume.ensure_dir("evidence", "day")
    assert list(real_dir.iterdir()) == []


def test_read_file_checks_kind_owner_mode_and_size(tmp_path: Path) -> None:
    from mcp_server_diwall_browser.mount import VolumeFileError

    paths, _fake, volume = mounted_volume(tmp_path)
    target = paths.project / "data.txt"
    target.write_bytes(b"12345")
    os.chmod(target, 0o600)
    assert volume.read_file("data.txt", max_bytes=5).data == b"12345"
    with pytest.raises(VolumeFileError) as too_large:
        volume.read_file("data.txt", max_bytes=4)
    assert too_large.value.reason == "too_large"
    os.chmod(target, 0o640)
    with pytest.raises(VolumeFileError) as bad_mode:
        volume.read_file("data.txt", max_bytes=5)
    assert bad_mode.value.reason == "bad_mode"
    (paths.project / "fifo").unlink(missing_ok=True)
    os.mkfifo(paths.project / "fifo", 0o600)
    with pytest.raises(VolumeFileError) as fifo:
        volume.read_file("fifo", max_bytes=5)
    assert fifo.value.reason == "not_regular_file"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("../../etc/passwd", "_.._etc_passwd"),
        ("..", "unnamed"),
        ("", "unnamed"),
        ("facture été.pdf", "facture__t_.pdf"),
        (".hidden", "hidden"),
        ("a/b\\c\x00d", "a_b_c_d"),
        ("x" * 100, "x" * 64),
    ],
)
def test_safe_name(text: str, expected: str) -> None:
    assert safe_name(text) == expected


# --- a refused path has its own code -----------------------------------------------------------

PathRefusal = Callable[[Volume, VolumePaths], None]


def _link_outside(volume: Volume, paths: VolumePaths) -> None:
    outside = paths.tmp / "outside"
    outside.mkdir()
    (paths.project / "evidence").symlink_to(outside)
    volume.write_new("evidence", "capture.png", data=b"x")


def _link_inside(volume: Volume, paths: VolumePaths) -> None:
    (paths.project / "real").mkdir(mode=0o700)
    (paths.project / "evidence").symlink_to(paths.project / "real")
    volume.write_new("evidence", "capture.png", data=b"x")


def _shared_directory(volume: Volume, paths: VolumePaths) -> None:
    (paths.project / "evidence").mkdir(mode=0o700)
    os.chmod(paths.project / "evidence", 0o755)
    volume.ensure_dir("evidence")


def _shared_file(volume: Volume, paths: VolumePaths) -> None:
    (paths.project / "journal").mkdir(mode=0o700)
    target = paths.project / "journal" / "operations.jsonl"
    target.write_bytes(b"")
    os.chmod(target, 0o644)
    os.close(volume.open_append("journal", "operations.jsonl"))


PATH_REFUSALS: list[tuple[str, PathRefusal, str]] = [
    ("link to the outside", _link_outside, "path_outside_volume"),
    ("link inside the volume", _link_inside, "symlink"),
    ("directory not private", _shared_directory, "dir_not_private"),
    ("file not private", _shared_file, "bad_mode"),
]


def path_refusals_have_their_own_code(make_volume: Callable[[Path], tuple[VolumePaths, Volume]], tmp_path: Path) -> bool:
    for number, (_case, refusal, reason) in enumerate(PATH_REFUSALS):
        base = tmp_path / str(number)
        base.mkdir()
        paths, volume = make_volume(base)
        try:
            refusal(volume, paths)
        except ToolError as exc:
            if exc.code != "volume_path_refused" or exc.extra_server.get("mount_reason") != reason:
                return False
        else:
            return False
    return True


def _real_volume(base: Path) -> tuple[VolumePaths, Volume]:
    paths, _fake, volume = mounted_volume(base)
    return paths, volume


class _OldCodeVolume(Volume):
    """Defective: reports every refused path as a volume of the wrong kind."""

    def _resolve(self, root: Path, parts: tuple[str, ...]) -> Path:
        try:
            return super()._resolve(root, parts)
        except ToolError as exc:
            raise ToolError("mount_not_gocryptfs", exc.message, extra_server=exc.extra_server) from None

    def ensure_dir(self, *parts: str) -> Path:
        try:
            return super().ensure_dir(*parts)
        except ToolError as exc:
            raise ToolError("mount_not_gocryptfs", exc.message, extra_server=exc.extra_server) from None

    def open_append(self, *parts: str) -> int:
        try:
            return super().open_append(*parts)
        except ToolError as exc:
            raise ToolError("mount_not_gocryptfs", exc.message, extra_server=exc.extra_server) from None


def _old_code_volume(base: Path) -> tuple[VolumePaths, Volume]:
    paths, fake, _volume = mounted_volume(base)
    return paths, _OldCodeVolume(fake.checker, paths.mount_point)


def test_refused_paths_inside_a_mounted_volume_have_their_own_code(tmp_path: Path) -> None:
    assert path_refusals_have_their_own_code(_real_volume, tmp_path)


def test_path_code_check_catches_refusals_reported_as_a_wrong_volume(tmp_path: Path) -> None:
    assert not path_refusals_have_their_own_code(_old_code_volume, tmp_path)


def test_a_volume_of_the_wrong_kind_keeps_its_own_code(tmp_path: Path) -> None:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point, fs_type="ext4")
    with pytest.raises(ToolError) as caught:
        Volume(fake.checker, paths.mount_point).require_mounted()
    assert caught.value.code == "mount_not_gocryptfs"


# --- whether a declared path lies in the mounted volume ----------------------------------------

Holds = Callable[[Volume, str], bool]


def holds_checks(holds: Holds, tmp_path: Path) -> list[str]:
    """The cases a membership answer must get right; empty when all are right."""
    paths, fake, volume = mounted_volume(tmp_path)
    wrong = []
    inside = paths.project / "browser-profile" / "chromium"
    if not holds(volume, str(inside)):
        wrong.append("inside, not yet created")
    sibling = tmp_path / (paths.mount_point.name + "-copy") / "browser-profile"
    sibling.mkdir(parents=True)
    if holds(volume, str(sibling)):
        wrong.append("sibling directory whose name starts like the mount point")
    link = paths.project / "link-out"
    link.symlink_to(sibling)
    if holds(volume, str(link)):
        wrong.append("link inside the volume that leads out of it")
    other_device = paths.project / "other-device"
    other_device.mkdir(mode=0o700)
    fake.stat_fn.overrides[os.path.realpath(other_device)] = OTHER_DEV
    if holds(volume, str(other_device)):
        wrong.append("directory of the volume on another device")
    fake.rewrite(ROOT_LINE + "\n")
    if holds(volume, str(inside)):
        wrong.append("volume closed")
    return wrong


def prefix_holds(volume: Volume, path: str) -> bool:
    # Defective: compares the texts of the paths, with neither links nor separators considered.
    return path.startswith(str(volume._secrets_dir))


def test_a_path_lies_in_the_volume_only_when_it_really_does(tmp_path: Path) -> None:
    assert holds_checks(lambda volume, path: volume.holds(path), tmp_path) == []


def test_membership_check_catches_a_comparison_of_path_texts(tmp_path: Path) -> None:
    assert set(holds_checks(prefix_holds, tmp_path)) >= {
        "sibling directory whose name starts like the mount point",
        "link inside the volume that leads out of it",
        "volume closed",
    }
