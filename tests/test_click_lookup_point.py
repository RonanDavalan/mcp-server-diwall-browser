"""The point a click asks the node lookup about, on a simulated browser that answers as the real one was measured to.

The browser answers the node lookup for a point of the document, while the box of an element and the
mouse events are in the view. On a page that is not scrolled the two are the same; on a scrolled page
the click must add the scroll offset to ask about the right place. The simulated browser of the other
action tests reads the point as one of the view, so its lookup is wrapped here to read a point of the
document. Each check is also run on a click that leaves the offset out and must fail there. The same
facts are checked against a real browser in test_click_integration.py, which the suite without a
browser does not run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fixtures.page_model import Page, button
from test_actions import Bench, bench, mark_of  # noqa: F401 - fixture

from mcp_server_diwall_browser.actions import Actions
from mcp_server_diwall_browser.browser import Tab
from mcp_server_diwall_browser.output import ToolError

# A button of 120 by 30 on four pages: not scrolled, scrolled down (it needs the scroll the click makes),
# scrolled across, and scrolled in both directions. The scroll offset is where the page starts being shown.
PAGES: dict[str, Callable[[], Page]] = {
    "not-scrolled": lambda: Page("Page", [button("Go", (100, 100, 120, 30))]),
    "down": lambda: Page("Page", [button("Go", (20, 900, 120, 30))], content_size=(800, 2000)),
    "across": lambda: Page("Page", [button("Go", (300, 100, 120, 30))], viewport=(200, 0, 800, 600), content_size=(1600, 600)),
    "both": lambda: Page("Page", [button("Go", (300, 400, 120, 30))], viewport=(200, 100, 800, 600), content_size=(1600, 1200)),
}
# The middle of the box in the document, which is the point the lookup has to be asked about.
CENTRES = {"not-scrolled": (160, 115), "down": (80, 915), "across": (360, 115), "both": (360, 415)}


def lookup_for_a_point_of_the_document(bench: Bench, page: Page) -> None:
    """Make the simulated node lookup answer for a point of the document, as the real browser was measured to."""
    simulated = bench.cdp.handlers["DOM.getNodeForLocation"]

    def handler(params: dict[str, Any], session_id: str | None) -> Any:
        # The simulated lookup adds the scroll offset to the point it is given: take it off first.
        scroll_x, scroll_y = page.viewport[0], page.viewport[1]
        return simulated({**params, "x": params["x"] - scroll_x, "y": params["y"] - scroll_y}, session_id)

    bench.cdp.on("DOM.getNodeForLocation", handler=handler)


async def check_the_click_lands_and_the_lookup_is_about_the_middle_of_the_box(bench: Bench, name: str) -> None:
    page = PAGES[name]()
    tab, snapshot = await bench.show(page)
    lookup_for_a_point_of_the_document(bench, page)
    try:
        await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    except ToolError as caught:
        raise AssertionError(f"the click was refused: {caught.code}") from None
    assert [hit for _, hit in bench.browser.clicks] == [page.body[0].backend_id]
    asked = [(message["params"]["x"], message["params"]["y"]) for message in bench.cdp.received if message.get("method") == "DOM.getNodeForLocation"]
    assert asked == [CENTRES[name]], asked


@pytest.mark.parametrize("name", PAGES)
async def test_a_click_asks_the_node_lookup_about_the_middle_of_the_box_in_the_document(bench: Bench, name: str) -> None:
    await check_the_click_lands_and_the_lookup_is_about_the_middle_of_the_box(bench, name)


async def no_scroll_offset(self: Actions, tab: Tab) -> tuple[float, float]:
    """The scroll offset read as none: the lookup is asked at the point of the view, as the server once did."""
    return 0.0, 0.0


REAL_VIEWPORT = Actions._viewport


async def vertical_scroll_offset_only(self: Actions, tab: Tab) -> tuple[float, float]:
    return 0.0, (await REAL_VIEWPORT(self, tab))[1]


OFFSETS: dict[str, Callable[[Actions, Tab], Awaitable[tuple[float, float]]]] = {
    "none": no_scroll_offset,
    "vertical-only": vertical_scroll_offset_only,
}
# Where each defective offset is wrong: a page scrolled in a direction it leaves out.
WRONG_ON = {"none": ("down", "across", "both"), "vertical-only": ("across", "both")}


@pytest.mark.parametrize(("offset", "name"), [(offset, name) for offset, names in WRONG_ON.items() for name in names])
async def test_the_check_fails_on_a_click_that_leaves_out_a_part_of_the_scroll_offset(
    bench: Bench, monkeypatch: pytest.MonkeyPatch, offset: str, name: str
) -> None:
    monkeypatch.setattr(Actions, "_viewport", OFFSETS[offset])
    with pytest.raises(AssertionError):
        await check_the_click_lands_and_the_lookup_is_about_the_middle_of_the_box(bench, name)


@pytest.mark.parametrize(("offset", "name"), [("none", "not-scrolled"), ("vertical-only", "not-scrolled"), ("vertical-only", "down")])
async def test_the_defect_shows_only_where_the_page_is_scrolled_in_a_direction_the_click_leaves_out(
    bench: Bench, monkeypatch: pytest.MonkeyPatch, offset: str, name: str
) -> None:
    monkeypatch.setattr(Actions, "_viewport", OFFSETS[offset])
    await check_the_click_lands_and_the_lookup_is_about_the_middle_of_the_box(bench, name)
