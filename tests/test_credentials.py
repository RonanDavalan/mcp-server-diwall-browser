from __future__ import annotations

import copy
import json
import os
import pickle
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import (
    DELETE,
    FAKE_PASSWORD,
    FAKE_USERNAME,
    ROOT_LINE,
    VolumePaths,
    mounted_volume,
    write_credential,
)

from mcp_server_diwall_browser import credentials, output
from mcp_server_diwall_browser.credentials import Secret, resolve
from mcp_server_diwall_browser.mount import Volume
from mcp_server_diwall_browser.origin import Origin, parse_origin
from mcp_server_diwall_browser.output import ToolError, ToolOutput, tool

SITE = parse_origin("https://example.invalid")


def resolve_error(volume: Volume, origin: Origin = SITE, key: str = "password", name: str | None = None) -> ToolError:
    with pytest.raises(ToolError) as caught:
        resolve(volume, origin, key, name)
    return caught.value


def test_credential_bound_to_the_origin_is_resolved(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    assert resolve(volume, SITE, "password").reveal() == FAKE_PASSWORD
    assert resolve(volume, SITE, "username", "service").reveal() == FAKE_USERNAME


def test_origin_with_explicit_default_port_matches(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json", allowed_origins=["https://example.invalid:443"])
    assert resolve(volume, SITE, "password").reveal() == FAKE_PASSWORD


async def test_resolved_value_never_appears_in_any_finalized_output(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")

    @tool("fill")
    async def leaky_fill() -> ToolOutput:
        secret = resolve(volume, SITE, "password")
        user = resolve(volume, SITE, "username")
        return ToolOutput(
            server={"typed": secret.reveal(), "note": f"{user.reveal()} / {secret}", "url": f"https://x.test/?p={secret.reveal()}"},
            page_content={"value": [secret.reveal()], "echo": json.dumps(secret.reveal())},
        )

    @tool("fill")
    async def failing_fill() -> ToolOutput:
        secret = resolve(volume, SITE, "password")
        raise ToolError("credential_invalid", f"valeur {secret.reveal()} refusée")

    @tool("fill")
    async def crashing_fill() -> ToolOutput:
        secret = resolve(volume, SITE, "password")
        raise RuntimeError(secret.reveal())

    for fn in (leaky_fill, failing_fill, crashing_fill):
        result, _image = await fn()
        text = json.dumps(result)
        assert FAKE_PASSWORD not in text
        assert FAKE_USERNAME not in text


def test_old_format_file_is_refused_loudly(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    path = paths.credentials / "legacy.json"
    path.write_text(json.dumps({"origines_autorisees": ["example.invalid"], "identifiant": "a", "mot_de_passe": "b"}))
    os.chmod(path, 0o600)
    error = resolve_error(volume)
    assert error.code == "credential_invalid"
    assert error.extra_server["reason"] == "missing_allowed_origins"
    assert "legacy.json" in error.message


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"allowed_origins": []}, "missing_allowed_origins"),
        ({"allowed_origins": "https://example.invalid"}, "bad_origin"),
        ({"allowed_origins": ["example.invalid"]}, "bad_origin"),
        ({"allowed_origins": ["https://example.invalid/login"]}, "bad_origin"),
        ({"allowed_origins": ["https://*.example.invalid"]}, "bad_origin"),
        ({"allowed_origins": [42]}, "bad_origin"),
        ({"username": DELETE, "password": DELETE}, "no_username_or_password"),
        ({"password": 1234}, "no_username_or_password"),
        ({"password": ""}, "no_username_or_password"),
    ],
)
def test_schema_violations_are_refused(tmp_path: Path, fields: dict[str, object], reason: str) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json", **fields)
    error = resolve_error(volume)
    assert (error.code, error.extra_server["reason"]) == ("credential_invalid", reason)


@pytest.mark.parametrize("content", [b"not json", b"[1, 2]", b"\xff\xfe", b'{"password": "a", "password": "b"}'])
def test_unparsable_file_is_refused(tmp_path: Path, content: bytes) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    path = paths.credentials / "service.json"
    path.write_bytes(content)
    os.chmod(path, 0o600)
    error = resolve_error(volume)
    assert error.extra_server["reason"] == "not_json"


def test_unknown_key_is_refused_without_naming_it(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json", zz_hidden_field="v-zz-value")
    error = resolve_error(volume)
    assert error.extra_server["reason"] == "unknown_keys"
    rendered = json.dumps({"m": error.message, "e": error.extra_server})
    for forbidden in ("zz_hidden_field", "v-zz-value", "allowed_origins", "username", FAKE_PASSWORD):
        assert forbidden not in rendered


def test_file_readable_by_others_is_refused_with_the_fix(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    path = write_credential(paths, "service.json")
    os.chmod(path, 0o644)
    error = resolve_error(volume)
    assert error.extra_server["reason"] == "bad_mode"
    assert "chmod 600 service.json" in error.message


def test_credentials_directory_open_to_others_is_refused_with_the_fix(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    os.chmod(paths.credentials, 0o755)
    error = resolve_error(volume)
    assert error.extra_server["reason"] == "bad_mode"
    assert "chmod 700 credentials" in error.message


def test_credentials_directory_owned_by_someone_else_is_refused(tmp_path: Path) -> None:
    paths, fake, _volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    other = os.geteuid() + 1
    fake.rewrite(fake.text.replace(f"user_id={os.geteuid()}", f"user_id={other}"))
    from mcp_server_diwall_browser.mount import MountChecker

    checker = MountChecker(mountinfo_path=os.fspath(fake.path), euid=other, stat_fn=fake.stat_fn)
    error = resolve_error(Volume(checker, paths.mount_point))
    assert error.extra_server["reason"] == "bad_owner"


@pytest.mark.skipif(os.geteuid() != 0, reason="changing a file owner needs root")
def test_file_owned_by_someone_else_is_refused(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    path = write_credential(paths, "service.json")
    os.chown(path, 12345, 12345)
    assert resolve_error(volume).extra_server["reason"] == "bad_owner"


def test_symlinked_credential_is_refused_by_name_and_ignored_by_listing(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    target = write_credential(paths, "real-target.json")
    target.rename(paths.project / "real-target.json")
    (paths.credentials / "service.json").symlink_to(paths.project / "real-target.json")
    named = resolve_error(volume, name="service")
    assert (named.code, named.extra_server["reason"]) == ("credential_invalid", "not_regular_file")
    implicit = resolve_error(volume)
    assert implicit.code == "credential_not_bound"


def test_oversized_file_is_refused(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json", username="a" * (16 * 1024))
    assert resolve_error(volume).extra_server["reason"] == "too_large"


def test_file_of_exactly_the_limit_is_read(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    path = write_credential(paths, "service.json")
    size = path.stat().st_size
    write_credential(paths, "service.json", username="a" * (len(FAKE_USERNAME) + 16 * 1024 - size))
    assert path.stat().st_size == 16 * 1024
    assert resolve(volume, SITE, "password").reveal() == FAKE_PASSWORD


def test_missing_key_in_the_bound_file_is_refused(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json", username=DELETE)
    assert resolve(volume, SITE, "password").reveal() == FAKE_PASSWORD
    error = resolve_error(volume, key="username")
    assert error.extra_server["reason"] == "key_missing"


@pytest.mark.parametrize("key", ["passwd", "allowed_origins", "", "PASSWORD"])
def test_only_username_and_password_can_be_requested(tmp_path: Path, key: str) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    assert resolve_error(volume, key=key).code == "credential_invalid"


@pytest.mark.parametrize("name", ["../service", "Service", "-x", "a" * 65, "a/b", ""])
def test_invalid_names_are_refused(tmp_path: Path, name: str) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    assert resolve_error(volume, name=name).code == "credential_invalid"


def test_origin_must_be_an_origin_object(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    with pytest.raises(TypeError):
        resolve(volume, "https://example.invalid", "password")  # type: ignore[arg-type]


# --- origin binding: the check and its counter-proof ---------------------------------

NEIGHBOURS = [
    "https://example.invalid:8443",
    "http://example.invalid",
    "https://account.example.invalid",
    "https://example.invalid.evil.test",
]

Resolver = Callable[..., Secret]


def neighbours_are_refused(resolve_impl: Resolver, paths: VolumePaths, volume: Volume) -> bool:
    write_credential(paths, "service.json", allowed_origins=["https://example.invalid"])
    for neighbour in NEIGHBOURS:
        for name in (None, "service"):
            try:
                resolve_impl(volume, parse_origin(neighbour), "password", name)
            except ToolError as error:
                if error.code != "credential_not_bound":
                    return False
            else:
                return False
    return True


def resolve_trusting_the_name(volume: Volume, page_origin: Origin, key: str, name: str | None = None) -> Secret:
    # Defective: an explicitly named file is taken without the origin check.
    if name is None:
        return resolve(volume, page_origin, key)
    loaded = credentials._load(volume, f"{name}.json")
    return Secret(loaded.values[key], key)


def test_neighbouring_origins_are_refused_by_both_designations(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    assert neighbours_are_refused(resolve, paths, volume)


def test_origin_binding_check_catches_a_resolver_that_trusts_the_name(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    assert not neighbours_are_refused(resolve_trusting_the_name, paths, volume)


def test_not_bound_message_names_only_the_origin_and_the_expected_name(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    write_credential(paths, "other.json", allowed_origins=["https://other.invalid"])
    error = resolve_error(volume, origin=parse_origin("https://account.example.invalid"), name="service")
    assert "https://account.example.invalid" in error.message
    assert "service" in error.message
    for forbidden in ("other", "example.invalid,", FAKE_PASSWORD, FAKE_USERNAME, "https://example.invalid "):
        assert forbidden not in error.message


def test_two_files_bound_to_the_same_origin_are_ambiguous(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "first.json")
    write_credential(paths, "second.json", allowed_origins=["https://other.invalid", "https://example.invalid"])
    error = resolve_error(volume)
    assert error.code == "credential_ambiguous"
    assert "first.json" in error.message and "second.json" in error.message
    assert resolve(volume, SITE, "password", "first").reveal() == FAKE_PASSWORD


def test_invalid_unrelated_file_makes_the_resolution_refuse(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    broken = write_credential(paths, "broken.json")
    os.chmod(broken, 0o640)
    error = resolve_error(volume)
    assert error.code == "credential_invalid" and "broken.json" in error.message


def test_credential_outside_the_project_directory_is_not_found(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    loose = paths.mount_point / "credentials"
    loose.mkdir(mode=0o700)
    for location in (paths.mount_point / "service.json", loose / "service.json"):
        location.write_text(json.dumps({"allowed_origins": ["https://example.invalid"], "password": FAKE_PASSWORD}))
        os.chmod(location, 0o600)
    assert resolve_error(volume).code == "credential_not_bound"
    assert resolve_error(volume, name="service").code == "credential_not_bound"


def test_closed_volume_refuses_before_reading_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    crypt = tmp_path / "Main.enc"
    crypt.mkdir()
    (crypt / "gocryptfs.conf").write_text("{}")
    fake.rewrite(ROOT_LINE + "\n")
    closed = Volume(fake.checker, paths.mount_point, crypt)
    opened: list[str] = []
    real_open = os.open
    real_scandir = os.scandir

    def spy_open(path: object, *args: object, **kwargs: object) -> int:
        opened.append(os.fspath(path))  # type: ignore[arg-type]
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    def spy_scandir(path: object) -> object:
        opened.append(os.fspath(path))  # type: ignore[arg-type]
        return real_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "scandir", spy_scandir)
    error = resolve_error(closed)
    monkeypatch.undo()
    assert error.code == "encrypted_dir_closed"
    assert error.action_expected == "operator_opens_directory"
    assert not [path for path in opened if str(paths.mount_point) in path]


def test_values_are_registered_as_soon_as_they_are_read(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "unrelated.json", allowed_origins=["https://other.invalid"], password="unrelated-pw-value")
    write_credential(paths, "service.json")
    resolve(volume, SITE, "password")
    assert "unrelated-pw-value" not in output.redact_text("x unrelated-pw-value x")


def test_secret_never_shows_its_value() -> None:
    secret = Secret("test-secret-value-2", "password")
    assert repr(secret) == "<secret:password>"
    assert str(secret) == "<secret:password>"
    assert f"{secret}" == "<secret:password>"
    assert str(Secret("u-test", "username")) == "<secret:username>"
    assert secret.reveal() == "test-secret-value-2"
    for attempt in (lambda: pickle.dumps(secret), lambda: copy.copy(secret), lambda: copy.deepcopy(secret)):
        with pytest.raises(TypeError):
            attempt()
    with pytest.raises(AttributeError):
        secret.extra = 1  # type: ignore[attr-defined]


# --- file checks: one battery, run on the real loader and on a loader without checks ---


def _mode_644(paths: VolumePaths) -> None:
    os.chmod(write_credential(paths, "service.json"), 0o644)


def _symlink(paths: VolumePaths) -> None:
    target = write_credential(paths, "target.json")
    target.rename(paths.project / "target.json")
    (paths.credentials / "service.json").symlink_to(paths.project / "target.json")


def _too_large(paths: VolumePaths) -> None:
    write_credential(paths, "service.json", username="a" * (16 * 1024))


def _unknown_key(paths: VolumePaths) -> None:
    write_credential(paths, "service.json", note="x")


def _old_format(paths: VolumePaths) -> None:
    write_credential(paths, "service.json", allowed_origins=DELETE, origines_autorisees=["example.invalid"])


FILE_SCENARIOS = [_mode_644, _symlink, _too_large, _unknown_key, _old_format]


def load_without_checks(volume: Volume, basename: str) -> object:
    # Defective: reads the file with a plain open and trusts its content.
    root = volume.require_mounted()
    with open(root / "credentials" / basename) as handle:
        data = json.load(handle)
    origins = tuple(parse_origin(o) for o in data.get("allowed_origins", ["https://example.invalid"]))
    values = {k: data[k] for k in ("username", "password") if k in data}
    return credentials._CredentialFile(basename=basename, allowed_origins=origins, values=values)


def scenario_is_refused(scenario: Callable[[VolumePaths], None], tmp_path: Path) -> bool:
    paths, _fake, volume = mounted_volume(tmp_path)
    scenario(paths)
    try:
        resolve(volume, SITE, "password", "service")
    except ToolError as error:
        return error.code == "credential_invalid"
    return False


@pytest.mark.parametrize("scenario", FILE_SCENARIOS)
def test_file_check_refuses_the_scenario(scenario: Callable[[VolumePaths], None], tmp_path: Path) -> None:
    assert scenario_is_refused(scenario, tmp_path)


@pytest.mark.parametrize("scenario", FILE_SCENARIOS)
def test_file_check_scenario_passes_a_loader_without_checks(
    scenario: Callable[[VolumePaths], None], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(credentials, "_load", load_without_checks)
    assert not scenario_is_refused(scenario, tmp_path)


# --- duplicate keys ------------------------------------------------------------------------------


def duplicated_credential_refused(tmp_path: Path) -> bool:
    paths, _fake, volume = mounted_volume(tmp_path)
    raw = (
        '{"allowed_origins": ["https://example.invalid"], "username": "alice-test-user", '
        '"password": "first-fake-value", "password": "second-fake-value"}'
    )
    path = paths.credentials / "service.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(raw)
    try:
        resolve(volume, SITE, "password")
    except ToolError as exc:
        return exc.code == "credential_invalid" and exc.extra_server.get("reason") == "not_json"
    return False


def test_a_duplicated_key_in_a_credential_file_is_refused(tmp_path: Path) -> None:
    assert duplicated_credential_refused(tmp_path)


def test_duplicate_check_catches_a_credential_parser_where_the_last_value_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_no_duplicates", dict)
    assert not duplicated_credential_refused(tmp_path)
