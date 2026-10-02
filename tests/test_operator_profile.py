from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import DELETE, default_profile, write_profile

from mcp_server_diwall_browser import operator_profile
from mcp_server_diwall_browser.operator_profile import ENV_VAR, Limits, Profile, load_profile
from mcp_server_diwall_browser.origin import Origin
from mcp_server_diwall_browser.output import ToolError


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"XDG_CONFIG_HOME": str(tmp_path / "config"), "HOME": str(tmp_path / "home"), **extra}


def load_error(tmp_path: Path, **overrides: Any) -> ToolError:
    write_profile(tmp_path, **overrides)
    with pytest.raises(ToolError) as caught:
        load_profile(env_for(tmp_path))
    return caught.value


def test_valid_profile_is_loaded_with_typed_values_and_defaults(tmp_path: Path) -> None:
    write_profile(tmp_path, allowed_origins=["https://example.invalid", "https://account.example.invalid:8443"])
    profile = load_profile(env_for(tmp_path))
    assert isinstance(profile, Profile)
    assert profile.allowed_origins == (
        Origin("https", "example.invalid", 443),
        Origin("https", "account.example.invalid", 8443),
    )
    assert profile.secrets_dir == Path("~/Vaults/Main")
    assert profile.secrets_crypt_dir is None
    assert profile.confirm_each_change == ()
    assert profile.local_model_origins == ()
    assert profile.declarations.local_model_client is False
    assert profile.declarations.client_does_not_auto_confirm is False
    assert profile.browser.family == "chromium"
    assert profile.browser.debug_port == 9222
    assert (profile.limits.min_action_delay_ms, profile.limits.max_actions_per_session, profile.limits.max_pages_per_session) == (250, 200, 50)


def test_limits_have_defaults_without_any_file() -> None:
    limits = Limits()
    assert (limits.min_action_delay_ms, limits.max_actions_per_session, limits.max_pages_per_session) == (250, 200, 50)


def test_empty_allowed_origins_list_is_valid(tmp_path: Path) -> None:
    write_profile(tmp_path, allowed_origins=[])
    assert load_profile(env_for(tmp_path)).allowed_origins == ()


# One row per line of the profile table: value out of bounds, wrong type
# (a boolean for an integer included), path forms and origin forms.
INVALID_ROWS: list[tuple[str, dict[str, Any]]] = [
    ("schema_version", {"schema_version": 2}),
    ("schema_version", {"schema_version": True}),
    ("schema_version", {"schema_version": "1"}),
    ("secrets_dir", {"secrets_dir": "Vaults/Main"}),
    ("secrets_dir", {"secrets_dir": "~/Vaults/Main"}),
    ("secrets_dir", {"secrets_dir": "/" + "a" * 4096}),
    ("secrets_dir", {"secrets_dir": 42}),
    ("secrets_crypt_dir", {"secrets_crypt_dir": "relative/Main.enc"}),
    ("secrets_crypt_dir", {"secrets_crypt_dir": "~/x"}),
    ("allowed_origins", {"allowed_origins": ["https://*.example.invalid"]}),
    ("allowed_origins", {"allowed_origins": ["https://alice@example.invalid"]}),
    ("allowed_origins", {"allowed_origins": ["https://example.invalid/path"]}),
    ("allowed_origins", {"allowed_origins": ["https://example.invalid/"]}),
    ("allowed_origins", {"allowed_origins": ["example.invalid"]}),
    ("allowed_origins", {"allowed_origins": [f"https://h{i}.example.invalid" for i in range(201)]}),
    ("allowed_origins", {"allowed_origins": "https://example.invalid"}),
    ("confirm_each_change", {"confirm_each_change": ["https://*.example.invalid"]}),
    ("local_model_origins", {"local_model_origins": ["http://127.0.0.1:8080/v1"]}),
    ("declarations", {"declarations": {"local_model_client": 1}}),
    ("declarations", {"declarations": {"client_does_not_auto_confirm": "yes"}}),
    ("declarations", {"declarations": {"other": True}}),
    ("browser", {"browser": {"family": "firefox", "profile_dir": "~/p"}}),
    ("browser", {"browser": {"family": "chrome", "profile_dir": "p"}}),
    ("browser", {"browser": {"family": "chrome", "profile_dir": "~/p", "debug_port": 1023}}),
    ("browser", {"browser": {"family": "chrome", "profile_dir": "~/p", "debug_port": 65536}}),
    ("browser", {"browser": {"family": "chrome", "profile_dir": "~/p", "debug_port": True}}),
    ("browser", {"browser": {"family": "chrome", "profile_dir": "~/p", "debug_port": "127.0.0.1:9222"}}),
    ("limits", {"limits": {"min_action_delay_ms": -1}}),
    ("limits", {"limits": {"min_action_delay_ms": 60001}}),
    ("limits", {"limits": {"max_actions_per_session": 0}}),
    ("limits", {"limits": {"max_actions_per_session": 100001}}),
    ("limits", {"limits": {"max_pages_per_session": 0}}),
    ("limits", {"limits": {"max_pages_per_session": True}}),
    ("limits", {"limits": {"max_pages_per_session": 10.0}}),
    ("upload_dir", {"upload_dir": "~/upload"}),
    ("unknown_key", {"unknown_key": 1}),
]


@pytest.mark.parametrize(("key", "overrides"), INVALID_ROWS)
def test_invalid_profile_value_is_refused_and_named(tmp_path: Path, key: str, overrides: dict[str, Any]) -> None:
    error = load_error(tmp_path, **overrides)
    assert error.code == "profile_invalid"
    assert key in error.message


@pytest.mark.parametrize(
    "overrides",
    [
        {"limits": {"min_action_delay_ms": 0, "max_actions_per_session": 100000, "max_pages_per_session": 1}},
        {"browser": {"family": "brave", "profile_dir": "~/p", "debug_port": 1024}},
        {"browser": {"family": "chrome", "profile_dir": "~/p", "debug_port": 65535}},
        {"allowed_origins": [f"https://h{i}.example.invalid" for i in range(200)]},
        {"secrets_crypt_dir": "~/.local/share/plasma-vault/Main.enc"},
        {"declarations": {"local_model_client": True, "client_does_not_auto_confirm": True}},
        {"local_model_origins": ["http://127.0.0.1:8080"], "confirm_each_change": ["https://example.invalid"]},
    ],
)
def test_boundary_values_are_accepted(tmp_path: Path, overrides: dict[str, Any]) -> None:
    write_profile(tmp_path, **overrides)
    load_profile(env_for(tmp_path))


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"secrets_dir": DELETE}, "secrets_dir"),
        ({"schema_version": DELETE}, "schema_version"),
        ({"allowed_origins": DELETE}, "allowed_origins"),
        ({"browser": DELETE}, "browser"),
        ({"browser": {"family": "chrome"}}, "browser.profile_dir"),
    ],
)
def test_missing_required_value_is_not_configured(tmp_path: Path, overrides: dict[str, Any], missing: str) -> None:
    error = load_error(tmp_path, **overrides)
    assert error.code == "not_configured"
    assert missing in error.message
    assert ENV_VAR in error.message


def test_absent_profile_names_both_ways_and_lists_no_key(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as caught:
        load_profile(env_for(tmp_path))
    error = caught.value
    assert error.code == "not_configured"
    assert ENV_VAR in error.message
    standard = str(tmp_path / "config" / "mcp-server-diwall-browser" / "profile.json")
    assert standard in error.message
    without_paths = error.message.replace(standard, "")
    for key in default_profile():
        assert key not in without_paths


def test_standard_location_falls_back_to_home_config(tmp_path: Path) -> None:
    env = {"HOME": "~"}
    assert operator_profile.standard_profile_path(env) == Path("~/.config/mcp-server-diwall-browser/profile.json")
    env = {"HOME": "~", "XDG_CONFIG_HOME": "relative"}
    assert operator_profile.standard_profile_path(env) == Path("~/.config/mcp-server-diwall-browser/profile.json")


def test_environment_variable_wins_over_the_standard_file(tmp_path: Path) -> None:
    write_profile(tmp_path, allowed_origins=["https://standard.example.invalid"])
    other = tmp_path / "other"
    write_profile(other, allowed_origins=["https://designated.example.invalid"])
    designated = other / "config" / "mcp-server-diwall-browser" / "profile.json"
    profile = load_profile(env_for(tmp_path, **{ENV_VAR: str(designated)}))
    assert profile.allowed_origins == (Origin("https", "designated.example.invalid", 443),)
    assert profile.source == designated


def test_relative_environment_variable_is_refused(tmp_path: Path) -> None:
    write_profile(tmp_path)
    with pytest.raises(ToolError) as caught:
        load_profile(env_for(tmp_path, **{ENV_VAR: "profile.json"}))
    assert caught.value.code == "profile_invalid"


def test_profile_readable_by_others_is_refused_with_the_fix(tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    os.chmod(path, 0o644)
    with pytest.raises(ToolError) as caught:
        load_profile(env_for(tmp_path))
    assert caught.value.code == "profile_invalid"
    assert caught.value.extra_server["reason"] == "permissions"
    assert f"chmod 600 {os.path.realpath(path)}" in caught.value.message


def test_profile_directory_open_to_others_is_refused_with_the_fix(tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    os.chmod(path.parent, 0o755)
    with pytest.raises(ToolError) as caught:
        load_profile(env_for(tmp_path))
    assert caught.value.extra_server["reason"] == "permissions"
    assert f"chmod 700 {os.path.realpath(path.parent)}" in caught.value.message


def test_profile_owned_by_someone_else_is_refused(tmp_path: Path) -> None:
    write_profile(tmp_path)
    with pytest.raises(ToolError) as caught:
        load_profile(env_for(tmp_path), euid=os.geteuid() + 1)
    assert caught.value.extra_server["reason"] == "permissions"


def test_profile_that_is_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    path.unlink()
    path.mkdir(mode=0o700)
    with pytest.raises(ToolError) as caught:
        load_profile(env_for(tmp_path))
    assert caught.value.extra_server["reason"] == "permissions"


@pytest.mark.parametrize("raw", ["not json", "[1]", '{"schema_version": 1, "schema_version": 1}'])
def test_unparsable_profile_is_refused(tmp_path: Path, raw: str) -> None:
    assert load_error(tmp_path, _raw=raw).code == "profile_invalid"


def test_validation_message_never_quotes_a_value(tmp_path: Path) -> None:
    error = load_error(tmp_path, secrets_dir="relative/very-distinctive-value")
    assert "very-distinctive-value" not in error.message


def test_profile_change_is_seen_on_the_next_load(tmp_path: Path) -> None:
    write_profile(tmp_path, allowed_origins=["https://first.example.invalid"])
    first = load_profile(env_for(tmp_path))
    write_profile(tmp_path, allowed_origins=["https://second.example.invalid"])
    second = load_profile(env_for(tmp_path))
    assert first.allowed_origins == (Origin("https", "first.example.invalid", 443),)
    assert second.allowed_origins == (Origin("https", "second.example.invalid", 443),)


# --- red list keys: the check and its counter-proof --------------------------------

REDLIST_PROFILES = [
    {"payment": {"confirm": True}},
    {"Delete": True},
    {"limits": {"RED_LIST": []}},
    {"browser": {"family": "chrome", "profile_dir": "~/p", "secret_rotation": 1}},
    {"declarations": {"rotate_secret": False}},
    {"confirm_each_change": [{"deletion": 1}]},
    {"redlist": 1},
]

Loader = Callable[[dict[str, str]], Profile]


def redlist_keys_get_their_own_code(load: Loader, tmp_path: Path) -> bool:
    for index, overrides in enumerate(REDLIST_PROFILES):
        directory = tmp_path / str(index)
        write_profile(directory, **overrides)
        try:
            load(env_for(directory))
        except ToolError as error:
            if error.code != "redlist_in_profile":
                return False
        else:
            return False
    return True


def load_treating_redlist_keys_as_unknown(env: dict[str, str]) -> Profile:
    # Defective: no red list walk, so such a key is only an unknown key.
    original = operator_profile._reject_redlist_keys
    operator_profile._reject_redlist_keys = lambda _data: None  # type: ignore[assignment]
    try:
        return load_profile(env)
    finally:
        operator_profile._reject_redlist_keys = original


def test_redlist_keys_are_refused_with_their_own_code(tmp_path: Path) -> None:
    assert redlist_keys_get_their_own_code(load_profile, tmp_path)


def test_redlist_check_catches_a_loader_that_sees_them_as_unknown_keys(tmp_path: Path) -> None:
    assert not redlist_keys_get_their_own_code(load_treating_redlist_keys_as_unknown, tmp_path)


def test_redlist_key_is_refused_before_any_other_validation(tmp_path: Path) -> None:
    error = load_error(tmp_path, schema_version="wrong", secrets_dir=DELETE, payment=1)
    assert error.code == "redlist_in_profile"


def test_written_profile_is_private(tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(path.read_text())["schema_version"] == 1


# --- the content of a profile with wrong rights is never read -------------------------------

MARKER = "distinctive-profile-content-8"
Loader = Callable[[dict[str, str], int | None], Profile]

RIGHTS_DEFECTS: list[tuple[str, Callable[[Path], None], int]] = [
    ("file readable by others", lambda path: os.chmod(path, 0o644), 0),
    ("directory open to others", lambda path: os.chmod(path.parent, 0o755), 0),
    ("file of another user", lambda path: None, 1),
]


def content_never_quoted_on_wrong_rights(loader: Loader, tmp_path: Path) -> bool:
    # Content that would fail any later check and quote itself if it were read.
    raw = '{"' + MARKER + '": "' + MARKER + '", "payment": "' + MARKER + '"'
    for number, (_case, spoil, euid_shift) in enumerate(RIGHTS_DEFECTS):
        base = tmp_path / str(number)
        base.mkdir()
        path = write_profile(base, _raw=raw)
        spoil(path)
        try:
            loader(env_for(base), os.geteuid() + euid_shift)
        except ToolError as exc:
            text = json.dumps([exc.code, exc.message, exc.extra_server])
            if MARKER in text or exc.extra_server.get("reason") != "permissions":
                return False
        else:
            return False
    return True


def real_loader(environ: dict[str, str], euid: int | None) -> Profile:
    return load_profile(environ, euid=euid)


def content_first_loader(environ: dict[str, str], euid: int | None) -> Profile:
    # Defective: parses the file before looking at who may write it.
    path = operator_profile.standard_profile_path(environ)
    raw = path.read_text()
    try:
        json.loads(raw)
    except ValueError:
        raise ToolError("profile_invalid", f"profil illisible : {raw[:60]}", extra_server={"reason": "not_json"}) from None
    return load_profile(environ, euid=euid)


def test_content_of_a_profile_with_wrong_rights_never_appears_in_the_error(tmp_path: Path) -> None:
    assert content_never_quoted_on_wrong_rights(real_loader, tmp_path)


def test_rights_check_catches_a_loader_that_reads_the_content_first(tmp_path: Path) -> None:
    assert not content_never_quoted_on_wrong_rights(content_first_loader, tmp_path)


# --- duplicate keys and size ------------------------------------------------------------------

DUPLICATED = (
    '{"schema_version": 1, "secrets_dir": "~/Vaults/Main", '
    '"allowed_origins": ["https://example.invalid"], "allowed_origins": ["https://other.example.invalid"], '
    '"browser": {"family": "chromium", "profile_dir": "~/Vaults/Main/browser-profile"}}'
)


def duplicate_key_refused(tmp_path: Path) -> bool:
    write_profile(tmp_path, _raw=DUPLICATED)
    try:
        load_profile(env_for(tmp_path))
    except ToolError as exc:
        return exc.code == "profile_invalid" and exc.extra_server.get("reason") == "not_json"
    return False


def test_a_duplicated_key_in_the_profile_is_refused(tmp_path: Path) -> None:
    assert duplicate_key_refused(tmp_path)


def test_duplicate_check_catches_a_parser_where_the_last_value_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator_profile, "_no_duplicates", dict)
    assert not duplicate_key_refused(tmp_path)


PROFILE_CAP = operator_profile.MAX_PROFILE_BYTES


def oversized_profile_refused(tmp_path: Path) -> bool:
    padding = " " * (PROFILE_CAP + 1)
    write_profile(tmp_path, _raw=json.dumps(default_profile()) + padding)
    try:
        load_profile(env_for(tmp_path))
    except ToolError as exc:
        return exc.extra_server.get("reason") == "too_large"
    return False


def test_a_profile_larger_than_the_cap_is_refused(tmp_path: Path) -> None:
    assert oversized_profile_refused(tmp_path)


def test_size_check_catches_a_loader_without_a_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator_profile, "MAX_PROFILE_BYTES", 10 * 1024 * 1024)
    assert not oversized_profile_refused(tmp_path)
