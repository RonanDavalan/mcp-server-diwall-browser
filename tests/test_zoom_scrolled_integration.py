"""The image of an observation of a scrolled page, at several page zooms and screen scales, on a real browser.

The cut of a screenshot is read in CSS pixels times the page zoom, scroll offset included. The truth
is read apart from the server: the boxes by the page itself, the colours of a block that nothing else
on the page shares by the image the server publishes. Each check is also run on a defective cut and
must fail there.
"""

from __future__ import annotations

import asyncio
import io
import math

import pytest
from fixtures.page_server import PageServer
from fixtures.raw_cdp import RawSession
from fixtures.zoomed_page import clip_with_the_offset_unscaled, clip_without_the_zoom
from PIL import Image
from test_scale_integration import Rect, demo, pages, perceiver, registry  # noqa: F401 - fixtures
from test_zoom_integration import BOX_SCRIPT, COVERED, TOLERANCE, ZoomedScreen, masked_share, screen  # noqa: F401 - fixtures

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.browser import TabRegistry
from mcp_server_diwall_browser.perception import MASK_COLOUR

pytestmark = pytest.mark.browser

PAGE = "scrolled-password.html"
LANDMARK_COLOUR = (10, 160, 10)
# Where the field is in the view once the page is scrolled: near the bottom edge, or in the middle.
PLACES = ("bottom", "middle")
# How far inside its field the edge of a mask may be, in pixels of the published image.
INWARD_SLACK = 1.0


def scroll_script(place: str) -> str:
    anchor = "document.getElementById('password').getBoundingClientRect()"
    if place == "bottom":
        return f"window.scrollTo(0, {anchor}.bottom + window.scrollY - window.innerHeight + 12)"
    return f"window.scrollTo(0, {anchor}.top + window.scrollY - window.innerHeight / 2)"


async def scrolled(raw: RawSession, place: str) -> None:
    await raw.evaluate(scroll_script(place))
    previous = None
    for _ in range(20):
        await asyncio.sleep(0.15)
        position = await raw.evaluate("window.scrollY")
        if position == previous:
            return
        previous = position
    raise AssertionError("the page keeps scrolling")


def colour_share(image: Image.Image, box: Rect, colour: tuple[int, int, int], margin: int = 4) -> float:
    """The share of the inside of ``box`` (image pixels) that is near ``colour``; the edges are left out.

    A box that is not in the image has none of its colour in it.
    """
    left, top = math.ceil(box[0]) + margin, math.ceil(box[1]) + margin
    right, bottom = math.floor(box[2]) - margin, math.floor(box[3]) - margin
    left, top, right, bottom = max(0, left), max(0, top), min(image.width, right), min(image.height, bottom)
    if right <= left or bottom <= top:
        return 0.0
    pixels = list(image.crop((left, top, right, bottom)).getdata())
    return sum(1 for pixel in pixels if all(abs(a - b) <= 3 for a, b in zip(pixel, colour, strict=True))) / len(pixels)


def mask_extent(image: Image.Image, box: Rect, margin: int = 12) -> tuple[int, int, int, int] | None:
    """The bounding box of the pixels of the mask colour that are joined to the inside of ``box``."""
    pixels = image.load()
    assert pixels is not None
    inside = (math.ceil(box[0]) + 2, math.ceil(box[1]) + 2, math.floor(box[2]) - 2, math.floor(box[3]) - 2)
    window = (max(0, math.floor(box[0]) - margin), max(0, math.floor(box[1]) - margin), min(image.width, math.ceil(box[2]) + margin), min(image.height, math.ceil(box[3]) + margin))
    todo = [
        (x, y)
        for y in range(max(window[1], inside[1]), min(window[3], inside[3]))
        for x in range(max(window[0], inside[0]), min(window[2], inside[2]))
        if pixels[x, y] == MASK_COLOUR
    ]
    joined = set(todo)
    while todo:
        x, y = todo.pop()
        for near in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if window[0] <= near[0] < window[2] and window[1] <= near[1] < window[3] and near not in joined and pixels[near[0], near[1]] == MASK_COLOUR:
                joined.add(near)
                todo.append(near)
    if not joined:
        return None
    return min(p[0] for p in joined), min(p[1] for p in joined), max(p[0] for p in joined) + 1, max(p[1] for p in joined) + 1


async def image_problems(screen: ZoomedScreen, registry: TabRegistry, pages: PageServer, place: str) -> list[str]:
    """What is wrong with the image of the scrolled page; empty when it follows the view and the mask covers the field."""
    tab = await registry.open_tab(pages.url(PAGE))
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        await scrolled(raw, place)
        observation = await perceiver(pages, registry).observe(tab)
        ratio = await raw.evaluate("devicePixelRatio")
        password = await raw.evaluate(f"({BOX_SCRIPT})('password')")
        landmark = await raw.evaluate(f"({BOX_SCRIPT})('landmark')")
    if observation.server["capture"] != "ok" or observation.image_png is None:
        return [f"the image is not published: capture is {observation.server['capture']}"]
    image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
    # The published image may be reduced; the page is painted at its box times the device pixels per CSS pixel.
    factor = ratio * observation.server["image"]["scale"]
    painted = tuple(value * factor for value in password)
    found = []
    if (share := masked_share(image, painted)) < COVERED:  # type: ignore[arg-type]
        found.append(f"the mask covers {share:.0%} of the password field")
    if (share := colour_share(image, tuple(value * factor for value in landmark), LANDMARK_COLOUR)) < COVERED:  # type: ignore[arg-type]
        found.append(f"the block the browser paints in view shows in {share:.0%} of its place")
    extent = mask_extent(image, painted)  # type: ignore[arg-type]
    if extent is None:
        found.append("no mask over the password field")
    else:
        outward = (painted[0] - extent[0], painted[1] - extent[1], extent[2] - painted[2], extent[3] - painted[3])
        if min(outward) < -INWARD_SLACK:
            found.append(f"the mask stops {-min(outward):.1f} pixels inside the field")
        if max(outward) > TOLERANCE:
            found.append(f"the mask goes {max(outward):.1f} pixels beyond the field")
    return found


# --- the image follows the view ------------------------------------------------------------------


@pytest.mark.parametrize("place", PLACES)
async def test_the_image_of_a_scrolled_page_follows_the_view_and_the_mask_covers_the_field(
    screen: ZoomedScreen, registry: TabRegistry, pages: PageServer, place: str
) -> None:
    assert await image_problems(screen, registry, pages, place) == []


async def test_the_image_of_the_demo_page_is_published_with_the_mask_over_the_field_at_every_page_zoom(
    screen: ZoomedScreen, registry: TabRegistry, demo: PageServer
) -> None:
    """The plain test of the mask passes when no image is published; this one asks for the image."""
    tab = await registry.open_tab(demo.url("index.html"))
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        observation = await perceiver(demo, registry).observe(tab)
        ratio = await raw.evaluate("devicePixelRatio")
        password = await raw.evaluate(f"({BOX_SCRIPT})('password')")
    assert observation.server["capture"] == "ok" and observation.image_png is not None
    image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
    factor = ratio * observation.server["image"]["scale"]
    assert masked_share(image, tuple(value * factor for value in password)) >= COVERED  # type: ignore[arg-type]


# --- counter-proofs: the same checks on a cut that has the defect --------------------------------


@pytest.mark.parametrize("place", PLACES)
async def test_the_image_check_catches_a_cut_whose_offset_is_not_multiplied_by_the_zoom(
    screen: ZoomedScreen, registry: TabRegistry, pages: PageServer, place: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perception, "_screenshot_clip", clip_with_the_offset_unscaled)
    found = await image_problems(screen, registry, pages, place)
    if screen.zoom == 100:
        assert found == []  # the cut is right at a zoom of 100 %: the check fails only where the defect is
    else:
        assert found and not found[0].startswith("the image is not published"), found


async def test_a_cut_made_without_the_zoom_is_withheld_on_a_real_browser(
    screen: ZoomedScreen, registry: TabRegistry, pages: PageServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perception, "_screenshot_clip", clip_without_the_zoom)
    found = await image_problems(screen, registry, pages, "bottom")
    if screen.zoom == 100:
        assert found == []
    else:
        assert found == ["the image is not published: capture is unavailable"], found


async def test_without_the_size_guard_a_cut_made_without_the_zoom_would_publish_a_misplaced_mask(
    screen: ZoomedScreen, registry: TabRegistry, pages: PageServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perception, "_screenshot_clip", clip_without_the_zoom)
    monkeypatch.setattr(perception, "_image_fits_view", lambda png, metrics: True)
    found = await image_problems(screen, registry, pages, "bottom")
    if screen.zoom == 100:
        assert found == []
    else:
        assert found and not found[0].startswith("the image is not published"), found


async def test_the_image_check_catches_a_page_zoom_read_as_one(
    screen: ZoomedScreen, registry: TabRegistry, pages: PageServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect the guard exists for: a missing zoom taken for 1 gives a cut the browser reads wrongly."""
    monkeypatch.setattr(perception, "_page_zoom", lambda metrics: 1.0)
    monkeypatch.setattr(perception, "_image_fits_view", lambda png, metrics: True)
    found = await image_problems(screen, registry, pages, "bottom")
    if screen.zoom == 100:
        assert found == []
    else:
        assert found and not found[0].startswith("the image is not published"), found
