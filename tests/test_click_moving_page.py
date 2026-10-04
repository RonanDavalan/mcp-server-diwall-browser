"""A click on a simulated page that moves when the pointer first moves, without a browser.

The first mouse event of a click is what makes some pages move. On the simulated browser, the page
here moves under the pointer at the first ``mouseMoved``: its target goes down and an intruder takes
its place, or the view scrolls. The server must then not press: no ``mousePressed``, no
``mouseReleased``, nothing clicked. Each check also runs on a server that does not check again after
the movement and must fail there. The same facts are checked against a real browser in
test_click_shifting_integration.py, which the suite without a browser does not run.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest
from fixtures.page_model import El, Page, button
from test_actions import Bench, bench, mark_of  # noqa: F401 - fixture

from mcp_server_diwall_browser.actions import Actions
from mcp_server_diwall_browser.browser import Tab
from mcp_server_diwall_browser.output import ToolError


def shifting_page() -> tuple[Page, Callable[[], None]]:
    """A target and a hidden intruder; the move sends the target 60 pixels down and shows the intruder where it was."""
    target = button("Go", (100, 100, 120, 30))
    intruder = button("Intruder", None)
    page = Page("Page", [target, intruder], content_size=(800, 1200))

    def move() -> None:
        target.box = (100, 160, 120, 30)
        intruder.box = (100, 100, 120, 30)

    return page, move


def scrolling_page() -> tuple[Page, Callable[[], None]]:
    """A target on a long page; the move scrolls the view by 60 pixels, under the pointer."""
    page = Page("Page", [button("Go", (100, 100, 120, 30))], content_size=(800, 1200))

    def move() -> None:
        page.viewport = (0, 60, 800, 600)

    return page, move


PAGES: dict[str, Callable[[], tuple[Page, Callable[[], None]]]] = {"shifts": shifting_page, "scrolls": scrolling_page}


def move_at_the_first_pointer_movement(bench: Bench, move: Callable[[], None]) -> None:
    """Make the simulated browser move its page when the first ``mouseMoved`` arrives."""
    previous = bench.cdp.handlers["Input.dispatchMouseEvent"]
    moved: list[bool] = []

    async def handler(params: dict[str, Any], session_id: str | None) -> Any:
        result = previous(params, session_id)
        if inspect.isawaitable(result):
            result = await result
        if params.get("type") == "mouseMoved" and not moved:
            moved.append(True)
            move()
        return result

    bench.cdp.on("Input.dispatchMouseEvent", handler=handler)


async def check_a_click_on_a_page_that_moves_is_refused_and_nothing_is_pressed(bench: Bench, name: str) -> None:
    page, move = PAGES[name]()
    tab, snapshot = await bench.show(page)
    move_at_the_first_pointer_movement(bench, move)
    try:
        await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    except ToolError as caught:
        assert caught.code == "element_obscured", caught.code
    else:
        raise AssertionError("the click went through although the page moved under the pointer")
    assert [params["type"] for _, params in bench.browser.mouse] == ["mouseMoved"], bench.browser.mouse
    assert bench.browser.clicks == []


@pytest.mark.parametrize("name", PAGES)
async def test_a_click_on_a_page_that_moves_at_the_first_pointer_movement_is_refused_and_nothing_is_pressed(bench: Bench, name: str) -> None:
    await check_a_click_on_a_page_that_moves_is_refused_and_nothing_is_pressed(bench, name)


async def no_recheck(self: Actions, tab: Tab, backend: int, x: int, y: int) -> None:
    """Defective: the press goes ahead on the answer given before the movement."""


@pytest.mark.parametrize("name", PAGES)
async def test_the_check_fails_on_a_server_that_does_not_check_again_after_the_pointer_moved(
    bench: Bench, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr(Actions, "_recheck_before_press", no_recheck)
    with pytest.raises(AssertionError):
        await check_a_click_on_a_page_that_moves_is_refused_and_nothing_is_pressed(bench, name)


async def test_a_click_on_a_page_that_does_not_move_presses_after_the_movement_and_looks_the_node_up_before_and_after(
    bench: Bench,
) -> None:
    page = Page("Page", [button("Go", (100, 100, 120, 30))])
    tab, snapshot = await bench.show(page)
    await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    assert [params["type"] for _, params in bench.browser.mouse] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert bench.cdp.methods().count("DOM.getNodeForLocation") == 2


async def test_an_element_with_no_box_after_the_movement_is_refused_and_nothing_is_pressed(bench: Bench) -> None:
    page, _ = shifting_page()
    tab, snapshot = await bench.show(page)
    target: El = page.body[0]
    move_at_the_first_pointer_movement(bench, lambda: setattr(target, "box", None))
    with pytest.raises(ToolError) as caught:
        await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    assert caught.value.code == "element_obscured"
    assert [params["type"] for _, params in bench.browser.mouse] == ["mouseMoved"]


def covering_page() -> tuple[Page, Callable[[], None]]:
    """A target and a hidden cover; the move shows the cover over the target, which keeps its box."""
    cover = button("Cover", None)
    page = Page("Page", [button("Go", (100, 100, 120, 30)), cover], content_size=(800, 1200))

    def move() -> None:
        cover.box = (90, 90, 140, 50)

    return page, move


async def check_a_target_covered_without_moving_is_refused_and_nothing_is_pressed(bench: Bench) -> None:
    page, move = covering_page()
    tab, snapshot = await bench.show(page)
    move_at_the_first_pointer_movement(bench, move)
    try:
        await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    except ToolError as caught:
        assert caught.code == "element_obscured", caught.code
    else:
        raise AssertionError("the click went through although something covered the target at the first movement")
    assert [params["type"] for _, params in bench.browser.mouse] == ["mouseMoved"], bench.browser.mouse
    assert bench.browser.clicks == []


async def test_a_target_covered_at_the_first_movement_without_moving_is_refused_and_nothing_is_pressed(bench: Bench) -> None:
    await check_a_target_covered_without_moving_is_refused_and_nothing_is_pressed(bench)


def ask_again_only_when_the_box_or_the_offset_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defective: the question is asked again only when the box or the offset read again differ from the first reading."""
    real_centre, real_viewport, real_require = Actions._centre, Actions._viewport, Actions._require_target_at
    first_centre: list[tuple[float, float]] = []
    first_viewport: list[tuple[float, float]] = []

    async def centre(self: Actions, tab: Tab, backend: int) -> tuple[float, float]:
        value = await real_centre(self, tab, backend)
        first_centre.append(value)
        return value

    async def viewport(self: Actions, tab: Tab, *, strict: bool = False) -> tuple[float, float]:
        value = await real_viewport(self, tab, strict=strict)
        first_viewport.append(value)
        return value

    async def conditional(self: Actions, tab: Tab, backend: int, x: int, y: int) -> None:
        if (await self._centre(tab, backend), await self._viewport(tab, strict=True)) != (first_centre[0], first_viewport[0]):
            scroll_x, scroll_y = first_viewport[-1]
            await real_require(self, tab, backend, round(x + scroll_x), round(y + scroll_y))

    monkeypatch.setattr(Actions, "_centre", centre)
    monkeypatch.setattr(Actions, "_viewport", viewport)
    monkeypatch.setattr(Actions, "_recheck_before_press", conditional)


async def test_the_check_fails_on_a_server_that_asks_again_only_when_the_box_or_the_offset_changed(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_again_only_when_the_box_or_the_offset_changed(monkeypatch)
    with pytest.raises(AssertionError):
        await check_a_target_covered_without_moving_is_refused_and_nothing_is_pressed(bench)


@pytest.mark.parametrize("name", PAGES)
async def test_the_server_that_asks_again_only_on_a_change_still_refuses_a_page_that_moves(
    bench: Bench, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The defective server above is the one of the first change: it holds where the page moves, and fails only on a cover."""
    ask_again_only_when_the_box_or_the_offset_changed(monkeypatch)
    await check_a_click_on_a_page_that_moves_is_refused_and_nothing_is_pressed(bench, name)
