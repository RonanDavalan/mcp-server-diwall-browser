"""The origin policy: the one place that decides whether an origin is read and acted on."""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from conftest import write_profile
from fixtures.action_support import FakeOriginWindow

from mcp_server_diwall_browser.confirm import ADD_LABEL, DENY_LABEL, SESSION_LABEL, OriginAnswer, build_origin_text
from mcp_server_diwall_browser.operator_profile import Profile, load_profile
from mcp_server_diwall_browser.origin import origin_of_url
from mcp_server_diwall_browser.origin_policy import MAX_WINDOWS, OriginPolicy, is_local_origin, origin_not_allowed
from mcp_server_diwall_browser.output import ToolError

LISTED = "https://listed.example.test"
UNKNOWN = "https://unknown.example.test"


class Bench:
    """A policy over a real profile file in a temporary directory, the operator's own files never touched."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        self.tmp = tmp_path
        self.environ = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config")}
        self.path = write_profile(tmp_path, allowed_origins=[LISTED], **overrides)
        self.window = FakeOriginWindow()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.policy = OriginPolicy(
            profile=self.profile,
            window=lambda: self.window,
            record=lambda event, changes: self.events.append((event, dict(changes))),
        )

    def profile(self) -> Profile:
        return load_profile(self.environ)

    def rewrite(self, **overrides: Any) -> None:
        write_profile(self.tmp, **{"allowed_origins": [LISTED], **overrides})

    async def admit(self, url: str) -> bool:
        return await self.policy.admit(origin_of_url(url))

    def document(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path, origin_policy="discovery")


# --- the key of the profile ---------------------------------------------------------------------------


def test_a_profile_without_the_key_is_strict(tmp_path: Path) -> None:
    write_profile(tmp_path)
    environ = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config")}
    assert load_profile(environ).origin_policy == "strict"


@pytest.mark.parametrize("value", ["strict", "discovery", "open"])
def test_each_of_the_three_values_is_read(tmp_path: Path, value: str) -> None:
    write_profile(tmp_path, origin_policy=value)
    environ = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config")}
    assert load_profile(environ).origin_policy == value


@pytest.mark.parametrize("value", ["libre", "Strict", "OPEN", "", "open ", None, 1, True, ["open"]])
def test_an_unknown_value_is_profile_invalid_never_a_quiet_fallback(tmp_path: Path, value: Any) -> None:
    write_profile(tmp_path, origin_policy=value)
    environ = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config")}
    with pytest.raises(ToolError) as caught:
        load_profile(environ)
    assert caught.value.code == "profile_invalid"
    assert "origin_policy" in caught.value.message


def test_the_key_is_added_and_no_key_is_renamed() -> None:
    from mcp_server_diwall_browser.operator_profile import _ProfileModel

    assert set(_ProfileModel.model_fields) == {
        "schema_version",
        "secrets_dir",
        "secrets_crypt_dir",
        "allowed_origins",
        "confirm_each_change",
        "local_model_origins",
        "declarations",
        "browser",
        "limits",
        "origin_policy",
    }


# --- strict ---------------------------------------------------------------------------------------------


async def test_strict_reads_the_list_and_nothing_else(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    assert await bench.admit(LISTED) is True
    assert await bench.admit(UNKNOWN) is False
    assert await bench.policy.admit(None) is False
    assert bench.window.texts == [] and bench.events == []


async def test_an_empty_list_in_strict_admits_nothing(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    bench.rewrite(allowed_origins=[])
    assert await bench.admit(LISTED) is False


# --- discovery: the window ---------------------------------------------------------------------------------


async def test_a_listed_origin_opens_no_window(bench: Bench) -> None:
    assert await bench.admit(LISTED + "/page") is True
    assert bench.window.texts == []


async def test_an_unlisted_origin_opens_the_window_and_it_holds_the_origin_only(bench: Bench) -> None:
    await bench.admit(UNKNOWN + "/a/very/secret/path?token=abc#frag")
    [text] = bench.window.texts
    assert text == build_origin_text(UNKNOWN)
    assert "secret" not in text and "token" not in text and "frag" not in text
    # Only the origin varies: the rest is the server's own sentences.
    assert build_origin_text("https://a.test").replace("https://a.test", "<o>") == build_origin_text("https://b.test").replace(
        "https://b.test", "<o>"
    )
    for label in (DENY_LABEL, SESSION_LABEL, ADD_LABEL):
        assert label in text


async def test_the_default_answer_is_to_refuse(bench: Bench) -> None:
    # The scripted window answers nothing: the default of the window, refusal.
    assert await bench.admit(UNKNOWN) is False


async def test_this_session_reads_and_acts_and_writes_nothing(bench: Bench) -> None:
    before = bench.path.read_bytes()
    bench.window.answers = [OriginAnswer.SESSION]
    assert await bench.admit(UNKNOWN) is True
    assert bench.path.read_bytes() == before
    assert await bench.admit(UNKNOWN + "/other") is True
    assert len(bench.window.texts) == 1
    assert bench.policy.session_origins == (UNKNOWN,)
    assert await bench.admit("https://another.example.test") is False


async def test_add_to_the_list_writes_the_exact_origin_and_nothing_else(bench: Bench) -> None:
    before = bench.document()
    bench.window.answers = [OriginAnswer.ADD]
    assert await bench.admit(UNKNOWN + "/page") is True
    after = bench.document()
    assert after["allowed_origins"] == [*before["allowed_origins"], UNKNOWN]
    assert {key: value for key, value in after.items() if key != "allowed_origins"} == {
        key: value for key, value in before.items() if key != "allowed_origins"
    }
    assert "*" not in json.dumps(after["allowed_origins"])
    assert stat.S_IMODE(bench.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(bench.path.parent.stat().st_mode) == 0o700
    assert sorted(path.name for path in bench.path.parent.iterdir()) == ["profile.json"]
    assert UNKNOWN in [entry.canonical for entry in bench.profile().allowed_origins]
    # Listed now: no window the second time, nor a session entry.
    assert await bench.admit(UNKNOWN) is True
    assert len(bench.window.texts) == 1 and bench.policy.session_origins == ()


async def test_an_added_origin_with_a_port_is_written_with_its_port(bench: Bench) -> None:
    bench.window.answers = [OriginAnswer.ADD]
    assert await bench.admit("http://unknown.example.test:8080/x") is True
    assert bench.document()["allowed_origins"][-1] == "http://unknown.example.test:8080"


async def test_the_write_is_atomic(bench: Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(source: Any, destination: Any, *args: Any, **kwargs: Any) -> None:
        replaced.append((str(source), str(destination)))
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    bench.window.answers = [OriginAnswer.ADD]
    await bench.admit(UNKNOWN)
    [(source, destination)] = replaced
    assert destination == str(bench.path) and source != destination
    assert os.path.dirname(source) == os.path.dirname(destination)


async def test_a_write_that_fails_leaves_the_profile_as_it_was_and_admits_for_the_session(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = bench.path.read_bytes()

    def broken(source: Any, destination: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    bench.window.answers = [OriginAnswer.ADD]
    assert await bench.admit(UNKNOWN) is True
    assert bench.path.read_bytes() == before
    assert sorted(path.name for path in bench.path.parent.iterdir()) == ["profile.json"]
    assert bench.policy.session_origins == (UNKNOWN,)
    assert ("origin_not_saved", {"origin": UNKNOWN, "policy": "discovery"}) in bench.events


@pytest.mark.parametrize(
    "answer", [OriginAnswer.REFUSED, OriginAnswer.EXPIRED], ids=["refusal", "expiry"]
)
async def test_a_refusal_and_an_expiry_are_not_asked_again_for_the_same_origin(bench: Bench, answer: OriginAnswer) -> None:
    bench.window.answers = [answer, OriginAnswer.ADD]
    assert await bench.admit(UNKNOWN) is False
    assert await bench.admit(UNKNOWN + "/again") is False
    assert await bench.admit(UNKNOWN) is False
    assert len(bench.window.texts) == 1


async def test_an_unavailable_channel_refuses_without_opening_any_window(bench: Bench) -> None:
    bench.window.is_available = False
    assert await bench.admit(UNKNOWN) is False
    assert await bench.admit(UNKNOWN) is False
    assert bench.window.texts == []
    # Nothing was shown, so nothing is held against the origin: a window that can be shown later is asked then.
    bench.window.is_available = True
    bench.window.answers = [OriginAnswer.SESSION]
    assert await bench.admit(UNKNOWN) is True


async def test_an_answer_that_says_unavailable_refuses_and_is_not_repeated_in_the_call(bench: Bench) -> None:
    bench.window.answers = [OriginAnswer.UNAVAILABLE]
    assert await bench.admit(UNKNOWN) is False


async def test_the_sixth_new_origin_of_a_session_opens_no_window(bench: Bench) -> None:
    for number in range(MAX_WINDOWS):
        assert await bench.admit(f"https://site{number}.example.test") is False
    assert len(bench.window.texts) == MAX_WINDOWS == 5
    bench.window.answers = [OriginAnswer.ADD]
    assert await bench.admit("https://site5.example.test") is False
    assert len(bench.window.texts) == 5
    # What is already listed, or admitted for the session, still is.
    assert await bench.admit(LISTED) is True


async def test_the_cap_counts_windows_opened_not_origins_met(bench: Bench) -> None:
    bench.window.answers = [OriginAnswer.SESSION] * 5
    for number in range(5):
        assert await bench.admit(f"https://site{number}.example.test") is True
    assert await bench.admit("https://site0.example.test") is True
    assert await bench.admit("https://site9.example.test") is False
    assert len(bench.window.texts) == 5


async def test_two_calls_at_once_for_the_same_origin_open_one_window(bench: Bench) -> None:
    bench.window.answers = [OriginAnswer.SESSION]

    async def slow() -> None:
        await asyncio.sleep(0.05)

    bench.window.on_ask = slow
    results = await asyncio.gather(*(bench.admit(UNKNOWN) for _ in range(4)))
    assert results == [True] * 4 and len(bench.window.texts) == 1


async def test_every_answer_is_an_event_of_the_journal(bench: Bench) -> None:
    bench.window.answers = [OriginAnswer.REFUSED, OriginAnswer.SESSION, OriginAnswer.ADD, OriginAnswer.EXPIRED]
    for name in ("a", "b", "c", "d"):
        await bench.admit(f"https://{name}.example.test")
    assert [(event, changes["answer"]) for event, changes in bench.events] == [
        ("origin_decision", "refused"),
        ("origin_decision", "session"),
        ("origin_decision", "add"),
        ("origin_decision", "expired"),
    ]
    assert all(set(changes) == {"origin", "answer", "policy"} and changes["policy"] == "discovery" for _, changes in bench.events)


# --- open ---------------------------------------------------------------------------------------------------


async def test_open_reads_a_public_origin_never_listed(tmp_path: Path) -> None:
    bench = Bench(tmp_path, origin_policy="open")
    assert await bench.admit("https://www.france-travail.example.test/offres") is True
    assert await bench.admit("http://8.8.8.8:8080/") is True
    assert bench.window.texts == []


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1",
        "http://172.16.4.2:8000",
        "http://127.0.0.1:9222",
        "http://169.254.169.254",
        "http://0.0.0.0",
        "http://100.64.0.1",
        "http://192.0.2.1",
        "http://[::1]:8000",
        "http://[fe80::1]",
        "http://[fc00::1]",
        "http://[::ffff:10.0.0.1]",
        "http://localhost:8000",
        "http://LOCALHOST",
        "http://api.localhost",
        "http://machine.local",
        "https://printer.office.local:631",
        "http://machine.local.",
    ],
)
async def test_open_still_refuses_a_local_origin_that_is_not_listed(tmp_path: Path, url: str) -> None:
    bench = Bench(tmp_path, origin_policy="open")
    assert is_local_origin(origin_of_url(url)) is True
    assert await bench.admit(url) is False
    assert bench.window.texts == []


async def test_a_local_origin_that_is_listed_is_read_in_open(tmp_path: Path) -> None:
    bench = Bench(tmp_path, origin_policy="open")
    bench.rewrite(origin_policy="open", allowed_origins=[LISTED, "http://localhost:8000", "http://machine.local"])
    assert await bench.admit("http://localhost:8000/x") is True
    assert await bench.admit("http://machine.local/") is True
    assert await bench.admit("http://localhost:8001/") is False


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test",
        "https://localhost.example.test",
        "https://local.example.test",
        "https://notlocal.test",
        "http://8.8.8.8",
        "http://1.1.1.1:8080",
        "http://[2606:4700:4700::1111]",
    ],
)
def test_a_public_name_or_address_is_not_local(url: str) -> None:
    assert is_local_origin(origin_of_url(url)) is False


# --- the profile is read at each decision --------------------------------------------------------------------


async def test_a_change_of_policy_takes_effect_without_a_restart(bench: Bench) -> None:
    assert await bench.admit(UNKNOWN) is False and len(bench.window.texts) == 1
    bench.rewrite(origin_policy="open")
    assert await bench.admit("https://anything.example.test") is True
    bench.rewrite(origin_policy="strict")
    assert await bench.admit("https://anything.example.test") is False
    assert await bench.admit("https://third.example.test") is False
    assert len(bench.window.texts) == 1
    bench.rewrite(origin_policy="discovery")
    bench.window.answers = [OriginAnswer.SESSION]
    assert await bench.admit("https://third.example.test") is True


async def test_an_origin_admitted_for_the_session_stops_being_admitted_in_strict(bench: Bench) -> None:
    bench.window.answers = [OriginAnswer.SESSION]
    assert await bench.admit(UNKNOWN) is True
    bench.rewrite(origin_policy="strict")
    assert await bench.admit(UNKNOWN) is False


async def test_an_invalid_value_written_while_running_refuses_the_decision(bench: Bench) -> None:
    bench.rewrite(origin_policy="libre")
    with pytest.raises(ToolError) as caught:
        await bench.admit(UNKNOWN)
    assert caught.value.code == "profile_invalid"


# --- the refusal ------------------------------------------------------------------------------------------------


def test_the_refusal_names_the_origin_and_says_what_the_operator_can_do() -> None:
    error = origin_not_allowed("https://unknown.example.test")
    assert error.code == "origin_not_allowed"
    assert error.extra_server == {"origin": "https://unknown.example.test"}
    assert "https://unknown.example.test" in error.message
    assert "allowed_origins" in error.message and "origin_policy" in error.message
    assert "discovery" in error.message and "open" in error.message
    for forbidden in (".json", "/home", "Vaults", "profile.json", "~/"):
        assert forbidden not in error.message


def test_the_refusal_of_a_page_without_an_origin_does_not_invent_one() -> None:
    error = origin_not_allowed(None)
    assert error.extra_server == {"origin": None}
    assert "None" not in error.message


# --- one place decides ----------------------------------------------------------------------------------------------


def test_no_module_but_the_policy_compares_an_origin_with_the_list() -> None:
    root = Path(__file__).resolve().parent.parent / "src" / "mcp_server_diwall_browser"
    for path in sorted(root.rglob("*.py")):
        if path.name == "origin_policy.py":
            continue
        text = path.read_text()
        assert not re.search(r"origin_allowed\([^)]*allowed_origins", text), path.name
        assert "AllowedOrigins" not in text, path.name
