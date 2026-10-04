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
    # Once before the pointer moves and once before it presses, at the same point of the document.
    assert asked == [CENTRES[name], CENTRES[name]], asked


@pytest.mark.parametrize("name", PAGES)
async def test_a_click_asks_the_node_lookup_twice_about_the_middle_of_the_box_in_the_document(bench: Bench, name: str) -> None:
    await check_the_click_lands_and_the_lookup_is_about_the_middle_of_the_box(bench, name)


async def no_scroll_offset(self: Actions, tab: Tab, *, strict: bool = False) -> tuple[float, float]:
    """The scroll offset read as none: the lookup is asked at the point of the view, as the server once did."""
    return 0.0, 0.0


REAL_VIEWPORT = Actions._viewport


async def vertical_scroll_offset_only(self: Actions, tab: Tab, *, strict: bool = False) -> tuple[float, float]:
    return 0.0, (await REAL_VIEWPORT(self, tab))[1]


OFFSETS: dict[str, Callable[..., Awaitable[tuple[float, float]]]] = {
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


# --- a browser that gives no usable scroll offset -------------------------------------------------------


def metrics_with_an_offset_that_is(page: Page, kind: str) -> None:
    """Make the simulated view metrics give no usable ``pageX`` and ``pageY``: missing, text, null or a boolean."""
    simulated = page.layout_metrics

    def layout_metrics() -> dict[str, Any]:
        metrics = simulated()
        view = metrics["cssVisualViewport"]
        for key in ("pageX", "pageY"):
            if kind == "missing":
                del view[key]
            else:
                view[key] = {"text": "0", "null": None, "boolean": True}[kind]
        return metrics

    page.layout_metrics = layout_metrics  # type: ignore[method-assign]


async def check_a_click_without_a_usable_offset_is_refused_and_nothing_is_clicked(bench: Bench, kind: str) -> None:
    page = PAGES["not-scrolled"]()
    tab, snapshot = await bench.show(page)
    metrics_with_an_offset_that_is(page, kind)
    try:
        await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    except ToolError as caught:
        assert caught.code == "element_obscured"
    else:
        raise AssertionError("the click went through although the browser gave no scroll offset")
    assert "DOM.getNodeForLocation" not in bench.cdp.methods()
    assert "Input.dispatchMouseEvent" not in bench.cdp.methods()


UNUSABLE = ("missing", "text", "null", "boolean")


@pytest.mark.parametrize("kind", UNUSABLE)
async def test_a_click_is_refused_when_the_browser_gives_no_usable_scroll_offset(bench: Bench, kind: str) -> None:
    await check_a_click_without_a_usable_offset_is_refused_and_nothing_is_clicked(bench, kind)


async def reads_a_missing_offset_as_none(self: Actions, tab: Tab, *, strict: bool = False) -> tuple[float, float]:
    """Defective: the strict reading is asked for and ignored, so what the browser does not say reads as 0."""
    return await REAL_VIEWPORT(self, tab)


# A null offset is not here: the lenient reading raises on it instead of reading 0, and nothing is clicked.
@pytest.mark.parametrize("kind", ("missing", "text", "boolean"))
async def test_the_check_fails_on_a_click_that_reads_an_offset_the_browser_does_not_give_as_none(
    bench: Bench, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(Actions, "_viewport", reads_a_missing_offset_as_none)
    with pytest.raises(AssertionError):
        await check_a_click_without_a_usable_offset_is_refused_and_nothing_is_clicked(bench, kind)


async def test_scrolling_to_the_bottom_still_reads_a_missing_offset_as_none(bench: Bench) -> None:
    """The strict reading is the click's: the scroll keeps its own, which only has to see whether the view moved."""
    page = PAGES["down"]()
    tab, _snapshot = await bench.show(page)
    metrics_with_an_offset_that_is(page, "missing")
    outcome = await bench.actions.scroll(tab.tab_id, to="bottom")
    assert outcome.server["action"] == "scroll"
