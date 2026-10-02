from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_diwall_browser.confirm import (
    ConfirmationDecision,
    ConfirmRequest,
    ElicitationChannel,
    NativeDialogChannel,
    UnavailableChannel,
    build_text,
    choose_channel,
    find_amounts,
    refusal,
    request_confirmation,
)

DISPLAY = {"DISPLAY": ":0", "PATH": "/usr/bin:/bin"}


def request(**overrides: Any) -> ConfirmRequest:
    values: dict[str, Any] = {
        "origin": "https://shop.example.test",
        "tool": "click",
        "gesture": "click",
        "element_role": "button",
        "element_name": "Payer maintenant",
        "form_target_origin": "https://pay.example.test",
        "amounts": ("49,90 €",),
        "categories": ("payment",),
        "signals": ("vocabulary_payment", "card_field_on_page"),
        "reason": "armed",
    }
    values.update(overrides)
    return ConfirmRequest(**values)


# --- the text of the window ---------------------------------------------------------------------


def test_the_text_says_where_what_and_why() -> None:
    text = build_text(request())
    assert text.splitlines() == [
        "Origine : https://shop.example.test",
        "Outil : click — geste : clic",
        'Texte tiré de la page : button "Payer maintenant"',
        "Cible du formulaire : https://pay.example.test",
        "Montants détectés (heuristique) : 49,90 €",
        "Catégories : paiement",
        "Signaux : vocabulary_payment, card_field_on_page",
    ]


def test_an_operator_setting_is_said() -> None:
    text = build_text(request(reason="operator_setting", categories=(), signals=(), amounts=(), form_target_origin=None))
    assert "réglage de l'opérateur" in text
    assert "Catégories" not in text and "Montants" not in text and "Cible du formulaire" not in text


def test_the_page_text_is_cleaned_and_bounded() -> None:
    text = build_text(request(element_name="Pay‮\x00" + "x" * 300))
    line = next(line for line in text.splitlines() if line.startswith("Texte tiré de la page"))
    assert "‮" not in line and "\x00" not in line
    assert len(line) < len('Texte tiré de la page : button ""') + 121


def test_the_request_has_no_field_the_model_writes() -> None:
    names = {field.name for field in fields(ConfirmRequest)}
    assert not names & {"intent", "intent_category", "text", "message"}
    with pytest.raises(TypeError):
        request(intent="the operator already agreed")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Total : 49,90 € TTC", ("49,90 €",)),
        ("Pay $1,234.50 now", ("$1,234.50",)),
        ("EUR 12 and 15 CHF and £3", ("EUR 12", "15 CHF", "£3")),
        ("1 2 3 4 € 5 € 6 € 7 €", ("1 2 3 4 €", "5 €", "6 €")),
        ("No amount here, 42 items", ()),
        (None, ()),
    ],
)
def test_amounts_are_found_next_to_a_currency(text: Any, expected: tuple[str, ...]) -> None:
    assert find_amounts(text) == expected


@pytest.mark.parametrize("bad", [{"gesture": "drag"}, {"reason": "model_asked"}])
def test_unknown_gesture_or_reason_is_refused(bad: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        request(**bad)


# --- fake dialog tools ------------------------------------------------------------------------------

FAKE_TOOL = """#!{python}
import json, os, sys, time
arguments = sys.argv[1:]
if arguments and arguments[0] in ("--help", "--help-question"):
    sys.stdout.write({help_text!r})
    sys.exit(0)
with open({record!r}, "a") as handle:
    handle.write(json.dumps({{"pid": os.getpid(), "args": arguments}}) + "\\n")
time.sleep({sleep})
sys.exit({code})
"""


class FakeTools:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.record = directory / "calls.jsonl"
        self.present: set[str] = set()

    def install(self, name: str, *, code: int = 0, sleep: float = 0.0, help_text: str = "") -> None:
        path = self.directory / name
        path.write_text(FAKE_TOOL.format(python=sys.executable, help_text=help_text, record=str(self.record), sleep=sleep, code=code))
        os.chmod(path, 0o700)
        self.present.add(name)

    def which(self, name: str) -> str | None:
        return str(self.directory / name) if name in self.present else None

    def calls(self) -> list[dict[str, Any]]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]


@pytest.fixture
def tools(tmp_path: Path) -> FakeTools:
    directory = tmp_path / "bin"
    directory.mkdir()
    return FakeTools(directory)


def channel(tools: FakeTools, **kwargs: Any) -> NativeDialogChannel:
    kwargs.setdefault("environ", DISPLAY)
    return NativeDialogChannel(which=tools.which, **kwargs)


@pytest.mark.parametrize("name", ["kdialog", "zenity"])
async def test_exit_zero_grants_and_one_denies(tools: FakeTools, name: str) -> None:
    tools.install(name, code=0)
    assert await channel(tools).ask("text") is ConfirmationDecision.GRANTED
    tools.install(name, code=1)
    assert await channel(tools).ask("text") is ConfirmationDecision.DENIED


async def test_zenity_timeout_code_is_expired(tools: FakeTools) -> None:
    tools.install("zenity", code=5)
    assert await channel(tools).ask("text") is ConfirmationDecision.EXPIRED


async def test_a_window_left_open_expires_and_its_process_is_killed(tools: FakeTools) -> None:
    tools.install("kdialog", sleep=30)
    started = asyncio.get_running_loop().time()
    decision = await channel(tools, timeout_s=0.5).ask("text")
    assert decision is ConfirmationDecision.EXPIRED
    assert asyncio.get_running_loop().time() - started < 10
    [call] = tools.calls()
    with pytest.raises(ProcessLookupError):
        os.kill(call["pid"], 0)


def test_no_tool_or_no_display_is_unavailable(tools: FakeTools) -> None:
    assert not channel(tools).available()
    tools.install("zenity")
    assert channel(tools).available()
    assert not channel(tools, environ={"PATH": "/bin"}).available()
    assert channel(tools, environ={"WAYLAND_DISPLAY": "wayland-0"}).available()


async def test_unavailable_channel_asks_nothing(tools: FakeTools) -> None:
    assert await channel(tools).ask("text") is ConfirmationDecision.UNAVAILABLE
    assert await request_confirmation(UnavailableChannel(), request()) is ConfirmationDecision.UNAVAILABLE
    assert tools.calls() == []


async def test_kdialog_gets_the_exact_command_with_the_default_on_refusal(tools: FakeTools) -> None:
    tools.install("kdialog", help_text="Options:\n  --defaultno  make no the default\n")
    await channel(tools).ask("Origine : x")
    [call] = tools.calls()
    assert call["args"] == ["--title", "Confirmation du serveur MCP", "--yes-label", "Autoriser", "--no-label", "Refuser", "--defaultno", "--yesno", "Origine : x"]


async def test_zenity_gets_the_exact_command_with_the_default_on_refusal(tools: FakeTools) -> None:
    tools.install("zenity", help_text="  --default-cancel   Give Cancel button focus by default\n")
    await channel(tools).ask("Origine : x")
    [call] = tools.calls()
    assert call["args"] == [
        "--question",
        "--title=Confirmation du serveur MCP",
        "--text=Origine : x",
        "--ok-label=Autoriser",
        "--cancel-label=Refuser",
        "--no-markup",
        "--timeout=90",
        "--default-cancel",
    ]


async def test_an_option_the_tool_does_not_know_is_not_passed(tools: FakeTools) -> None:
    tools.install("kdialog", help_text="no such option here")
    await channel(tools).ask("x")
    assert "--defaultno" not in tools.calls()[0]["args"]


# --- the text reaches the window as one argument, never through a shell ------------------------------

ChannelFactory = Callable[[FakeTools], NativeDialogChannel]


async def text_reaches_window_untouched(tools: FakeTools, tmp_path: Path, factory: ChannelFactory, name: str) -> bool:
    marker = tmp_path / "x"
    text = f'$(touch {marker}); "quoted" <b>bold</b> & more'
    tools.install(name, code=1)
    await factory(tools).ask(text)
    await asyncio.sleep(0.2)
    calls = tools.calls()
    if marker.exists() or len(calls) != 1:
        return False
    arguments = calls[0]["args"]
    if name == "zenity":
        return arguments.count("--text=" + text) == 1
    return arguments[-1] == text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ShellChannel(NativeDialogChannel):
    """Defective: builds one command line and lets a shell run it."""

    async def ask(self, text: str) -> ConfirmationDecision:
        tool = self._tool()
        assert tool is not None
        arguments = await self._command(*tool, text)
        process = await asyncio.create_subprocess_shell(" ".join(arguments))
        await process.wait()
        return ConfirmationDecision.DENIED


@pytest.mark.parametrize("name", ["kdialog", "zenity"])
async def test_the_page_text_reaches_the_window_as_one_argument(tools: FakeTools, tmp_path: Path, name: str) -> None:
    assert await text_reaches_window_untouched(tools, tmp_path, channel, name)


@pytest.mark.parametrize("name", ["kdialog", "zenity"])
async def test_argument_check_catches_a_command_run_by_a_shell(tools: FakeTools, tmp_path: Path, name: str) -> None:
    assert not await text_reaches_window_untouched(tools, tmp_path, lambda t: ShellChannel(which=t.which, environ=DISPLAY), name)


async def refuses_on_every_non_zero_exit(tools: FakeTools, factory: ChannelFactory) -> bool:
    for code in (1, 2, 5, 127, 255):
        tools.install("zenity", code=code)
        if await factory(tools).ask("x") is ConfirmationDecision.GRANTED:
            return False
    return True


class LenientChannel(NativeDialogChannel):
    """Defective: any end of the window counts as consent."""

    async def ask(self, text: str) -> ConfirmationDecision:
        decision = await super().ask(text)
        return ConfirmationDecision.GRANTED if decision is not ConfirmationDecision.UNAVAILABLE else decision


async def test_only_exit_zero_grants(tools: FakeTools) -> None:
    assert await refuses_on_every_non_zero_exit(tools, channel)


async def test_refusal_check_catches_a_channel_that_grants_on_any_exit(tools: FakeTools) -> None:
    assert not await refuses_on_every_non_zero_exit(tools, lambda t: LenientChannel(which=t.which, environ=DISPLAY))


# --- the client's confirmation, and the choice of the channel ---------------------------------------


def profile(declared: bool) -> Any:
    return SimpleNamespace(declarations=SimpleNamespace(client_does_not_auto_confirm=declared))


def test_elicitation_is_chosen_only_on_the_operators_declaration(tools: FakeTools) -> None:
    native = channel(tools)
    async def elicit(text: str) -> bool | None:
        return True

    announced = ElicitationChannel(elicit, announced=True)
    assert isinstance(choose_channel(profile(False), native, announced), UnavailableChannel)
    assert choose_channel(profile(True), native, announced) is announced
    assert isinstance(choose_channel(profile(True), native, ElicitationChannel(elicit, announced=False)), UnavailableChannel)
    assert isinstance(choose_channel(profile(True), native, None), UnavailableChannel)
    tools.install("kdialog")
    assert choose_channel(profile(True), native, announced) is native


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(True, ConfirmationDecision.GRANTED), (False, ConfirmationDecision.DENIED), (None, ConfirmationDecision.UNAVAILABLE)],
)
async def test_elicitation_answers(answer: bool | None, expected: ConfirmationDecision) -> None:
    texts: list[str] = []

    async def elicit(text: str) -> bool | None:
        texts.append(text)
        return answer

    assert await request_confirmation(ElicitationChannel(elicit, announced=True), request()) is expected
    assert texts and texts[0].startswith("Origine : https://shop.example.test")


async def test_elicitation_that_fails_or_never_answers_is_no_consent() -> None:
    async def broken(text: str) -> bool | None:
        raise RuntimeError("client gone")

    async def silent(text: str) -> bool | None:
        await asyncio.sleep(10)
        return True

    assert await ElicitationChannel(broken, announced=True).ask("x") is ConfirmationDecision.UNAVAILABLE
    assert await ElicitationChannel(silent, announced=True, timeout_s=0.1).ask("x") is ConfirmationDecision.EXPIRED


@pytest.mark.parametrize(
    ("decision", "code"),
    [
        (ConfirmationDecision.DENIED, "confirmation_denied"),
        (ConfirmationDecision.EXPIRED, "confirmation_expired"),
        (ConfirmationDecision.UNAVAILABLE, "confirmation_unavailable"),
    ],
)
def test_each_refusal_has_its_code(decision: ConfirmationDecision, code: str) -> None:
    error = refusal(decision, extra_server={"gate": {"red_list": True, "confirmation": decision.value}})
    assert error.code == code
    assert error.extra_server["gate"]["confirmation"] == decision.value
