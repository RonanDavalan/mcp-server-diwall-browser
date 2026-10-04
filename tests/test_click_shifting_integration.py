"""A click of the server on a page that moves at the first movement of the pointer, on a real browser.

The page moves each target 60 CSS pixels down at the first mouse movement and puts an intruder where
the target was. What each button received is read apart from the server. The click is the server's
own, by its entry point, on the browser of each page zoom and screen scale. Two issues are allowed:
the click landed on the target, or the server refused it as covered and nothing at all was clicked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from fixtures.browser_launcher import LaunchedBrowser
from fixtures.click_shifting import PAGE, assert_landed_or_refused
from fixtures.click_targets import TARGETS, Received, assert_landed
from fixtures.raw_cdp import RawSession
from test_actions_integration import Rig, mark_of, other_pages, pages, rig  # noqa: F401 - fixtures
from test_click_integration import ACCESSIBLE_NAMES, assert_view
from test_zoom_integration import ZoomedScreen, screen  # noqa: F401 - fixture

from mcp_server_diwall_browser.actions import Actions
from mcp_server_diwall_browser.browser import Tab
from mcp_server_diwall_browser.output import ToolError

pytestmark = pytest.mark.browser

SHIFTED = "document.getElementById({id!r}).style.marginTop"


@pytest.fixture(scope="module")
def launched(screen: ZoomedScreen) -> LaunchedBrowser:
    """The browser of the zoom and scale under test, in place of the one the imported rig starts on its own."""
    return screen.browser


@dataclass(frozen=True)
class Outcome:
    received: Received
    # Whether the page moved its target during the attempt: without it the attempt proves nothing.
    shifted: bool


async def click(rig: Rig, tab: Tab, raw: RawSession, target: str) -> Received:
    """Observe the page as it is, click ``target`` by the server's entry point, read what the page received."""
    observation = await rig.perception.observe(tab, image=False)
    assert observation.snapshot is not None
    mark = mark_of(observation.snapshot, ACCESSIBLE_NAMES[target])
    error = None
    try:
        await rig.actions.click(tab.tab_id, observation.snapshot.snapshot_id, mark)
    except ToolError as caught:
        error = caught.code
    except Exception as caught:  # noqa: BLE001 - an escaped exception is a failed click, to be judged by the check
        error = type(caught).__name__
    return Received(await raw.evaluate("window.__clicks"), await raw.evaluate("window.__stray_clicks"), error)


async def attempt(rig: Rig, screen: ZoomedScreen, target: str, *, then: Callable[[Rig, Tab, RawSession], Awaitable[None]] | None = None) -> Outcome:
    tab = await rig.registry.open_tab(rig.pages.url(PAGE))
    async with rig.raw(tab) as raw:
        await assert_view(raw, screen)
        received = await click(rig, tab, raw, target)
        shifted = await raw.evaluate(SHIFTED.format(id=target)) == "60px"
        if then is not None:
            await then(rig, tab, raw)
    return Outcome(received, shifted)


@pytest.mark.parametrize("target", TARGETS)
async def test_a_click_of_the_server_on_a_page_that_moves_at_the_first_movement_lands_or_is_refused_and_nothing_else_happens(
    screen: ZoomedScreen, rig: Rig, target: str
) -> None:
    outcome = await attempt(rig, screen, target)
    assert outcome.shifted, "the page did not move its target: the attempt proves nothing"
    assert_landed_or_refused(outcome.received, target)


async def test_the_server_refuses_the_click_on_a_page_that_moves_and_clicks_nothing(screen: ZoomedScreen, rig: Rig) -> None:
    """The issue the server takes: a refusal, with the intruder, the decoys and the page all untouched."""
    for target in TARGETS:
        outcome = await attempt(rig, screen, target)
        assert outcome.received.error == "element_obscured", (target, outcome.received)
        assert not any(outcome.received.clicks.values()) and outcome.received.stray == 0, (target, outcome.received)


@pytest.mark.parametrize("target", TARGETS)
async def test_a_second_click_after_the_refusal_on_a_new_observation_lands_on_the_target(
    screen: ZoomedScreen, rig: Rig, target: str
) -> None:
    """The page has moved and moves no more: what the first attempt left is a page a click can land on."""

    async def again(rig: Rig, tab: Tab, raw: RawSession) -> None:
        second = await click(rig, tab, raw, target)
        assert_landed(second, target)

    outcome = await attempt(rig, screen, target, then=again)
    assert outcome.received.error == "element_obscured", outcome.received


async def no_recheck(self: Actions, tab: Tab, backend: int, before: tuple[float, float, float, float], x: int, y: int) -> None:
    """Defective: the press goes to the point the first check was made for, whatever the movement did."""


@pytest.mark.parametrize("target", TARGETS)
async def test_the_shifting_click_check_fails_on_a_server_that_checks_only_before_the_movement(
    screen: ZoomedScreen, rig: Rig, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Actions, "_recheck_before_press", no_recheck)
    outcome = await attempt(rig, screen, target)
    assert outcome.shifted, "the page did not move its target: the attempt proves nothing"
    assert outcome.received.error is None and outcome.received.clicks[f"{target}-intruder"] == 1, outcome.received
    with pytest.raises(AssertionError):
        assert_landed_or_refused(outcome.received, target)
