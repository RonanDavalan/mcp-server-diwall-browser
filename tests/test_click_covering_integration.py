"""A click of the server on a target that gets covered at the first pointer movement, on a real browser.

The page shows a button over the target when the pointer first moves; the target keeps its box and
the view keeps its offset. What each button received is read apart from the server. The server must
refuse the click as covered and click nothing; a server that checks only before the movement, or
only when the box or the offset changed, lets the cover receive the click.
"""

from __future__ import annotations

import pytest
from test_actions_integration import Rig, browser_home, launched, mark_of, other_pages, pages, rig  # noqa: F401 - fixtures
from test_click_moving_page import ask_again_only_when_the_box_or_the_offset_changed

from mcp_server_diwall_browser.actions import Actions
from mcp_server_diwall_browser.browser import Tab
from mcp_server_diwall_browser.output import ToolError

pytestmark = pytest.mark.browser

PAGE = "click-covering.html"


async def attempt(rig: Rig) -> tuple[str | None, dict[str, int], bool]:
    """Click the target by the server's entry point; return the error code, the clicks received and whether the cover showed."""
    tab, snapshot = await rig.open(PAGE)
    error = None
    async with rig.raw(tab) as raw:
        try:
            await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Covered target"))
        except ToolError as caught:
            error = caught.code
        covered = await raw.evaluate("getComputedStyle(document.getElementById('cover')).display") == "block"
        return error, await raw.evaluate("window.__clicks"), covered


async def test_the_server_refuses_a_click_on_a_target_covered_at_the_first_movement_and_clicks_nothing(rig: Rig) -> None:
    error, clicks, covered = await attempt(rig)
    assert covered, "the page did not cover its target: the attempt proves nothing"
    assert error == "element_obscured", error
    assert clicks == {"target": 0, "cover": 0}, clicks


async def no_recheck(self: Actions, tab: Tab, backend: int, x: int, y: int) -> None:
    """Defective: the press goes to the point the first check was made for, whatever the movement did."""


async def test_the_covering_check_fails_on_a_server_that_checks_only_before_the_movement(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Actions, "_recheck_before_press", no_recheck)
    error, clicks, covered = await attempt(rig)
    assert covered
    assert error is None and clicks == {"target": 0, "cover": 1}, (error, clicks)


async def test_the_covering_check_fails_on_a_server_that_asks_again_only_when_the_box_or_the_offset_changed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_again_only_when_the_box_or_the_offset_changed(monkeypatch)
    error, clicks, covered = await attempt(rig)
    assert covered
    assert error is None and clicks == {"target": 0, "cover": 1}, (error, clicks)
