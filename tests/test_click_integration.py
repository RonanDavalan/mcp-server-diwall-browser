"""A click of the server lands on its target at every page zoom and screen scale, on a real browser.

The truth is read apart from the server: the page counts the clicks each of its buttons receives, and
a separate session reads the counts. The click is the server's own, by its entry point with its gate
and its limits, on the browser of each zoom and scale. The same check is also run on clicks sent to
wrong points by a raw session and must refuse them there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from fixtures.browser_launcher import LaunchedBrowser
from fixtures.click_targets import NAMES, PAGE, Received, assert_landed
from fixtures.raw_cdp import RawSession
from test_actions_integration import Rig, mark_of, other_pages, pages, rig  # noqa: F401 - fixtures
from test_zoom_integration import BOX_SCRIPT, ZoomedScreen, screen  # noqa: F401 - fixture

from mcp_server_diwall_browser.actions import Actions
from mcp_server_diwall_browser.browser import Tab
from mcp_server_diwall_browser.output import ToolError
from mcp_server_diwall_browser.perception import Snapshot

pytestmark = pytest.mark.browser

# A box is read in CSS pixels to the hundredth: the browser states a fraction where its device pixels do not divide.
SLACK = 0.01
BODY_BOX_SCRIPT = "(() => { const b = document.body.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; })()"


@pytest.fixture(scope="module")
def launched(screen: ZoomedScreen) -> LaunchedBrowser:
    """The browser of the zoom and scale under test, in place of the one the imported rig starts on its own."""
    return screen.browser


@dataclass(frozen=True)
class Case:
    """A button to click and how the page is when it is observed."""

    target: str
    # Scrolled before the observation so that the button is in the middle of the view.
    scrolled: bool = False

    def __str__(self) -> str:
        return f"{self.target}-after-scroll" if self.scrolled else self.target


CASES = (Case("large"), Case("small"), Case("far"), Case("large", scrolled=True))

# What a clicker is: the click itself, with the error code it returned if it returned one.
Clicker = Callable[[Rig, Tab, Snapshot, RawSession, str], Awaitable[str | None]]


# --- what the run takes for granted, checked --------------------------------------------------------


async def assert_view(raw: RawSession, screen: ZoomedScreen) -> None:
    """The zoom and the screen scale asked for are those the page shows; a run at a zoom of 100 % proves nothing else."""
    ratio = await raw.evaluate("devicePixelRatio")
    zoom = (await raw.call("Page.getLayoutMetrics"))["cssVisualViewport"]["zoom"]
    assert abs(ratio - screen.zoom / 100 * screen.scale) < SLACK, (ratio, screen.zoom, screen.scale)
    assert abs(zoom - screen.zoom / 100) < SLACK, (zoom, screen.zoom)


async def scroll_to_the_middle(raw: RawSession, element_id: str) -> None:
    """Scroll so that the element is in the middle of the view, wait for the scroll to stop, check where it stopped."""
    _left, top, _right, bottom = await raw.evaluate(f"({BOX_SCRIPT})('{element_id}')")
    height, offset = await raw.evaluate("[innerHeight, scrollY]")
    await raw.evaluate(f"window.scrollTo(0, {(top + bottom) / 2 + offset - height / 2})")
    previous = None
    for _ in range(20):
        await asyncio.sleep(0.15)
        position = await raw.evaluate("window.scrollY")
        if position == previous:
            break
        previous = position
    else:
        raise AssertionError("the page keeps scrolling")
    assert position > 0, "the page is not scrolled: its offset does not enter the point of the click"
    _left, top, _right, bottom = await raw.evaluate(f"({BOX_SCRIPT})('{element_id}')")
    assert abs((top + bottom) / 2 - height / 2) <= 1, (top, bottom, height)


# --- the click, and what the page received ------------------------------------------------------


async def server_click(rig: Rig, tab: Tab, snapshot: Snapshot, raw: RawSession, target: str) -> str | None:
    """The click of the server, by its own entry point, on the mark of the button the observation names.

    What it returns is the code of the error the click gave, or the name of the exception it let out
    (the tool layer would report that one as an internal error): either is data for the check.
    """
    mark = mark_of(snapshot, NAMES[target])
    try:
        await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark)
    except ToolError as caught:
        return caught.code
    except Exception as caught:  # noqa: BLE001 - an escaped exception is a failed click, to be judged by the check
        return type(caught).__name__
    return None


def raw_click_at(place: Callable[[int, int], tuple[float, float]]) -> Clicker:
    """A click that is not the server's: the point of the box as the server reads it, moved by ``place``.

    It sends its events by a raw session, which the server's list of methods would refuse for some.
    """

    async def click(rig: Rig, tab: Tab, snapshot: Snapshot, raw: RawSession, target: str) -> str | None:
        backend = await raw.backend_id(f"#{target}")
        await raw.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend})
        quads = (await raw.call("DOM.getContentQuads", {"backendNodeId": backend}))["quads"][0]
        x, y = place(round(sum(quads[0::2]) / 4), round(sum(quads[1::2]) / 4))
        for kind, button, count in (("mouseMoved", "none", 0), ("mousePressed", "left", 1), ("mouseReleased", "left", 1)):
            await raw.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": button, "clickCount": count})
        return None

    return click


@dataclass(frozen=True)
class Outcome:
    received: Received
    # Where the page was scrolled to after the click, which may have scrolled it: read apart from the server.
    scroll: tuple[float, float]


async def attempt(rig: Rig, screen: ZoomedScreen, case: Case, clicker: Clicker) -> Outcome:
    """Open the page, observe it, click with ``clicker``, and read what the page received."""
    tab = await rig.registry.open_tab(rig.pages.url(PAGE))
    async with rig.raw(tab) as raw:
        await assert_view(raw, screen)
        if case.scrolled:
            await scroll_to_the_middle(raw, case.target)
        snapshot = await rig.observe(tab)
        error = await clicker(rig, tab, snapshot, raw, case.target)
        received = Received(await raw.evaluate("window.__clicks"), await raw.evaluate("window.__stray_clicks"), error)
        scroll_x, scroll_y = await raw.evaluate("[scrollX, scrollY]")
    return Outcome(received, (scroll_x, scroll_y))


# --- the page is what the checks assume -------------------------------------------------------------


async def test_the_target_page_is_laid_out_at_every_zoom_and_scale_as_the_click_checks_assume(screen: ZoomedScreen, rig: Rig) -> None:
    tab = await rig.registry.open_tab(rig.pages.url(PAGE))
    async with rig.raw(tab) as raw:
        await assert_view(raw, screen)
        boxes = {name: await raw.evaluate(f"({BOX_SCRIPT})('{name}')") for name in NAMES}
        width, height, offset = await raw.evaluate("[innerWidth, innerHeight, scrollY]")
        body = await raw.evaluate(BODY_BOX_SCRIPT)
    assert offset == 0
    assert body[3] - body[1] >= boxes["far"][3], f"the body is {body[3] - body[1]} high: it does not hold the far button"
    size = {name: (right - left, bottom - top) for name, (left, top, right, bottom) in boxes.items()}
    left, top, *_ = boxes["large"]
    assert size["large"][0] >= 80 - SLACK and size["large"][1] >= 32 - SLACK, size["large"]
    assert left > 300 and top > 300, (left, top)
    for name in ("decoy-left", "small", "decoy-right"):
        assert all(abs(side - 16) < SLACK for side in size[name]), (name, size[name])
        assert boxes[name][3] <= height and boxes[name][2] <= width, f"{name} is not in view"
    assert abs(boxes["small"][0] - boxes["decoy-left"][2] - 4) < SLACK, boxes
    assert abs(boxes["decoy-right"][0] - boxes["small"][2] - 4) < SLACK, boxes
    assert len({boxes[name][1] for name in ("decoy-left", "small", "decoy-right")}) == 1, boxes
    assert boxes["far"][1] >= 2 * height, f"the far button is {boxes['far'][1]} down, the view is {height} high"


# --- the click of the server -----------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=str)
async def test_a_click_of_the_server_lands_on_its_target_at_every_page_zoom_and_screen_scale(
    screen: ZoomedScreen, rig: Rig, case: Case
) -> None:
    outcome = await attempt(rig, screen, case, server_click)
    assert_landed(outcome.received, case.target)


# --- the same check on a server that asks for the node at the view point ---------------------------


async def no_scroll_offset(self: Actions, tab: Tab) -> tuple[float, float]:
    """The scroll offset read as none: the node lookup is asked at the view point, as the server once did."""
    return 0.0, 0.0


@pytest.mark.parametrize("case", CASES, ids=str)
async def test_the_click_check_fails_on_a_server_that_asks_for_the_node_at_the_view_point_of_a_scrolled_page(
    screen: ZoomedScreen, rig: Rig, case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Actions, "_viewport", no_scroll_offset)
    outcome = await attempt(rig, screen, case, server_click)
    if outcome.scroll == (0.0, 0.0):
        # The view point is the document point: the defect shows only where the page ends scrolled.
        assert_landed(outcome.received, case.target)
    else:
        with pytest.raises(AssertionError):
            assert_landed(outcome.received, case.target)
