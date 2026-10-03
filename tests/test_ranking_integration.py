"""The choice of marks and tree lines on a dense page with a consent banner, in a real browser.

The page has 643 things to click: a menu of 96 links and a menu of 132 behind the banner, eight
filters behind it too, 400 results below the view, a sticky header, and a banner fixed over the
lower part of the view whose three buttons are the last elements of the document.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fixtures.browser_launcher import SKIP_REASON, LaunchedBrowser, launch_browser
from test_actions_integration import Rig, browser_home, mark_of, other_pages, pages, rig  # noqa: F401 - fixtures
from test_perception_ranking import old_keep

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.output import ToolError
from mcp_server_diwall_browser.perception import MAX_MARKS, MAX_TREE_LINES, OVERLAY_LINE, Observation

pytestmark = pytest.mark.browser

PAGE = "dense-banner.html"
BANNER_BUTTONS = ("Accept", "Refuse", "Customize")


@pytest.fixture(scope="module")
def launched(browser_home: Path) -> Iterator[LaunchedBrowser]:
    browser = launch_browser(home=str(browser_home), arguments=["--window-size=1280,800"])
    if browser is None:
        pytest.skip(SKIP_REASON)
    yield browser
    browser.stop()


async def observed(rig: Rig, *, scroll: int = 0) -> tuple[Any, Observation]:
    tab = await rig.registry.open_tab(rig.pages.url(PAGE))
    if scroll:
        async with rig.raw(tab) as raw:
            await raw.evaluate(f"window.scrollTo(0, {scroll})")
            await asyncio.sleep(0.3)
    observation = await rig.perception.observe(tab, image=False)
    assert observation.page_content is not None and observation.snapshot is not None
    return tab, observation


def element_names(observation: Observation) -> list[str]:
    assert observation.page_content is not None
    return [e["name"] for e in observation.page_content["elements"]]


def old_tree(shown: list[Any], depths: list[int], counters: dict[str, Any]) -> list[str]:
    """The tree before the layers: the view first, then document order, no overlay line."""
    lines = [(position, item) for position, item in enumerate(shown) if item.line is not None]
    kept, counters["tree_truncated"] = old_keep(lines, MAX_TREE_LINES, key=lambda pair: pair[0], rank=lambda pair: 0)
    return ["  " * depths[position] + (item.line or "") for position, item in kept]


# --- the banner can be answered ------------------------------------------------------------------


async def test_the_buttons_of_the_banner_are_marked_and_in_the_tree_on_a_dense_page(rig: Rig) -> None:
    _tab, observation = await observed(rig)
    assert observation.page_content is not None
    counters = observation.server["counters"]
    assert counters["marks"] == MAX_MARKS and counters["marks_truncated"] is True
    assert counters["tree_lines"] == MAX_TREE_LINES and counters["tree_truncated"] is True
    names = element_names(observation)
    assert {*BANNER_BUTTONS, "Privacy"} <= set(names)
    tree = observation.page_content["tree"]
    assert tree[0] == OVERLAY_LINE
    assert tree[1:6] == [
        '  StaticText "Cookies and advertising choices"',
        '  link "Privacy"',
        '  button "Accept"',
        '  button "Refuse"',
        '  button "Customize"',
    ]
    assert not any(line.startswith("overlay") for line in tree[6:])
    # Numbered from 1 in document order: the banner is the last of the document.
    marks = [e["mark"] for e in observation.page_content["elements"]]
    assert marks == list(range(1, MAX_MARKS + 1))
    assert names[-4:] == ["Privacy", "Accept", "Refuse", "Customize"]


async def test_ranking_check_catches_the_old_choice_on_the_dense_page(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_keep", old_keep)
    monkeypatch.setattr(perception, "_tree", old_tree)
    _tab, observation = await observed(rig)
    assert observation.page_content is not None
    assert not {*BANNER_BUTTONS, "Privacy"} & set(element_names(observation))
    assert not any(line.startswith("overlay") or "Refuse" in line for line in observation.page_content["tree"])
    assert observation.server["counters"]["marks_truncated"] is True


async def banner_goes_away_when_refused(rig: Rig) -> bool:
    tab, observation = await observed(rig)
    mark = mark_of(observation.snapshot, "Refuse")
    await rig.actions.click(tab.tab_id, observation.snapshot.snapshot_id, mark)
    after = await rig.perception.observe(tab, image=False)
    return not {*BANNER_BUTTONS} & {e["name"] for e in (after.page_content or {})["elements"]}


async def test_a_click_on_refuse_by_its_mark_takes_the_banner_away(rig: Rig) -> None:
    assert await banner_goes_away_when_refused(rig) is True


async def test_banner_check_catches_the_old_choice_that_left_no_mark_on_refuse(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_keep", old_keep)
    with pytest.raises(AssertionError, match="no mark named 'Refuse'"):
        await banner_goes_away_when_refused(rig)


async def test_once_the_banner_is_gone_nothing_is_flagged_covered(rig: Rig) -> None:
    tab, observation = await observed(rig)
    await rig.actions.click(tab.tab_id, observation.snapshot.snapshot_id, mark_of(observation.snapshot, "Refuse"))
    after = await rig.perception.observe(tab, image=False)
    assert after.page_content is not None
    assert after.server["counters"]["marks_truncated"] is True
    assert not any("covered" in e for e in after.page_content["elements"])


# --- what is behind the banner is said so --------------------------------------------------------


async def test_the_filters_behind_the_banner_are_flagged_covered_and_a_click_on_them_is_refused(rig: Rig) -> None:
    tab, observation = await observed(rig)
    assert observation.page_content is not None
    elements = {e["name"]: e for e in observation.page_content["elements"]}
    assert all(elements[f"Filter {n}"].get("covered") is True for n in range(1, 9))
    clear = [e for name, e in elements.items() if name.startswith("Department")]
    assert clear and not any("covered" in e for e in clear)
    assert not any("covered" in elements[name] for name in (*BANNER_BUTTONS, "Privacy", "Home", "Orders", "Help"))
    # The flag is true to the click: the browser puts the banner under the pointer.
    for number in (1, 4, 8):
        with pytest.raises(ToolError) as caught:
            await rig.actions.click(tab.tab_id, observation.snapshot.snapshot_id, mark_of(observation.snapshot, f"Filter {number}"))
        assert caught.value.code == "element_obscured"


async def test_covered_check_catches_a_perception_that_never_looks_under_the_banner(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_covers", lambda root, dom: [])
    _tab, observation = await observed(rig)
    assert observation.page_content is not None
    assert not any("covered" in e for e in observation.page_content["elements"])


# --- the header sticks once the page passes under it ---------------------------------------------


async def test_the_header_and_the_banner_are_both_layers_once_the_page_has_scrolled(rig: Rig) -> None:
    _tab, observation = await observed(rig, scroll=500)
    assert observation.page_content is not None
    names = element_names(observation)
    assert {"Home", "Orders", "Help", *BANNER_BUTTONS} <= set(names)
    tree = observation.page_content["tree"]
    assert tree[0] == OVERLAY_LINE
    head = tree[1:10]
    assert '    link "Home"' in head and '  button "Refuse"' in tree[1:12]


async def test_the_header_is_not_a_layer_before_anything_passes_under_it(rig: Rig) -> None:
    _tab, observation = await observed(rig)
    assert observation.page_content is not None
    tree = observation.page_content["tree"]
    # Only the banner is above the page: the header's links are in the tree after the overlay lines.
    assert tree[0] == OVERLAY_LINE
    assert 'link "Home"' not in "\n".join(tree[1:6])
    assert any('link "Home"' in line for line in tree[6:])


async def test_ranking_check_catches_the_old_choice_on_the_scrolled_page(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_keep", old_keep)
    monkeypatch.setattr(perception, "_tree", old_tree)
    _tab, observation = await observed(rig, scroll=500)
    assert observation.page_content is not None
    assert not {*BANNER_BUTTONS} & set(element_names(observation))
    assert not any(line.startswith("overlay") for line in observation.page_content["tree"])
