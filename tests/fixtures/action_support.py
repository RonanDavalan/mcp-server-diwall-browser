"""Fakes shared by the action tests: a confirmation channel, a clock, a profile.

Why this module exists:
    The action tests, against the fake browser and against a real one, need
    the same stand-ins for what only a human or the operator's files provide:
    an answer in the confirmation window, the passing of time between two
    actions, and a profile with the sites the test allows.
"""

from __future__ import annotations

import inspect
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
