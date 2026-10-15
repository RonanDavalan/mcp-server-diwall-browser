"""The command that carries an old credential file over to the format the server reads, without showing a value.

The values here are plainly false and live in a temporary tree with a simulated mount table. None of
them is ever put in an assertion that could print it: the checks answer yes or no.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import OTHER_DEV, FakeMount, FakeStat, VolumePaths, fake_mountinfo, make_volume, write_profile

from mcp_server_diwall_browser import credential_import, credentials, mount, operator_profile
from mcp_server_diwall_browser.credential_import import Dependencies, run
from mcp_server_diwall_browser.mount import MountChecker, Volume
from mcp_server_diwall_browser.origin import parse_origin
from mcp_server_diwall_browser.output import ToolError

OLD_USER = "test-old-user-1"
OLD_PASSWORD = "test-old-password-1"
OLD_TOTP = "TESTTOTPSEED234567"
OLD_TOPIC = "test-topic-notify-1"
VALUES = (OLD_USER, OLD_PASSWORD, OLD_TOTP, OLD_TOPIC)
HOST = "shop.example.invalid"
OLD_NAME = f"{HOST}.json"
KEYS_OF_THE_LINE = ["written", "origins", "carried", "not_carried", "origins_differ_from_source", "origins_assumed_https"]


def leaks(text: str) -> bool:
    """Whether any value of the old file is in ``text``; answers yes or no, never the value."""
    return any(value in text for value in VALUES)


@dataclass
class Outcome:
    code: int
    out: str
    err: str


@dataclass
class Rig:
    tmp: Path
    paths: VolumePaths
    fake: FakeMount
    environ: dict[str, str]
    old: Path

    def deps(self, checker: MountChecker | None = None) -> Dependencies:
        return Dependencies(load_profile=lambda: operator_profile.load_profile(self.environ), mount_checker=checker or self.fake.checker)

    def run(self, *argv: str, deps: Dependencies | None = None) -> Outcome:
        out, err = io.StringIO(), io.StringIO()
        code = run(list(argv), deps or self.deps(), out, err)
        return Outcome(code, out.getvalue(), err.getvalue())

    def convert(self, *extra: str, name: str = "shop-admin", source: Path | None = None, deps: Dependencies | None = None) -> Outcome:
        return self.run("--source", str(source or self.old / OLD_NAME), "--name", name, *extra, deps=deps)

    def old_file(self, name: str = OLD_NAME, *, mode: int = 0o600, checksum: bool | str = False, directory: Path | None = None, **fields: Any) -> Path:
        content: dict[str, Any] = {
            "origines_autorisees": [HOST],
            "username": OLD_USER,
            "password": OLD_PASSWORD,
            "totp_cle": OLD_TOTP,
            "ntfy_topic": OLD_TOPIC,
        }
        for key, value in fields.items():
            if value is None:
                content.pop(key, None)
            else:
                content[key] = value
        if checksum is True:
            subset = {k: content[k] for k in ("username", "password", "totp_cle", "origines_autorisees") if k in content}
            content["checksum"] = "sha256:" + hashlib.sha256(json.dumps(subset, sort_keys=True).encode("utf-8")).hexdigest()
        elif checksum:
            content["checksum"] = checksum
        path = (directory or self.old) / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(content, handle)
        os.chmod(path, mode)
        return path

    def target(self, name: str = "shop-admin") -> Path:
        return self.paths.credentials / f"{name}.json"

    def snapshot(self) -> dict[str, bytes]:
        """Every entry of the credentials directory, hidden ones too, with what it holds."""
        return {entry.name: entry.read_bytes() if entry.is_file() and not entry.is_symlink() else b"" for entry in self.paths.credentials.iterdir()}

    def produced(self, name: str = "shop-admin") -> dict[str, Any]:
        return json.loads(self.target(name).read_text())


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    paths = make_volume(tmp_path)
    fake = fake_mountinfo(paths.mount_point)
    write_profile(tmp_path, secrets_dir=str(paths.mount_point))
    environ = {"XDG_CONFIG_HOME": str(tmp_path / "config"), "HOME": str(tmp_path / "home")}
    old = paths.mount_point / "old-credentials"
    old.mkdir(mode=0o700)
    built = Rig(tmp_path, paths, fake, environ, old)
    built.old_file()
    return built


# --- the file produced ----------------------------------------------------------------------------


def test_the_file_produced_holds_exactly_the_origin_the_user_name_and_the_password_of_the_source(rig: Rig) -> None:
    outcome = rig.convert()
    assert outcome.code == 0 and outcome.err == ""
    identical = rig.produced() == {"allowed_origins": [f"https://{HOST}"], "username": OLD_USER, "password": OLD_PASSWORD}
    assert identical  # a yes or a no: a difference would print the values


def test_the_file_is_in_mode_600_in_a_directory_in_mode_700_and_nothing_else_is_left(rig: Rig) -> None:
    rig.convert()
    assert stat.S_IMODE(rig.target().stat().st_mode) == 0o600
    assert stat.S_IMODE(rig.paths.credentials.stat().st_mode) == 0o700
    assert sorted(rig.snapshot()) == ["shop-admin.json"]


def test_the_line_printed_has_exactly_the_six_keys_and_nothing_else_is_printed(rig: Rig) -> None:
    outcome = rig.convert()
    assert outcome.out.count("\n") == 1 and outcome.out.endswith("\n")
    line = json.loads(outcome.out)
    assert list(line) == KEYS_OF_THE_LINE
    assert line["written"] == "shop-admin.json" and line["origins"] == [f"https://{HOST}"]
    assert line["carried"] == ["username", "password"] and line["not_carried"] == 2
    assert line["origins_differ_from_source"] is False and line["origins_assumed_https"] is True


def test_the_second_factor_the_topic_and_the_checksum_are_not_in_the_file_and_are_counted(rig: Rig) -> None:
    rig.old_file(checksum=True)
    outcome = rig.convert()
    produced = rig.produced()
    assert set(produced) == {"allowed_origins", "username", "password"}
    assert not leaks(rig.target().read_text().replace(OLD_USER, "").replace(OLD_PASSWORD, ""))
    assert json.loads(outcome.out)["not_carried"] == 3


def test_a_source_with_only_a_password_carries_only_it(rig: Rig) -> None:
    rig.old_file(username=None)
    line = json.loads(rig.convert().out)
    assert line["carried"] == ["password"] and "username" not in rig.produced()


def test_the_origins_given_replace_those_of_the_source_and_the_line_says_they_differ(rig: Rig) -> None:
    outcome = rig.convert("--origin", "https://admin.example.invalid", "--origin", "http://admin.example.invalid:8080")
    line = json.loads(outcome.out)
    assert rig.produced()["allowed_origins"] == ["https://admin.example.invalid", "http://admin.example.invalid:8080"]
    assert line["origins_differ_from_source"] is True and line["origins_assumed_https"] is False


def test_origins_given_that_are_those_of_the_source_do_not_differ(rig: Rig) -> None:
    line = json.loads(rig.convert("--origin", f"https://{HOST}").out)
    assert line["origins_differ_from_source"] is False and line["origins_assumed_https"] is False


def test_the_origins_go_through_the_normalisation_of_the_origin_module(rig: Rig) -> None:
    rig.convert("--origin", "HTTPS://Admin.Example.Invalid:443")
    assert rig.produced()["allowed_origins"] == ["https://admin.example.invalid"]


# --- what is printed ----------------------------------------------------------------------------------


def everything_printed(rig: Rig) -> list[Outcome]:
    """One success and every refusal this command knows, each run on a fresh arrangement."""
    results = [rig.convert(name="shop-a")]
    results.append(rig.convert(name="shop-a"))  # the name is taken
    results.append(rig.convert(name="Bad Name"))
    results.append(rig.convert("--origin", "shop.example.invalid"))
    results.append(rig.run("--source", "relative.json", "--name", "x"))
    results.append(rig.run("--source", str(rig.old / OLD_NAME)))
    results.append(rig.run("--nothing", "x"))
    rig.old_file("wrong.json", checksum="sha256:" + "0" * 64)
    results.append(rig.convert(name="shop-b", source=rig.old / "wrong.json"))
    rig.old_file("empty.json", username=None, password=None)
    results.append(rig.convert(name="shop-c", source=rig.old / "empty.json"))
    rig.old_file("open.json", mode=0o644)
    results.append(rig.convert(name="shop-d", source=rig.old / "open.json"))
    (rig.old / "garbage.json").write_text("{" + OLD_PASSWORD)
    os.chmod(rig.old / "garbage.json", 0o600)
    results.append(rig.convert(name="shop-e", source=rig.old / "garbage.json"))
    results.append(rig.convert(name="shop-f", source=rig.tmp / "nowhere.json"))
    return results


def test_no_value_of_the_source_is_in_what_is_printed_for_a_success_and_for_every_refusal(rig: Rig) -> None:
    printed = everything_printed(rig)
    assert [r.code for r in printed][:2] == [0, 1]
    assert not any(leaks(r.out + r.err) for r in printed)


def test_no_value_is_in_the_message_of_an_exception_either(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    original = credential_import._convert

    def watching(argv: Any, dependencies: Any) -> Any:
        try:
            return original(argv, dependencies)
        except Exception as exc:  # noqa: BLE001 - the test reads what would have been shown
            seen.append(f"{type(exc).__name__} {exc} {getattr(exc, 'message', '')} {getattr(exc, 'extra_server', '')}")
            raise

    monkeypatch.setattr(credential_import, "_convert", watching)
    everything_printed(rig)
    assert seen and not any(leaks(text) for text in seen)


def test_a_value_that_names_the_file_is_taken_out_of_the_line_printed(rig: Rig) -> None:
    # The user name is also the name given to the new file: the value must not come back through the name.
    outcome = rig.convert(name=OLD_USER)
    assert outcome.code == 0 and not leaks(outcome.out)


def test_printing_check_catches_an_output_that_does_not_go_through_the_redaction(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def plain(stream: Any, text: str) -> None:
        stream.write(text + "\n")

    monkeypatch.setattr(credential_import, "_emit", plain)
    assert leaks(rig.convert(name=OLD_USER).out)


def test_printing_check_catches_a_refusal_that_repeats_a_value(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def repeating(argv: Any, dependencies: Any) -> Any:
        raise credential_import.ImportRefused("source_invalid", OLD_PASSWORD)

    monkeypatch.setattr(credential_import, "_convert", repeating)
    assert leaks(rig.convert().err)


# --- the schema of the server did not move ---------------------------------------------------------------


def resolved_for_old_file(rig: Rig, name: str) -> str:
    """What happens when the old file is put straight in the directory of credential files."""
    (rig.paths.credentials / name).write_bytes((rig.old / OLD_NAME).read_bytes())
    os.chmod(rig.paths.credentials / name, 0o600)
    volume = Volume(rig.fake.checker, rig.paths.mount_point)
    try:
        credentials.resolve(volume, parse_origin(f"https://{HOST}"), "password")
    except ToolError as refused:
        return refused.code
    return "accepted"


def test_the_old_format_put_straight_in_the_credentials_directory_is_still_refused(rig: Rig) -> None:
    assert resolved_for_old_file(rig, "old.json") == "credential_invalid"


def test_schema_check_catches_a_schema_that_would_accept_the_old_format(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def lax(basename: str, raw: bytes) -> Any:
        return credentials._CredentialFile(basename, (parse_origin(f"https://{HOST}"),), {"password": "any"})

    monkeypatch.setattr(credentials, "_parse", lax)
    assert resolved_for_old_file(rig, "old.json") == "accepted"


def test_the_file_produced_is_accepted_for_its_origin_and_refused_for_the_neighbours(rig: Rig) -> None:
    rig.convert()
    volume = Volume(rig.fake.checker, rig.paths.mount_point)
    assert credentials.resolve(volume, parse_origin(f"https://{HOST}"), "password").reveal() == OLD_PASSWORD
    assert credentials.resolve(volume, parse_origin(f"https://{HOST}"), "username", "shop-admin").reveal() == OLD_USER
    for neighbour in (f"https://{HOST}:8443", f"http://{HOST}", f"https://www.{HOST}", "https://example.invalid"):
        with pytest.raises(ToolError) as refused:
            credentials.resolve(volume, parse_origin(neighbour), "password")
        assert refused.value.code == "credential_not_bound"


# --- refusals, and nothing created --------------------------------------------------------------------


def refused(rig: Rig, code: str, outcome: Outcome, before: dict[str, bytes]) -> bool:
    return outcome.code == 1 and outcome.out == "" and outcome.err.split()[0:2] == ["error:", code] and rig.snapshot() == before


def test_a_name_that_is_taken_is_refused_by_its_base_name_and_the_file_is_untouched(rig: Rig) -> None:
    assert rig.convert().code == 0
    before = rig.snapshot()
    rig.old_file(password="test-other-password-2")
    outcome = rig.convert()
    assert refused(rig, "target_exists", outcome, before)
    assert outcome.err.strip() == "error: target_exists shop-admin.json"
    assert not leaks(outcome.err)


def test_a_link_as_source_is_refused_and_nothing_is_created(rig: Rig) -> None:
    before = rig.snapshot()
    link = rig.old / "link.json"
    os.symlink(rig.old / OLD_NAME, link)
    assert refused(rig, "source_invalid", rig.convert(source=link), before)


def test_a_link_to_outside_the_volume_as_source_is_refused_and_nothing_is_created(rig: Rig) -> None:
    before = rig.snapshot()
    elsewhere = rig.tmp / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    outside = rig.old_file(OLD_NAME)
    moved = elsewhere / OLD_NAME
    moved.write_bytes(outside.read_bytes())
    os.chmod(moved, 0o600)
    link = rig.old / "out.json"
    os.symlink(moved, link)
    assert refused(rig, "source_not_in_volume", rig.convert(source=link), before)


def test_a_link_as_a_directory_of_the_source_path_is_refused_and_nothing_is_created(rig: Rig) -> None:
    before = rig.snapshot()
    os.symlink(rig.old, rig.paths.mount_point / "shortcut")
    assert refused(rig, "source_invalid", rig.convert(source=rig.paths.mount_point / "shortcut" / OLD_NAME), before)


def test_a_source_outside_the_volume_is_refused_and_nothing_is_created(rig: Rig) -> None:
    before = rig.snapshot()
    outside = rig.tmp / "outside.json"
    outside.write_bytes((rig.old / OLD_NAME).read_bytes())
    os.chmod(outside, 0o600)
    assert refused(rig, "source_not_in_volume", rig.convert(source=outside), before)


def test_a_source_on_another_device_than_the_volume_is_refused(rig: Rig) -> None:
    before = rig.snapshot()
    nested = rig.old / OLD_NAME
    rig.fake.checker.stat_fn = FakeStat(rig.paths.mount_point, {os.path.realpath(nested): OTHER_DEV})  # type: ignore[assignment]
    assert refused(rig, "source_not_in_volume", rig.convert(), before)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o604])
def test_a_source_with_rights_for_group_or_others_is_refused(rig: Rig, mode: int) -> None:
    before = rig.snapshot()
    rig.old_file(mode=mode)
    assert refused(rig, "source_mode", rig.convert(), before)


def test_a_source_that_is_not_the_users_is_refused(rig: Rig) -> None:
    before = rig.snapshot()
    other = fake_mountinfo(rig.paths.mount_point, user_id=os.geteuid() + 1, euid=os.geteuid() + 1)
    assert refused(rig, "source_invalid", rig.convert(deps=rig.deps(other.checker)), before)


def test_a_wrong_checksum_is_refused_and_a_right_one_or_none_is_accepted(rig: Rig) -> None:
    before = rig.snapshot()
    rig.old_file(checksum="sha256:" + "0" * 64)
    assert refused(rig, "checksum_mismatch", rig.convert(), before)
    rig.old_file(checksum="not a checksum")
    assert refused(rig, "checksum_mismatch", rig.convert(), before)
    rig.old_file(checksum=True, password="test-changed-after-the-sum-3")
    # The sum was made for other content: written again with the right sum it passes.
    content = json.loads((rig.old / OLD_NAME).read_text())
    content["password"] = OLD_PASSWORD
    (rig.old / OLD_NAME).write_text(json.dumps(content))
    assert refused(rig, "checksum_mismatch", rig.convert(), before)
    rig.old_file(checksum=True)
    assert rig.convert().code == 0
    rig.target().unlink()
    rig.old_file()
    assert rig.convert().code == 0


def test_checksum_check_catches_a_conversion_that_does_not_verify_it(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.old_file(checksum="sha256:" + "0" * 64)
    monkeypatch.setattr(credential_import, "_verify_checksum", lambda data: None)
    assert rig.convert().code == 0


def test_a_source_without_a_user_name_or_a_password_carries_nothing_and_is_refused(rig: Rig) -> None:
    before = rig.snapshot()
    rig.old_file(username=None, password=None)
    assert refused(rig, "nothing_to_carry", rig.convert(), before)
    rig.old_file(username="", password="")
    assert refused(rig, "nothing_to_carry", rig.convert(), before)


@pytest.mark.parametrize("name", ["Shop", "a/b", "", "x" * 65, ".hidden", "with space", "../up", "name.json."[:0] + "ünï"])
def test_a_name_the_server_would_not_read_is_refused(rig: Rig, name: str) -> None:
    before = rig.snapshot()
    assert refused(rig, "name_invalid", rig.convert(name=name), before)


def test_the_name_rule_is_the_one_of_the_credentials_module(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    before = rig.snapshot()
    monkeypatch.setattr(credentials, "valid_name", lambda name: False)
    assert refused(rig, "name_invalid", rig.convert(), before)


@pytest.mark.parametrize(
    "origin", ["shop.example.invalid", "https://shop.example.invalid/path", "ftp://shop.example.invalid", "https://user@shop.example.invalid", "https://*.example.invalid", ""]
)
def test_an_origin_that_is_not_an_origin_is_refused(rig: Rig, origin: str) -> None:
    before = rig.snapshot()
    assert refused(rig, "origin_invalid", rig.convert("--origin", origin), before)


@pytest.mark.parametrize("hosts", [None, [], "shop.example.invalid", [3], ["Shop.Example.invalid"], ["shop.example.invalid:8443"], ["https://shop.example.invalid"]])
def test_hosts_of_the_source_that_are_not_plain_host_names_are_refused_unless_origins_are_given(rig: Rig, hosts: Any) -> None:
    before = rig.snapshot()
    content = json.loads((rig.old / OLD_NAME).read_text())
    content.pop("origines_autorisees")
    if hosts is not None:
        content["origines_autorisees"] = hosts
    (rig.old / OLD_NAME).write_text(json.dumps(content))
    assert refused(rig, "origin_invalid", rig.convert(), before)
    outcome = rig.convert("--origin", "https://admin.example.invalid")
    assert outcome.code == 0 and json.loads(outcome.out)["origins_differ_from_source"] is True


def test_a_volume_that_is_closed_is_refused_with_the_usual_code_and_nothing_is_read(rig: Rig) -> None:
    before = rig.snapshot()
    closed = fake_mountinfo(rig.paths.mount_point, lines=["22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw"])
    assert refused(rig, "encrypted_dir_undetermined", rig.convert(deps=rig.deps(closed.checker)), before)


def test_a_volume_that_is_not_the_users_gocryptfs_is_refused(rig: Rig) -> None:
    before = rig.snapshot()
    foreign = fake_mountinfo(rig.paths.mount_point, fs_type="ext4")
    assert refused(rig, "mount_not_gocryptfs", rig.convert(deps=rig.deps(foreign.checker)), before)


@pytest.mark.parametrize(
    "content",
    ["{", "[]", '"text"', "", '{"username": "a", "username": "b"}', '{"username": 3, "origines_autorisees": ["x.example.invalid"]}', '{"username": ["a"], "origines_autorisees": ["x.example.invalid"]}'],
)
def test_a_source_that_is_not_an_object_or_has_a_wrong_type_is_refused(rig: Rig, content: str) -> None:
    before = rig.snapshot()
    (rig.old / "bad.json").write_text(content)
    os.chmod(rig.old / "bad.json", 0o600)
    assert refused(rig, "source_invalid", rig.convert(source=rig.old / "bad.json"), before)


def test_a_source_that_is_missing_relative_or_too_large_is_refused(rig: Rig) -> None:
    before = rig.snapshot()
    assert refused(rig, "source_invalid", rig.convert(source=rig.old / "absent.json"), before)
    assert refused(rig, "source_invalid", rig.run("--source", "old/shop.json", "--name", "x"), before)
    (rig.old / "big.json").write_text(json.dumps({"username": "a" * (credential_import.SOURCE_LIMIT + 10)}))
    os.chmod(rig.old / "big.json", 0o600)
    assert refused(rig, "source_invalid", rig.convert(source=rig.old / "big.json"), before)


def test_a_target_that_is_a_link_is_refused_and_leads_nowhere(rig: Rig) -> None:
    victim = rig.tmp / "victim"
    victim.write_text("victim")
    os.symlink(victim, rig.target())
    before = rig.snapshot()
    assert refused(rig, "volume_path_refused", rig.convert(), before)
    assert victim.read_text() == "victim"


def test_a_credentials_directory_that_is_a_link_is_refused_and_leads_nowhere(rig: Rig) -> None:
    elsewhere = rig.tmp / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    rig.paths.credentials.rmdir()
    os.symlink(elsewhere, rig.paths.credentials)
    outcome = rig.convert()
    assert outcome.code == 1 and outcome.err.split()[:2] == ["error:", "volume_path_refused"]
    assert list(elsewhere.iterdir()) == []


def test_a_credentials_directory_that_is_not_private_or_not_made_is_refused(rig: Rig) -> None:
    os.chmod(rig.paths.credentials, 0o750)
    outcome = rig.convert()
    assert outcome.err.split()[:2] == ["error:", "credential_invalid"] and sorted(rig.snapshot()) == []
    os.chmod(rig.paths.credentials, 0o700)
    rig.paths.credentials.rmdir()
    assert rig.convert().err.split()[:2] == ["error:", "credential_invalid"]
    assert not rig.paths.credentials.exists()


def test_a_write_that_fails_leaves_no_file_in_the_directory(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    before = rig.snapshot()

    def disk_full(fd: int) -> None:
        raise OSError(28, "no space")

    monkeypatch.setattr(mount.os, "fsync", disk_full)
    outcome = rig.convert()
    assert refused(rig, "internal_error", outcome, before)


def test_nothing_left_check_catches_a_publication_that_leaves_a_half_file(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    before = rig.snapshot()

    def half(self: Volume, *parts: str, data: bytes) -> Any:
        self.write_new(*parts, data=data[: len(data) // 2])
        raise OSError(28, "no space")

    monkeypatch.setattr(Volume, "publish_new", half)
    outcome = rig.convert()
    assert outcome.code == 1 and rig.snapshot() != before


def test_a_profile_that_is_missing_is_refused_as_usual(rig: Rig) -> None:
    os.unlink(rig.tmp / "config" / "mcp-server-diwall-browser" / "profile.json")
    outcome = rig.convert()
    assert outcome.code == 1 and outcome.err.split()[:2] == ["error:", "not_configured"] and outcome.out == ""


@pytest.mark.parametrize(
    "argv",
    [[], ["--source", "/x"], ["--name", "x"], ["--source", "/x", "--name", "x", "--other", "y"], ["--source", "/x", "--name", "x", "extra"], ["--sou", "/x", "--name", "x"], ["-h"]],
)
def test_a_command_line_that_is_not_the_three_options_is_refused_without_repeating_it(rig: Rig, argv: list[str]) -> None:
    before = rig.snapshot()
    outcome = rig.run(*argv)
    assert refused(rig, "invalid_arguments", outcome, before)
    assert "/x" not in outcome.err and "extra" not in outcome.err and "--other" not in outcome.err


# --- a directory of old files ---------------------------------------------------------------------------


@pytest.fixture
def batch(rig: Rig) -> Path:
    directory = rig.paths.mount_point / "batch"
    directory.mkdir(mode=0o700)
    return directory


def convert_dir(rig: Rig, directory: Path, *extra: str, deps: Dependencies | None = None) -> Outcome:
    return rig.run("--source-dir", str(directory), *extra, deps=deps)


def lines_of(outcome: Outcome) -> list[dict[str, Any]]:
    return [json.loads(line) for line in outcome.out.splitlines()]


def three_files(rig: Rig, batch: Path) -> None:
    rig.old_file("alpha.json", directory=batch)
    rig.old_file("bravo.json", directory=batch, mode=0o644)
    rig.old_file("charlie.json", directory=batch, username=None, password=None)


def test_a_directory_of_three_files_writes_one_refuses_two_prints_three_lines_and_exits_with_1(rig: Rig, batch: Path) -> None:
    three_files(rig, batch)
    outcome = convert_dir(rig, batch)
    assert outcome.code == 1 and outcome.err == ""
    lines = lines_of(outcome)
    assert len(lines) == 3 and outcome.out.count("\n") == 3
    assert list(lines[0]) == KEYS_OF_THE_LINE and lines[0]["written"] == "alpha.json"
    assert lines[0]["origins"] == [f"https://{HOST}"] and lines[0]["origins_assumed_https"] is True
    assert lines[1] == {"file": "bravo.json", "error": "source_mode"}
    assert lines[2] == {"file": "charlie.json", "error": "nothing_to_carry"}
    assert sorted(rig.snapshot()) == ["alpha.json"]
    assert rig.produced("alpha") == {"allowed_origins": [f"https://{HOST}"], "username": OLD_USER, "password": OLD_PASSWORD}
    assert stat.S_IMODE(rig.target("alpha").stat().st_mode) == 0o600


def test_a_directory_of_good_files_exits_with_0_and_lists_them_in_alphabetical_order(rig: Rig, batch: Path) -> None:
    for name in ("zulu.json", "alpha.json", "mike.json"):
        rig.old_file(name, directory=batch)
    outcome = convert_dir(rig, batch)
    assert outcome.code == 0 and outcome.err == ""
    assert [line["written"] for line in lines_of(outcome)] == ["alpha.json", "mike.json", "zulu.json"]
    assert sorted(rig.snapshot()) == ["alpha.json", "mike.json", "zulu.json"]


def test_a_refused_file_does_not_stop_the_files_after_it(rig: Rig, batch: Path) -> None:
    rig.old_file("a-open.json", directory=batch, mode=0o644)
    rig.old_file("b-good.json", directory=batch)
    outcome = convert_dir(rig, batch)
    assert outcome.code == 1
    assert [line.get("file") or line["written"] for line in lines_of(outcome)] == ["a-open.json", "b-good.json"]
    assert sorted(rig.snapshot()) == ["b-good.json"]


def test_a_sub_directory_is_ignored_and_so_is_what_is_not_a_json_file(rig: Rig, batch: Path) -> None:
    rig.old_file("alpha.json", directory=batch)
    (batch / "nested").mkdir(mode=0o700)
    rig.old_file("inner.json", directory=batch / "nested")
    (batch / "folder.json").mkdir(mode=0o700)
    rig.old_file("deep.json", directory=batch / "folder.json")
    rig.old_file("notes.txt", directory=batch)
    rig.old_file("alpha.json.bak", directory=batch)
    outcome = convert_dir(rig, batch)
    assert outcome.code == 0
    assert [line["written"] for line in lines_of(outcome)] == ["alpha.json"]
    assert sorted(rig.snapshot()) == ["alpha.json"]


def test_the_name_of_the_new_file_is_the_base_name_of_the_old_one_and_must_be_a_name_the_server_reads(rig: Rig, batch: Path) -> None:
    rig.old_file(OLD_NAME, directory=batch)
    rig.old_file("Bad Name.json", directory=batch)
    rig.old_file(".hidden.json", directory=batch)
    outcome = convert_dir(rig, batch)
    lines = {line.get("file") or line["written"]: line for line in lines_of(outcome)}
    assert lines[OLD_NAME]["written"] == OLD_NAME
    assert lines["Bad Name.json"]["error"] == "name_invalid" and lines[".hidden.json"]["error"] == "name_invalid"
    assert sorted(rig.snapshot()) == [OLD_NAME]


def test_a_name_that_is_taken_is_refused_for_that_file_only_and_the_existing_file_is_untouched(rig: Rig, batch: Path) -> None:
    rig.old_file("alpha.json", directory=batch, password="test-other-password-2")
    rig.old_file("bravo.json", directory=batch)
    rig.old_file("alpha.json")
    assert rig.convert(name="alpha", source=rig.old / "alpha.json").code == 0
    outcome = convert_dir(rig, batch)
    lines = lines_of(outcome)
    assert outcome.code == 1 and lines[0] == {"file": "alpha.json", "error": "target_exists"} and lines[1]["written"] == "bravo.json"
    assert rig.produced("alpha")["password"] == OLD_PASSWORD


def test_each_file_goes_through_the_checks_of_the_single_file_command(rig: Rig, batch: Path) -> None:
    rig.old_file("a-sum.json", directory=batch, checksum="sha256:" + "0" * 64)
    rig.old_file("b-bad.json", directory=batch)
    (batch / "b-bad.json").write_text("{")
    os.chmod(batch / "b-bad.json", 0o600)
    os.symlink(batch / "a-sum.json", batch / "c-link.json")
    outcome = convert_dir(rig, batch)
    assert [line["error"] for line in lines_of(outcome)] == ["checksum_mismatch", "source_invalid", "source_invalid"]
    assert outcome.code == 1 and rig.snapshot() == {}


@pytest.mark.parametrize("extra", [["--source", "/x"], ["--name", "x"], ["--origin", "https://admin.example.invalid"], ["--source", "/x", "--name", "x"]])
def test_the_directory_option_is_exclusive_of_the_three_others(rig: Rig, batch: Path, extra: list[str]) -> None:
    rig.old_file("alpha.json", directory=batch)
    before = rig.snapshot()
    assert refused(rig, "invalid_arguments", convert_dir(rig, batch, *extra), before)


def test_a_directory_that_is_not_one_the_command_may_read_is_refused_with_one_line(rig: Rig, batch: Path) -> None:
    before = rig.snapshot()
    rig.old_file("alpha.json", directory=batch)
    elsewhere = rig.tmp / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    os.symlink(batch, rig.paths.mount_point / "shortcut")
    os.symlink(elsewhere, rig.paths.mount_point / "out")
    cases = [
        (rig.run("--source-dir", "batch"), "source_invalid"),
        (convert_dir(rig, rig.paths.mount_point / "absent"), "source_invalid"),
        (convert_dir(rig, batch / "alpha.json"), "source_invalid"),
        (convert_dir(rig, rig.paths.mount_point / "shortcut"), "source_invalid"),
        (convert_dir(rig, elsewhere), "source_not_in_volume"),
        (convert_dir(rig, rig.paths.mount_point / "out"), "source_not_in_volume"),
    ]
    for outcome, code in cases:
        assert refused(rig, code, outcome, before)
        assert str(rig.tmp) not in outcome.err


def test_a_directory_without_a_json_file_is_refused_and_not_taken_for_a_success(rig: Rig, batch: Path) -> None:
    before = rig.snapshot()
    rig.old_file("notes.txt", directory=batch)
    assert refused(rig, "source_dir_empty", convert_dir(rig, batch), before)


def test_a_volume_that_is_closed_is_refused_for_a_directory_too(rig: Rig, batch: Path) -> None:
    before = rig.snapshot()
    rig.old_file("alpha.json", directory=batch)
    closed = fake_mountinfo(rig.paths.mount_point, lines=["22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw"])
    assert refused(rig, "encrypted_dir_undetermined", convert_dir(rig, batch, deps=rig.deps(closed.checker)), before)


def directory_run_with_everything(rig: Rig, batch: Path) -> Outcome:
    """A directory with a success, a file named after a value, and a refusal of each kind that carries a value."""
    rig.old_file("alpha.json", directory=batch, zz_unknown_key="test-unknown-key-value-4")
    rig.old_file(f"{OLD_USER}.json", directory=batch)
    rig.old_file("wrong.json", directory=batch, checksum="sha256:" + "0" * 64)
    rig.old_file("open.json", directory=batch, mode=0o644)
    (batch / "garbage.json").write_text("{" + OLD_PASSWORD)
    os.chmod(batch / "garbage.json", 0o600)
    return convert_dir(rig, batch)


def test_no_value_no_unknown_key_name_and_no_full_path_is_in_what_a_directory_run_prints(rig: Rig, batch: Path) -> None:
    outcome = directory_run_with_everything(rig, batch)
    assert outcome.code == 1 and len(lines_of(outcome)) == 5
    text = outcome.out + outcome.err
    assert not leaks(text) and "zz_unknown_key" not in text and "test-unknown-key-value-4" not in text
    assert str(rig.tmp) not in text and "/batch" not in text


def test_directory_printing_check_catches_an_output_that_does_not_go_through_the_redaction(rig: Rig, batch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def plain(stream: Any, text: str) -> None:
        stream.write(text + "\n")

    monkeypatch.setattr(credential_import, "_emit", plain)
    assert leaks(directory_run_with_everything(rig, batch).out)


# --- where the command may and may not reach --------------------------------------------------------------


SOURCE = Path(credential_import.__file__)


def imported_modules(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


FORBIDDEN_IMPORTS = {"socket", "subprocess", "http", "urllib", "ssl", "ftplib", "smtplib", "requests", "asyncio", "websockets", "ctypes", "pty"}


def reaches_out(source: str) -> bool:
    """Whether the source could open a connection or start a program."""
    forbidden_calls = {"system", "popen", "spawnv", "execv", "execvp"}
    calls = {node.func.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    return bool(imported_modules(source) & FORBIDDEN_IMPORTS) or bool(calls & forbidden_calls) or "gocryptfs" in source or "fusermount" in source


def test_the_command_opens_no_connection_and_starts_no_program_and_names_no_mount_tool() -> None:
    assert not reaches_out(SOURCE.read_text())


@pytest.mark.parametrize(
    "source",
    [
        "import socket\n",
        "import subprocess\nsubprocess.run(['x'])\n",
        "from urllib import request\n",
        "import os\nos.system('x')\n",
        "x = 'gocryptfs'\n",
        "x = 'fusermount3 -u'\n",
    ],
)
def test_reach_check_catches_each_way_out(source: str) -> None:
    assert reaches_out(source)


def written_calls(source: str) -> list[int]:
    """Lines where the module writes on a stream with ``.write(``."""
    return [node.lineno for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write"]


def test_everything_the_command_writes_leaves_through_its_one_function() -> None:
    source = SOURCE.read_text()
    tree = ast.parse(source)
    emit = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_emit")
    inside = set(range(emit.lineno, emit.end_lineno + 1))  # type: ignore[operator]
    assert written_calls(source) and set(written_calls(source)) <= inside


def test_write_check_catches_a_second_way_to_write() -> None:
    planted = "def _emit(s, t):\n    s.write(t)\n\n\ndef run(out):\n    out.write('x')\n"
    tree = ast.parse(planted)
    emit = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_emit")
    inside = set(range(emit.lineno, emit.end_lineno + 1))  # type: ignore[operator]
    assert not set(written_calls(planted)) <= inside


def order_of_calls(source: str, function: str, first: str, then: str) -> bool:
    """Whether, in ``function``, the call to ``first`` comes before the call to ``then``."""
    tree = ast.parse(source)
    body = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function)
    lines: dict[str, int] = {}
    for node in ast.walk(body):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            lines.setdefault(name, node.lineno)
    return first in lines and then in lines and lines[first] < lines[then]


def test_core_dumps_are_disabled_before_the_first_value_is_read() -> None:
    assert order_of_calls(SOURCE.read_text(), "run", "disable_core_dumps", "_convert")


def test_core_dump_ordering_check_catches_a_command_that_reads_first() -> None:
    planted = "def run(a):\n    _convert(a)\n    hardening.disable_core_dumps()\n"
    assert not order_of_calls(planted, "run", "disable_core_dumps", "_convert")


def test_the_umask_is_restricted_before_the_command_runs() -> None:
    assert order_of_calls(SOURCE.read_text(), "main", "restrict_umask", "run")


def test_no_other_module_of_the_package_imports_the_command() -> None:
    package = SOURCE.parent
    importers = [path.name for path in package.rglob("*.py") if path != SOURCE and "credential_import" in path.read_text()]
    assert importers == []


# --- the command as a process --------------------------------------------------------------------------------


def run_process(*argv: str, environ: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_server_diwall_browser.credential_import", *argv],
        capture_output=True,
        text=True,
        env={**environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
        check=False,
    )


def test_the_process_refuses_a_volume_that_is_not_mounted_with_one_line_and_exit_code_1(rig: Rig) -> None:
    env = {**rig.environ, "MCP_DIWALL_BROWSER_PROFILE": str(rig.tmp / "config" / "mcp-server-diwall-browser" / "profile.json")}
    done = run_process("--source", str(rig.old / OLD_NAME), "--name", "shop-admin", environ=env)
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr.split() == ["error:", "encrypted_dir_undetermined"]
    assert not leaks(done.stdout + done.stderr) and str(rig.tmp) not in done.stderr


def test_the_process_without_a_profile_says_so_in_one_line(tmp_path: Path) -> None:
    (tmp_path / "home").mkdir(mode=0o700)
    done = run_process("--source", "/x", "--name", "x", environ={"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "none")})
    assert done.returncode == 1 and done.stdout == "" and done.stderr.split() == ["error:", "not_configured"]


def test_the_process_with_a_wrong_command_line_says_so_in_one_line(tmp_path: Path) -> None:
    done = run_process("--source", "~/secret-path.json", "--unknown", environ={"HOME": str(tmp_path)})
    assert done.returncode == 1 and done.stdout == "" and done.stderr.split() == ["error:", "invalid_arguments"]
    assert "secret-path" not in done.stderr


def test_a_run_ends_with_a_core_file_limit_of_zero(tmp_path: Path) -> None:
    # The limit is raised first when the surroundings allow it, so that the command is seen to lower it.
    script = (
        "import resource, sys\n"
        "try:\n"
        "    resource.setrlimit(resource.RLIMIT_CORE, (0, resource.RLIM_INFINITY))\n"
        "    raised = True\n"
        "except ValueError:\n"
        "    raised = False\n"
        "from mcp_server_diwall_browser import credential_import\n"
        "code = credential_import.run(['--source', '/x', '--name', 'x'], credential_import.production_dependencies(), sys.stderr, sys.stderr)\n"
        "print(resource.getrlimit(resource.RLIMIT_CORE), code, raised)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env={"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "none")}, timeout=60, check=False
    )
    assert "(0, 0) 1" in done.stdout, done.stderr[-300:]
