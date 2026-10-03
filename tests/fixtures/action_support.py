"""Fakes shared by the action tests: a confirmation channel, a clock, a profile.

Why this module exists:
    The action tests, against the fake browser and against a real one, need
    the same stand-ins for what only a human or the operator's files provide:
    an answer in the confirmation window, the passing of time between two
    actions, and a profile with the sites the test allows.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp_server_diwall_browser.confirm import ConfirmationDecision
from mcp_server_diwall_browser.operator_profile import BrowserSettings, Declarations, Limits, Profile
from mcp_server_diwall_browser.origin import parse_origin


class FakeChannel:
    """A confirmation channel that answers what the test says and counts the questions."""

    def __init__(self, decision: ConfirmationDecision = ConfirmationDecision.GRANTED, *, available: bool = True) -> None:
        self.decision = decision
        self.is_available = available
        self.texts: list[str] = []
        self.on_ask: Callable[[], Any] | None = None

    def available(self) -> bool:
        return self.is_available

    async def ask(self, text: str) -> ConfirmationDecision:
        self.texts.append(text)
        if self.on_ask is not None:
            outcome = self.on_ask()
            if inspect.isawaitable(outcome):
                await outcome
        return self.decision


class FakeClock:
    """Time that passes only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_profile(
    tmp_path: Path,
    *,
    allowed: tuple[str, ...],
    confirm: tuple[str, ...] = (),
    limits: Limits | None = None,
) -> Profile:
    return Profile(
        source=tmp_path / "profile.json",
        schema_version=1,
        secrets_dir=Path("~/Vaults/Main"),
        secrets_crypt_dir=None,
        allowed_origins=tuple(parse_origin(o) for o in allowed),
        confirm_each_change=tuple(parse_origin(o) for o in confirm),
        local_model_origins=(),
        declarations=Declarations(),
        browser=BrowserSettings(family="chromium", profile_dir="~/Vaults/Main/browser-profile"),
        limits=limits or Limits(),
    )


_WINDOW_TOOL = """#!{python}
import json, sys
arguments = sys.argv[1:]
if arguments and arguments[0] == "--help":
    sys.stdout.write("--defaultno")
    sys.exit(0)
with open({record!r}, "a") as handle:
    handle.write(json.dumps(arguments) + "\\n")
with open({answer!r}) as handle:
    answer = handle.read().strip()
if answer == "wait":
    import time
    time.sleep(60)
sys.exit(int(answer) if answer.isdigit() else 1)
"""


class FakeWindow:
    """A kdialog the test writes: it records what it was asked and exits with the answer the test sets."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(mode=0o700, exist_ok=True)
        self.path = directory / "kdialog"
        self.record = directory / "asked.jsonl"
        self.answer_file = directory / "answer"
        self.answer(granted=False)
        self.path.write_text(_WINDOW_TOOL.format(python=sys.executable, record=str(self.record), answer=str(self.answer_file)))
        os.chmod(self.path, 0o700)
        self.present = True

    def answer(self, *, granted: bool = False, wait: bool = False) -> None:
        """Yes, no, or nobody there: the window stays open until the server gives up."""
        self.answer_file.write_text("wait" if wait else "0" if granted else "1")

    def which(self, name: str) -> str | None:
        return str(self.path) if self.present and name == "kdialog" else None

    def questions(self) -> list[str]:
        if not self.record.exists():
            return []
        return [json.loads(line)[-1] for line in self.record.read_text().splitlines()]
