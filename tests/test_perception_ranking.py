"""What the perception keeps when a page has more marks or tree lines than the caps, on a simulated browser.

A banner fixed over the page comes late in the document and covers part of the view. The answer to
it must reach the model, and the elements behind it must not be offered as if they could be clicked.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fixtures.page_model import El, Page, button, div, field_input, layer, link, text
from test_perception import built, names, perceiver, showing, fake, fake_browser, registry  # noqa: F401 - fixtures

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.browser import TabRegistry
from mcp_server_diwall_browser.perception import MAX_MARKS, MAX_TREE_LINES, OVERLAY_LINE, Box, Dom, build
from mcp_server_diwall_browser.origin import parse_origin

VIEWPORT = (0, 0, 800, 600)
BANNER = (0, 400, 800, 200)


def grid(prefix: str, count: int, *, left: float, top: float, columns: int, width: float = 66, height: float = 16, pitch_x: float = 70, pitch_y: float = 20) -> list[El]:
    return [
        link(f"{prefix} {n}", (left + (n % columns) * pitch_x, top + (n // columns) * pitch_y, width, height)) for n in range(count)
    ]


def banner(**kw: Any) -> El:
    return layer(
        text("Cookies and choices", (20, 410, 200, 16)),
        button("Accept", (20, 540, 100, 30)),
        button("Refuse", (140, 540, 100, 30)),
        button("Customize", (260, 540, 100, 30)),
        link("Privacy", (380, 546, 80, 18)),
        box=BANNER,
        **kw,
    )


def filters() -> list[El]:
    return [
        field_input(f"Filter {n}", (10, 410 + n * 22, 100, 18), attrs={"type": "checkbox"}, role="checkbox") for n in range(8)
    ]


def dense_page(**banner_kw: Any) -> Page:
    """60 links in view above the banner; 8 filters and 100 links behind it; 150 below the view; the banner last."""
    body = [
        *grid("Menu", 60, left=10, top=10, columns=10),
        *filters(),
        *grid("Deal", 100, left=120, top=410, columns=10, pitch_y=18),
        *grid("Item", 150, left=10, top=620, columns=10),
        banner(**banner_kw),
    ]
    return Page("Shop", body, viewport=VIEWPORT, content_size=(800, 900))


def crowded_view_page(**banner_kw: Any) -> Page:
    """450 tiny links across the view, the last rows behind the banner: more tree lines than the cap, all in view."""
    tiny = [link(f"Tile {n}", (4 + (n % 50) * 15, 4 + (n // 50) * 60, 12, 12)) for n in range(450)]
    return Page("Tiles", [*tiny, banner(**banner_kw)], viewport=VIEWPORT, content_size=(800, 600))


def old_keep(items: list[Any], limit: int, *, key: Callable[[Any], int], rank: Callable[[Any], int]) -> tuple[list[Any], bool]:
    """The ranking before the layers: those in the view first, then document order."""
    if len(items) <= limit:
        return items, False

    def in_viewport(item: Any) -> bool:
        return (item[1] if isinstance(item, tuple) else item).in_viewport

    first = [item for item in items if in_viewport(item)][:limit]
    rest = [item for item in items if not in_viewport(item)][: limit - len(first)]
    return sorted(first + rest, key=key), True


BANNER_NAMES = {"Accept", "Refuse", "Customize", "Privacy"}


def kept_names(page: Page) -> list[str]:
    return names(built(page))


# --- marks ---------------------------------------------------------------------------------------


def test_the_buttons_of_a_banner_fixed_over_the_page_are_marked_when_the_page_has_too_many_marks() -> None:
    result = built(dense_page())
    assert result.counters["marks_truncated"] is True and result.counters["marks"] == MAX_MARKS
    assert BANNER_NAMES <= set(names(result))
    # Numbered from 1 in document order: the banner is last in the document.
    assert [e.mark for e in result.elements] == list(range(1, MAX_MARKS + 1))
    assert [e.public["name"] for e in result.elements[-4:]] == ["Accept", "Refuse", "Customize", "Privacy"]


def test_ranking_check_catches_the_old_choice_that_dropped_the_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_keep", old_keep)
    kept = set(kept_names(dense_page()))
    assert not BANNER_NAMES & kept


def test_the_elements_behind_the_banner_say_so_and_the_others_do_not() -> None:
    result = built(dense_page())
    flagged = {e.public["name"] for e in result.elements if e.public.get("covered")}
    assert {f"Filter {n}" for n in range(8)} <= flagged
    assert all(name.startswith(("Filter", "Deal")) for name in flagged)
    clear = [e for e in result.elements if not e.public.get("covered")]
    assert {e.public["name"] for e in clear} >= BANNER_NAMES | {f"Menu {n}" for n in range(60)}
    # The key is there only when it is true.
    assert all(e.public["covered"] is True for e in result.elements if "covered" in e.public)


def test_covered_check_catches_a_perception_that_never_looks_under_the_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_covers", lambda root, dom: [])
    result = built(dense_page())
    assert not any(e.public.get("covered") for e in result.elements)


def test_the_marks_behind_the_banner_come_after_the_ones_in_the_clear() -> None:
    result = built(dense_page())
    in_the_clear = [e for e in result.elements if e.in_viewport and not e.public.get("covered") and e.public["name"] not in BANNER_NAMES]
    assert len(in_the_clear) == 60
    # 4 of the banner, 60 in the clear, and the rest of the 150 behind it: nothing from below the view.
    assert not any(e.public["name"].startswith("Item") for e in result.elements)
    assert sum(1 for e in result.elements if e.public.get("covered")) == MAX_MARKS - 4 - 60


# --- tree ----------------------------------------------------------------------------------------


def test_the_layer_comes_first_in_the_tree_under_an_overlay_line() -> None:
    result = built(crowded_view_page())
    assert result.counters["tree_truncated"] is True and len(result.tree_lines) == MAX_TREE_LINES
    assert result.tree_lines[0] == OVERLAY_LINE == 'overlay "layer above the page"'
    assert result.tree_lines[1:6] == [
        '  StaticText "Cookies and choices"',
        '  button "Accept"',
        '  button "Refuse"',
        '  button "Customize"',
        '  link "Privacy"',
    ]
    # The rest follows in document order.
    rest = result.tree_lines[6:]
    assert rest[0].startswith("RootWebArea") and rest[1] == '  link "Tile 0"'
    assert not any(line.startswith("overlay") for line in rest)


def test_tree_check_catches_the_old_choice_that_dropped_the_banner_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_keep", old_keep)
    lines = built(crowded_view_page()).tree_lines
    assert not any("Refuse" in line for line in lines)
    assert not any(line.startswith("overlay") for line in lines)


def test_without_a_layer_the_tree_keeps_the_view_first_and_has_no_overlay_line() -> None:
    page = Page("Plain", [*grid("Item", 500, left=0, top=0, columns=50, width=12, height=12, pitch_x=15, pitch_y=15)], viewport=VIEWPORT, content_size=(800, 900))
    lines = built(page).tree_lines
    assert len(lines) == MAX_TREE_LINES and not any(line.startswith("overlay") for line in lines)


# --- what is a layer -----------------------------------------------------------------------------


def with_filler(*extra: El, tiles: int = 450) -> Page:
    filler = [link(f"Tile {n}", (4 + (n % 50) * 15, 60 + (n // 50) * 15, 12, 12)) for n in range(tiles)]
    return Page("Layers", [*filler, *extra], viewport=VIEWPORT, content_size=(800, 600))


def has_overlay(page: Page) -> bool:
    return any(line.startswith("overlay") for line in built(page).tree_lines)


def test_a_sticky_header_is_a_layer_once_content_passes_under_it_and_not_before() -> None:
    header = layer(link("Home", (10, 4, 60, 18)), link("Orders", (80, 4, 60, 18)), box=(0, 0, 800, 40), position="sticky")
    assert not has_overlay(with_filler(header))  # the content starts below it
    passing = Page("Layers", [link("Under", (20, 10, 60, 18)), *with_filler().body, header], viewport=VIEWPORT, content_size=(800, 600))
    assert has_overlay(passing)


def test_a_dialog_is_a_layer_by_its_role() -> None:
    dialog = div(button("Save", (300, 300, 80, 24)), box=(250, 250, 300, 120), role="dialog", name="Settings")
    assert has_overlay(with_filler(dialog))


def test_a_modal_node_is_a_layer() -> None:
    modal = div(button("Save", (300, 300, 80, 24)), box=(250, 250, 300, 120), attrs={"aria-modal": "true"})
    assert has_overlay(with_filler(modal))


def test_a_dialog_out_of_the_view_is_not_a_layer() -> None:
    dialog = div(button("Save", (300, 1300, 80, 24)), box=(250, 1250, 300, 120), role="dialog", name="Settings")
    assert not has_overlay(with_filler(dialog))


def test_a_fixed_node_painted_behind_the_content_is_not_a_layer() -> None:
    background = layer(link("Logo", (10, 70, 60, 18)), box=(0, 0, 800, 600), attrs={"id": "background"})
    page = with_filler(background)
    dom = Dom(page.dom_snapshot())
    for node in dom.nodes:
        if node.attributes.get("id") == "background":
            node.paint_order = 0  # behind everything: z-index below the flow
    result = build(page.ax_tree(), dom, Box(*VIEWPORT), parse_origin("https://example.test"))
    assert not any(line.startswith("overlay") for line in result.tree_lines)


def test_a_wrapper_that_lets_the_pointer_through_covers_only_what_its_children_take() -> None:
    chat = layer(button("Chat", (700, 550, 80, 30)), box=(700, 550, 80, 30), pointer_events="auto")
    wrapper = layer(chat, box=(0, 0, 800, 600), pointer_events="none")
    # 100 in the clear, one under the button, and enough out of the view to pass the cap.
    page = Page(
        "Toast",
        [*grid("Item", 100, left=10, top=10, columns=10), *grid("Under", 1, left=710, top=558, columns=1), *grid("Far", 120, left=10, top=700, columns=10), wrapper],
        viewport=VIEWPORT,
        content_size=(800, 900),
    )
    result = built(page)
    assert result.counters["marks_truncated"] is True
    covered = {e.public["name"] for e in result.elements if e.public.get("covered")}
    assert covered == {"Under 0"}


def test_a_transparent_wrapper_that_takes_the_pointer_covers_the_whole_view() -> None:
    wrapper = layer(button("Close", (700, 550, 80, 30)), box=(0, 0, 800, 600), pointer_events="auto")
    page = Page(
        "Veil",
        [*grid("Item", 100, left=10, top=10, columns=10), *grid("Far", 120, left=10, top=700, columns=10), wrapper],
        viewport=VIEWPORT,
        content_size=(800, 900),
    )
    result = built(page)
    covered = [e for e in result.elements if e.public.get("covered")]
    assert covered and all(e.public["name"].startswith("Item") for e in covered)


# --- under the caps nothing changes --------------------------------------------------------------


def test_a_page_under_the_caps_gets_neither_covered_nor_an_overlay_line() -> None:
    page = Page("Small", [*grid("Item", 20, left=10, top=410, columns=10), banner()], viewport=VIEWPORT, content_size=(800, 600))
    result = built(page)
    assert result.counters["marks_truncated"] is False and result.counters["tree_truncated"] is False
    assert not any("covered" in e.public for e in result.elements)
    assert not any(line.startswith("overlay") for line in result.tree_lines)
    # Document order, as before: the banner is last.
    assert names(result)[-1] == "Privacy"


async def test_the_observation_carries_covered_and_the_overlay_line(fake_browser: Any, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, crowded_view_page())
    observation = await perceiver(registry).observe(tab)
    assert observation.page_content is not None
    assert observation.page_content["tree"][0] == OVERLAY_LINE
    assert observation.server["counters"]["tree_truncated"] is True
    tab = await showing(fake_browser, registry, dense_page())
    observation = await perceiver(registry).observe(tab)
    assert observation.page_content is not None
    covered = [e for e in observation.page_content["elements"] if "covered" in e]
    assert covered and all(e["covered"] is True for e in covered)


# --- the order of the choice itself --------------------------------------------------------------


def choice(rank: Callable[[Any], int]) -> list[str]:
    items = [
        SimpleNamespace(name="far", layer=False, in_viewport=False, covered=False),
        SimpleNamespace(name="under", layer=False, in_viewport=True, covered=True),
        SimpleNamespace(name="clear", layer=False, in_viewport=True, covered=False),
        SimpleNamespace(name="banner", layer=True, in_viewport=True, covered=False),
        SimpleNamespace(name="clear too", layer=False, in_viewport=True, covered=False),
    ]
    kept, truncated = perception._keep(items, 3, key=lambda item: items.index(item), rank=rank)
    assert truncated is True
    return [item.name for item in kept]


def test_the_choice_takes_the_layer_then_the_clear_view_then_what_is_covered_then_the_rest() -> None:
    assert choice(perception._rank) == ["clear", "banner", "clear too"]


def test_choice_check_catches_a_rank_that_puts_the_covered_with_the_clear() -> None:
    def blind_to_covers(item: Any) -> int:
        return 0 if item.layer else 1 if item.in_viewport else 3

    assert choice(blind_to_covers) != ["clear", "banner", "clear too"]
