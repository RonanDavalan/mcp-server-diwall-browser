"""Marks, masks and sizes checked against a real browser started at several screen scale factors.

The browser at a scale above 1 gives the snapshot and the image in device pixels and the viewport
in CSS pixels. The truth here is read apart from the server: the box of each element by
``DOM.getContentQuads`` and the size of the view by the page itself; what the server drew is read
back from the published image.
"""

from __future__ import annotations

import asyncio
import io
import math
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures.action_support import strict_admit
from fixtures.browser_launcher import SKIP_REASON, LaunchedBrowser, launch_browser
from fixtures.page_server import PageServer
from fixtures.raw_cdp import RawSession
from PIL import Image

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.browser import BrowserAttachment, TabRegistry
from mcp_server_diwall_browser.origin import parse_origin
from mcp_server_diwall_browser.perception import MARK_COLOUR, MASK_COLOUR, Perception

pytestmark = pytest.mark.browser

DEMO_DIR = Path(__file__).resolve().parent.parent / "docs" / "demo"
SCALES = (1.0, 1.5, 2.0)
# The same slack as for a human reading the image: two pixels of the image.
TOLERANCE = 2
SCROLL_Y = 500

Rect = tuple[float, float, float, float]


@dataclass
class Screen:
    scale: float
    browser: LaunchedBrowser


@pytest.fixture(scope="module", params=SCALES, ids=lambda scale: f"scale-{scale:g}")
def screen(request: pytest.FixtureRequest) -> Iterator[Screen]:
    browser = launch_browser(arguments=[f"--force-device-scale-factor={request.param}", "--window-size=1280,800"])
    if browser is None:
        pytest.skip(SKIP_REASON)
    yield Screen(request.param, browser)
    browser.stop()


@pytest.fixture(scope="module")
def demo() -> Iterator[PageServer]:
    with PageServer(DEMO_DIR) as server:
        yield server


@pytest.fixture(scope="module")
def pages() -> Iterator[PageServer]:
    with PageServer() as server:
        yield server


@pytest.fixture
async def registry(screen: Screen) -> AsyncIterator[TabRegistry]:
    browser = screen.browser
    profile = SimpleNamespace(browser=SimpleNamespace(family="chromium", profile_dir=browser.profile_dir, debug_port=browser.port))
    attached = await BrowserAttachment.attach(profile)
    tabs = TabRegistry(attached.connection)
    yield tabs
    for info in await tabs.list_tabs():
        await tabs.close_tab(info.tab_id)
    await attached.close()


def perceiver(server: PageServer, tabs: TabRegistry) -> Perception:
    allowed = [parse_origin(server.origin)]
    return Perception(tabs, admit=strict_admit(lambda: allowed))


# --- reading the truth apart from the server -----------------------------------------------------


async def quad_of(raw: RawSession, backend_node_id: int) -> Rect:
    """The box of an element in CSS pixels of the view, from the browser's own answer."""
    answer = await raw.call("DOM.getContentQuads", {"backendNodeId": backend_node_id})
    xs, ys = answer["quads"][0][0::2], answer["quads"][0][1::2]
    return min(xs), min(ys), max(xs), max(ys)


def edge_present(image: Image.Image, wanted: tuple[int, int, int], *, vertical: bool, lines: range, span: tuple[int, int]) -> bool:
    """A line of ``wanted`` pixels along ``span`` on one of ``lines`` (columns when ``vertical``)."""
    first, last = span
    if last <= first:
        return False
    for line in lines:
        if not 0 <= line < (image.width if vertical else image.height):
            continue
        count = sum(
            1
            for position in range(first, last)
            if 0 <= position < (image.height if vertical else image.width)
            and image.getpixel((line, position) if vertical else (position, line)) == wanted
        )
        if count >= 0.9 * (last - first):
            return True
    return False


def outline_matches(image: Image.Image, box: Rect) -> bool:
    """The outline of a mark is on ``box`` (in image pixels) within the tolerance, on its four sides."""
    x0, y0, x1, y1 = box
    # The middle half of each side: the number badge sits at a corner.
    across = (math.floor(x0 + (x1 - x0) / 4), math.ceil(x1 - (x1 - x0) / 4))
    down = (math.floor(y0 + (y1 - y0) / 4), math.ceil(y1 - (y1 - y0) / 4))
    sides = (
        (True, range(math.floor(x0) - 2 - TOLERANCE, math.ceil(x0) + TOLERANCE), down),
        (True, range(math.floor(x1) - TOLERANCE, math.ceil(x1) + 2 + TOLERANCE), down),
        (False, range(math.floor(y0) - 2 - TOLERANCE, math.ceil(y0) + TOLERANCE), across),
        (False, range(math.floor(y1) - TOLERANCE, math.ceil(y1) + 2 + TOLERANCE), across),
    )
    return all(edge_present(image, MARK_COLOUR[:3], vertical=vertical, lines=lines, span=span) for vertical, lines, span in sides)


def mask_group(image: Image.Image, window: tuple[int, int, int, int], seeds: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """The pixels of the mask colour inside ``window`` that are joined, side or corner, to ``seeds``."""
    pixels = image.load()
    assert pixels is not None
    left, top, right, bottom = window
    todo = [
        (x, y)
        for y in range(max(top, seeds[1]), min(bottom, seeds[3]))
        for x in range(max(left, seeds[0]), min(right, seeds[2]))
        if pixels[x, y] == MASK_COLOUR
    ]
    joined = set(todo)
    while todo:
        x, y = todo.pop()
        for near in ((x - 1, y - 1), (x, y - 1), (x + 1, y - 1), (x - 1, y), (x + 1, y), (x - 1, y + 1), (x, y + 1), (x + 1, y + 1)):
            if left <= near[0] < right and top <= near[1] < bottom and near not in joined and pixels[near[0], near[1]] == MASK_COLOUR:
                joined.add(near)
                todo.append(near)
    return list(joined)


def mask_found_apart(image: Image.Image, box: Rect) -> list[str]:
    """What is wrong with the mask over ``box`` (in image pixels); empty when it covers it and goes no further.

    The mask is the group of pixels of its colour that is joined to the inside of the box. A pixel of
    that colour which is not joined to it belongs to the page: the anti-aliasing of dark text gives
    exactly that colour by chance on some browsers, and counting it reads page text as a mask that goes
    beyond its field.
    """
    x0, y0, x1, y1 = box
    inside = (math.ceil(x0), math.ceil(y0), math.floor(x1), math.floor(y1))
    colours = set(image.crop(inside).getdata())
    found = [] if colours == {MASK_COLOUR} else [f"the box is not one colour: {len(colours)} colours"]
    margin = 12
    window = (max(0, math.floor(x0) - margin), max(0, math.floor(y0) - margin), min(image.width, math.ceil(x1) + margin), min(image.height, math.ceil(y1) + margin))
    dark = mask_group(image, window, inside)
    if dark:
        left, right = min(p[0] for p in dark), max(p[0] for p in dark) + 1
        top, bottom = min(p[1] for p in dark), max(p[1] for p in dark) + 1
        overflow = max(x0 - left, right - x1, y0 - top, bottom - y1)
        if overflow > TOLERANCE:
            found.append(f"the mask goes {overflow:.1f} pixels beyond the box")
    return found


async def audit(screen: Screen, tabs: TabRegistry, server: PageServer, page: str, *, scroll: bool = False) -> list[str]:
    """What an observation gets wrong about the page and the view it describes; empty when it is right."""
    tab = await tabs.open_tab(server.url(page))
    found: list[str] = []
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        if scroll:
            await raw.evaluate(f"window.scrollTo(0, {SCROLL_Y})")
            await asyncio.sleep(0.3)
        observation = await perceiver(server, tabs).observe(tab)
        assert observation.snapshot is not None and observation.image_png is not None
        view = await raw.evaluate(
            "[visualViewport.width, visualViewport.height, window.scrollX, window.scrollY, document.documentElement.scrollHeight]"
        )
        width, height, _scroll_x, scroll_y, document_height = view
        reported = observation.server["viewport"]
        for name, got, want, slack in (
            ("width", reported["width"], width, 1),
            ("height", reported["height"], height, 1),
            ("scroll_y", reported["scroll_y"], scroll_y, 1),
            ("content_height", reported["content_height"], document_height, 2),
        ):
            if abs(got - want) > slack:
                found.append(f"viewport.{name} is {got:.1f}, the page says {want:.1f}")
        image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
        ratio_x, ratio_y = image.width / width, image.height / height
        in_view = [e for e in observation.snapshot.elements if e.in_viewport]
        if not in_view:
            found.append("no element in view")
        for element in in_view:
            x0, y0, x1, y1 = await quad_of(raw, element.backend_node_id)
            # The box the server holds, in the document, against the browser's, in the view.
            held = (element.bounds.x, element.bounds.y - scroll_y, element.bounds.right, element.bounds.bottom - scroll_y)
            if any(abs(a - b) > TOLERANCE / max(ratio_x, 1.0) + 0.5 for a, b in zip(held, (x0, y0, x1, y1), strict=True)):
                found.append(f"box of mark {element.mark} ({element.name!r})")
            if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
                continue  # cut by the edge of the view: its outline is cut too
            wanted = (x0 * ratio_x, y0 * ratio_y, x1 * ratio_x, y1 * ratio_y)
            if element.sensitive:
                found += [f"mask of mark {element.mark}: {problem}" for problem in mask_found_apart(image, wanted)]
            if not outline_matches(image, wanted):
                found.append(f"outline of mark {element.mark} ({element.name!r})")
    return found


# --- the checks ----------------------------------------------------------------------------------


async def test_the_marks_and_the_mask_of_the_demo_page_are_where_the_browser_has_the_elements(
    screen: Screen, registry: TabRegistry, demo: PageServer
) -> None:
    assert await audit(screen, registry, demo, "index.html") == []


async def test_the_marks_of_a_scrolled_long_page_are_where_the_browser_has_the_elements(
    screen: Screen, registry: TabRegistry, pages: PageServer
) -> None:
    assert await audit(screen, registry, pages, "long.html", scroll=True) == []


async def test_the_password_field_of_the_demo_page_is_covered_by_its_mask_and_the_mask_goes_no_further(
    screen: Screen, registry: TabRegistry, demo: PageServer
) -> None:
    tab = await registry.open_tab(demo.url("index.html"))
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        observation = await perceiver(demo, registry).observe(tab)
        assert observation.snapshot is not None and observation.image_png is not None
        assert observation.server["capture"] == "ok" and observation.server["counters"]["redacted_fields"] == 1
        password = next(element for element in observation.snapshot.elements if element.sensitive)
        x0, y0, x1, y1 = await quad_of(raw, password.backend_node_id)
        width, height = await raw.evaluate("[visualViewport.width, visualViewport.height]")
    image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
    box = (x0 * image.width / width, y0 * image.height / height, x1 * image.width / width, y1 * image.height / height)
    assert mask_found_apart(image, box) == []


async def test_the_viewport_and_the_content_are_in_css_pixels(screen: Screen, registry: TabRegistry, pages: PageServer) -> None:
    tab = await registry.open_tab(pages.url("long.html"))
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        observation = await perceiver(pages, registry).observe(tab)
        width, height, document_height = await raw.evaluate(
            "[visualViewport.width, visualViewport.height, document.documentElement.scrollHeight]"
        )
    reported = observation.server["viewport"]
    assert abs(reported["width"] - width) <= 1 and abs(reported["height"] - height) <= 1
    assert abs(reported["content_height"] - document_height) <= 2, (reported, document_height)


# --- counter-proofs: the old reading, with the factor ignored ------------------------------------


@pytest.fixture
def factor_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "_device_scale", lambda metrics: 1.0)


async def test_audit_catches_a_perception_that_ignores_the_factor_on_the_demo_page(
    screen: Screen, registry: TabRegistry, demo: PageServer, factor_ignored: None
) -> None:
    found = await audit(screen, registry, demo, "index.html")
    if screen.scale == 1.0:
        assert found == []  # the old reading was right at a scale of 1: the check fails only where the defect is
    else:
        assert any(line.startswith("box of mark") for line in found), found
        assert any(line.startswith("viewport.content_height") for line in found), found
        assert any(line.startswith("outline of mark") for line in found), found


async def test_audit_catches_a_mask_that_misses_its_field_when_the_factor_is_ignored(
    screen: Screen, registry: TabRegistry, demo: PageServer, factor_ignored: None
) -> None:
    found = await audit(screen, registry, demo, "index.html")
    if screen.scale == 1.0:
        assert not any(line.startswith("mask of mark") for line in found)
    else:
        assert any(line.startswith("mask of mark") for line in found), found


async def test_audit_catches_a_scrolled_page_read_without_the_factor(
    screen: Screen, registry: TabRegistry, pages: PageServer, factor_ignored: None
) -> None:
    found = await audit(screen, registry, pages, "long.html", scroll=True)
    if screen.scale == 1.0:
        assert found == []
    else:
        assert any(line.startswith("viewport.content_height") for line in found), found
