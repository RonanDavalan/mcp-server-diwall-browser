"""A click on a target that its own pseudo-element covers, on a real browser.

The browser sends the click to the element a pseudo-element (``::before``, ``::after``) belongs to, so a pseudo-element of
the target or of one of its descendants is the target for the check of position. What really takes the click stays
refused: the pseudo-element of an ancestor, a sibling ``div``, a full-view ``div`` and a full-view frame that takes the
pointer. Each row is a page of ``tests/fixtures/pages``; the clicks received are read apart from the server.
"""

from __future__ import annotations

import pytest
from test_actions_integration import Rig, browser_home, launched, mark_of, other_pages, pages, rig  # noqa: F401 - fixtures

from mcp_server_diwall_browser.output import ToolError

pytestmark = pytest.mark.browser

REACHED = [
    "click-control.html",
    "click-pseudo-before.html",
    "click-pseudo-after.html",
    "click-pseudo-descendant.html",
    "click-pseudo-after-passive.html",
]

REFUSED = {
    "click-pseudo-ancestor.html": {"tag": None, "pseudo": "after"},
    "click-pseudo-sibling-after.html": {"tag": "div", "pseudo": None},
    "click-overlay-covering.html": {"tag": "div", "pseudo": None},
    "click-frame-covering.html": {"tag": "html", "pseudo": None},
}


async def attempt(rig: Rig, page: str) -> tuple[ToolError | None, int]:
    tab, snapshot = await rig.open(page)
    error = None
    try:
        await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Cible"))
    except ToolError as caught:
        error = caught
    async with rig.raw(tab) as raw:
        return error, await raw.evaluate("window.__clicks")


@pytest.mark.parametrize("page", REACHED)
async def test_a_click_reaches_a_target_covered_by_its_own_pseudo_element(rig: Rig, page: str) -> None:
    error, clicks = await attempt(rig, page)
    assert error is None, error.code
    assert clicks == 1


@pytest.mark.parametrize("page", sorted(REFUSED))
async def test_what_really_takes_the_click_stays_refused_and_receives_nothing(rig: Rig, page: str) -> None:
    error, clicks = await attempt(rig, page)
    assert error is not None and error.code == "element_obscured"
    assert clicks == 0


@pytest.mark.parametrize("page", sorted(REFUSED))
async def test_the_refusal_names_the_node_it_met_and_the_origin_without_any_text(rig: Rig, page: str) -> None:
    error, _ = await attempt(rig, page)
    assert error is not None
    assert error.extra_server["origin"] == rig.pages.origin
    assert error.extra_server["touched"] == {"tag": REFUSED[page]["tag"], "pseudo": REFUSED[page]["pseudo"]}
