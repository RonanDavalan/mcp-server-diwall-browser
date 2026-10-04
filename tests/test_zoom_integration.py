"""Marks, masks and sizes checked against a real browser whose page zoom is not 100 %.

Why this module exists:
    The browser's page zoom (the Ctrl + key) changes the size of a CSS pixel, as a screen scale
    does, but not in every place the same way. What the server reads and what it draws were
    measured at several zooms and screen scales; the checks here guard what was found.
"""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fixtures.browser_launcher import SKIP_REASON, launch_browser
from fixtures.raw_cdp import RawSession
from PIL import Image
from test_scale_integration import Rect, Screen, audit, demo, pages, perceiver, registry  # noqa: F401 - fixtures

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.browser import TabRegistry
from mcp_server_diwall_browser.perception import MASK_COLOUR

browser_test = pytest.mark.browser

ZOOMS = (100, 125, 150, 200)
SCREEN_SCALES = (1.0, 1.5)
# The browser keeps a zoom as a level, the power of 1.2 that gives the factor.
ZOOM_STEP = 1.2
# The same slack as for a human reading the image.
TOLERANCE = 2
COVERED = 0.99


@dataclass
class ZoomedScreen(Screen):
    zoom: int


def zoom_preferences(percent: int) -> dict[str, Any]:
    return {"partition": {"default_zoom_level": {"x": math.log(percent / 100) / math.log(ZOOM_STEP)}}}


@pytest.fixture(
    scope="module",
    params=[(zoom, scale) for scale in SCREEN_SCALES for zoom in ZOOMS],
    ids=lambda case: f"zoom-{case[0]}-scale-{case[1]:g}",
)
def screen(request: pytest.FixtureRequest) -> Iterator[ZoomedScreen]:
    zoom, scale = request.param
    launched = launch_browser(
        arguments=[f"--force-device-scale-factor={scale}", "--window-size=1280,800"], preferences=zoom_preferences(zoom)
    )
    if launched is None:
        pytest.skip(SKIP_REASON)
    yield ZoomedScreen(scale, launched, zoom)
    launched.stop()


# --- what is read apart from the server ----------------------------------------------------------


def masked_share(image: Image.Image, box: Rect) -> float:
    """The share of the part of ``box`` (in image pixels) that is in the image and is the mask colour.

    A box wholly outside the image is not published at all: nothing of it can show, so it counts as covered.
    """
    x0, y0, x1, y1 = (math.ceil(box[0]) + 2, math.ceil(box[1]) + 2, math.floor(box[2]) - 2, math.floor(box[3]) - 2)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(image.width, x1), min(image.height, y1)
    if x1 <= x0 or y1 <= y0:
        return 1.0
    pixels = list(image.crop((x0, y0, x1, y1)).getdata())
    return sum(1 for pixel in pixels if pixel == MASK_COLOUR) / len(pixels)


def first_ink(image: Image.Image, fixed: int, positions: range, *, vertical: bool) -> int | None:
    """The first position along a line of the image, in the order given, whose pixel is not white."""
    for position in positions:
        x, y = (fixed, position) if vertical else (position, fixed)
        if 0 <= x < image.width and 0 <= y < image.height and min(image.getpixel((x, y))[:3]) < 250:
            return position
    return None


async def painted_page(raw: RawSession) -> Image.Image:
    """The whole view as the browser paints it, in device pixels, without the server and without a mask."""
    shot = await raw.call("Page.captureScreenshot", {"format": "png"})
    return Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB")


BOX_SCRIPT = "(id => { const b = document.getElementById(id).getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; })"


# --- the zoom reaches the page -------------------------------------------------------------------


@browser_test
async def test_the_page_zoom_written_in_the_profile_reaches_the_page(screen: ZoomedScreen, registry: TabRegistry, demo: Any) -> None:
    tab = await registry.open_tab(demo.url("index.html"))
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        ratio = await raw.evaluate("devicePixelRatio")
        metrics = await raw.call("Page.getLayoutMetrics")
    assert abs(ratio - screen.zoom / 100 * screen.scale) < 0.01, ratio
    assert abs(metrics["cssVisualViewport"]["zoom"] - screen.zoom / 100) < 0.01, metrics["cssVisualViewport"]
    # The two descriptions of the view differ by the zoom and the screen scale together.
    measured = metrics["visualViewport"]["clientWidth"] / metrics["cssVisualViewport"]["clientWidth"]
    assert abs(measured - ratio) < 0.01, (measured, ratio)


@browser_test
async def test_the_browser_paints_an_element_at_its_box_times_the_device_pixels_per_css_pixel(
    screen: ZoomedScreen, registry: TabRegistry, demo: Any
) -> None:
    """The truth the mask check below relies on, read from the painted page and not from a box the browser states."""
    tab = await registry.open_tab(demo.url("index.html"))
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        ratio = await raw.evaluate("devicePixelRatio")
        left, top, right, bottom = await raw.evaluate(f"({BOX_SCRIPT})('user')")
        image = await painted_page(raw)
    middle_x, middle_y = round((left + right) / 2 * ratio), round((top + bottom) / 2 * ratio)
    found_top = first_ink(image, middle_x, range(math.floor(top * ratio) - 8, math.floor(top * ratio) + 9), vertical=True)
    found_bottom = first_ink(image, middle_x, range(math.ceil(bottom * ratio) + 8, math.ceil(bottom * ratio) - 9, -1), vertical=True)
    found_right = first_ink(image, middle_y, range(math.ceil(right * ratio) + 8, math.ceil(right * ratio) - 9, -1), vertical=False)
    assert found_top is not None and found_bottom is not None and found_right is not None, (found_top, found_bottom, found_right)
    assert abs(found_top - top * ratio) <= TOLERANCE, (found_top, top * ratio)
    assert abs(found_bottom + 1 - bottom * ratio) <= TOLERANCE, (found_bottom, bottom * ratio)
    assert abs(found_right + 1 - right * ratio) <= TOLERANCE, (found_right, right * ratio)


# --- what holds: sizes and boxes are in CSS pixels at any zoom -----------------------------------


async def boxes_and_sizes_wrong(screen: ZoomedScreen, registry: TabRegistry, demo: Any, pages: Any) -> list[str]:
    """What an observation gets wrong about sizes and boxes in CSS pixels, on a page and on a scrolled long one.

    The outlines and masks of the same audit are left out: they are compared with the server's own
    placement, not with where the browser paints the element, which the mask check below does.
    """
    wrong: list[str] = []
    for server, page, scroll in ((demo, "index.html", False), (pages, "long.html", True)):
        found = await audit(screen, registry, server, page, scroll=scroll)
        wrong += [f"{page}: {line}" for line in found if line.startswith(("viewport.", "box of mark"))]
    return wrong


@browser_test
async def test_the_viewport_and_the_element_boxes_are_in_css_pixels_at_any_page_zoom(
    screen: ZoomedScreen, registry: TabRegistry, demo: Any, pages: Any
) -> None:
    assert await boxes_and_sizes_wrong(screen, registry, demo, pages) == []


@pytest.fixture
def zoom_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The factor read as the screen alone: the zoom the browser also reports is left out."""
    original = perception._device_scale
    monkeypatch.setattr(perception, "_device_scale", lambda metrics: original(metrics) / metrics["cssVisualViewport"].get("zoom", 1))


@browser_test
async def test_the_audit_catches_boxes_read_without_the_zoom(
    screen: ZoomedScreen, registry: TabRegistry, demo: Any, pages: Any, zoom_ignored: None
) -> None:
    found = await boxes_and_sizes_wrong(screen, registry, demo, pages)
    if screen.zoom == 100:
        assert found == []  # the reading is right at a zoom of 100 %: the check fails only where the defect is
    else:
        assert any("box of mark" in line for line in found), found
        assert any("viewport.content_height" in line for line in found), found


# --- the mask over the image ---------------------------------------------------------------------


@browser_test
async def test_the_mask_covers_the_password_field_where_the_browser_paints_it(
    screen: ZoomedScreen, registry: TabRegistry, demo: Any
) -> None:
    tab = await registry.open_tab(demo.url("index.html"))
    async with RawSession(screen.browser.version_info["webSocketDebuggerUrl"], tab.target_id) as raw:
        observation = await perceiver(demo, registry).observe(tab)
        ratio = await raw.evaluate("devicePixelRatio")
        left, top, right, bottom = await raw.evaluate(f"({BOX_SCRIPT})('password')")
    if observation.image_png is None:
        return  # no image is published: failing closed shows nothing of the field
    image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
    # The published image may be reduced; the field is painted at its box times the device pixels per CSS pixel.
    factor = ratio * observation.server["image"]["scale"]
    share = masked_share(image, (left * factor, top * factor, right * factor, bottom * factor))
    assert share >= COVERED, f"the mask covers {share:.0%} of the field"


# --- counter-proofs of the mask check itself, without a browser ----------------------------------


Check = Callable[[Image.Image, Rect], float]
FIELD: Rect = (120.0, 80.0, 320.0, 110.0)


def masked_field(mask: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new("RGB", (400, 200), (255, 255, 255))
    image.paste((0, 0, 0), (int(FIELD[0]), int(FIELD[1]), int(FIELD[2]), int(FIELD[3])))  # the field, painted
    image.paste(MASK_COLOUR, mask)
    return image


def any_mask_in_the_image(image: Image.Image, box: Rect) -> float:
    """A check that is satisfied by a mask anywhere in the image, not over the field."""
    return 1.0 if MASK_COLOUR in {pixel for pixel in image.getdata()} else 0.0


def check_problems(check: Check) -> list[str]:
    wrong = []
    if check(masked_field((120, 80, 320, 110)), FIELD) < COVERED:
        wrong.append("it does not see a field that its mask covers")
    if check(masked_field((20, 150, 220, 180)), FIELD) >= COVERED:
        wrong.append("it takes a mask placed beside the field for a mask over it")
    if not 0.2 < check(masked_field((120, 80, 220, 110)), FIELD) < 0.8:
        wrong.append("it does not see that the mask covers half of the field")
    return wrong


def test_the_mask_check_sees_a_covered_field_a_displaced_mask_and_half_a_mask() -> None:
    assert check_problems(masked_share) == []


def test_a_field_outside_the_published_image_counts_as_covered() -> None:
    assert masked_share(masked_field((120, 80, 320, 110)), (500.0, 80.0, 700.0, 110.0)) == 1.0


def test_the_mask_check_that_takes_a_mask_anywhere_for_a_cover_fails_on_a_displaced_mask() -> None:
    assert check_problems(any_mask_in_the_image) == [
        "it takes a mask placed beside the field for a mask over it",
        "it does not see that the mask covers half of the field",
    ]
