"""Boxes, masks and sizes of an observation at several screen scale factors, on a simulated browser.

A browser at a scale factor above 1 gives the snapshot and the image in device pixels and the
viewport in CSS pixels. A mask laid with the snapshot's numbers read as CSS pixels would miss its
field, and the image would be published as if it were masked.
"""

from __future__ import annotations

import ast
import io
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_cdp import FakeBrowser, FakeCdp  # noqa: F401 - FakeCdp is the type of a fixture
from fixtures.page_model import Page
from PIL import Image
from test_perception import (  # noqa: F401 - fakes and fixtures
    URL,
    fake,
    fake_browser,
    login_page,
    perceiver,
    registry,
    showing,
)

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.browser import TabRegistry
from mcp_server_diwall_browser.perception import MARK_COLOUR, MASK_COLOUR, Box, Dom, SnapshotFormatError

SCALES = (1.0, 1.5, 2.0)
# The boxes of login_page, in CSS pixels.
USERNAME, PASSWORD, CARD = Box(10, 10, 200, 24), Box(10, 44, 200, 24), Box(10, 78, 200, 24)
TOLERANCE = 2


def page_at(scale: float) -> Page:
    return login_page(device_scale=scale, viewport=(0, 0, 800, 600), content_size=(800, 900))


def close(a: float, b: float, tolerance: float = 0.01) -> bool:
    return math.isclose(a, b, abs_tol=tolerance)


def pixels(png: bytes, box: Box, ratio: float) -> set[tuple[int, int, int]]:
    """The colours of the image under a box given in CSS pixels, ``ratio`` image pixels per CSS pixel."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    crop = (round(box.x * ratio), round(box.y * ratio), round(box.right * ratio), round(box.bottom * ratio))
    return set(image.crop(crop).getdata())


async def discrepancies(fake_browser: FakeBrowser, registry: TabRegistry, scale: float) -> list[str]:
    """What an observation gets wrong about the page it describes; empty when it is right."""
    page = page_at(scale)
    tab = await showing(fake_browser, registry, page)
    observation = await perceiver(registry).observe(tab)
    assert observation.snapshot is not None and observation.image_png is not None
    found: list[str] = []
    expected = {"Username": USERNAME, "Password": PASSWORD, "Card number": CARD}
    for element in observation.snapshot.elements:
        box = expected.get(element.name)
        if box is not None and not all(
            close(got, want) for got, want in zip((element.bounds.x, element.bounds.y, element.bounds.width, element.bounds.height), (box.x, box.y, box.width, box.height), strict=True)
        ):
            found.append(f"box of {element.name}")
    viewport = observation.server["viewport"]
    if not close(viewport["content_height"], 900):
        found.append("content_height")
    # Every pixel of a sensitive field is under its mask, and the mask goes no further than the tolerance.
    image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
    ratio = image.width / viewport["width"]
    for name, box in (("Password", PASSWORD), ("Card number", CARD)):
        if pixels(observation.image_png, box, ratio) != {MASK_COLOUR}:
            found.append(f"mask of {name}")
        around = Box(box.x - TOLERANCE - 1, box.y - TOLERANCE - 1, box.width + 2 * (TOLERANCE + 1), box.height + 2 * (TOLERANCE + 1))
        outside = pixels(observation.image_png, around, ratio) - {MASK_COLOUR}
        if not outside:
            found.append(f"mask of {name} is larger than its box")
    # The plain field keeps its text.
    if len(pixels(observation.image_png, USERNAME, ratio)) < 2:
        found.append("plain field")
    # The mark of the username field is drawn on its box, not on a box scaled again.
    top_edge = [image.getpixel((round((USERNAME.x + USERNAME.width / 2) * ratio), y)) for y in range(round(USERNAME.y * ratio) - 4, round(USERNAME.y * ratio))]
    if MARK_COLOUR[:3] not in top_edge:
        found.append("mark of Username")
    return found


@pytest.mark.parametrize("scale", SCALES)
async def test_boxes_masks_and_sizes_are_in_css_pixels_at_every_scale(
    fake_browser: FakeBrowser, registry: TabRegistry, scale: float
) -> None:
    assert await discrepancies(fake_browser, registry, scale) == []


@pytest.mark.parametrize("scale", [1.5, 2.0])
async def test_scale_check_catches_a_perception_that_ignores_the_factor(
    fake_browser: FakeBrowser, registry: TabRegistry, scale: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perception, "_device_scale", lambda metrics: 1.0)
    found = await discrepancies(fake_browser, registry, scale)
    assert "box of Password" in found and "content_height" in found


async def test_scale_check_leaves_a_perception_that_ignores_the_factor_alone_at_scale_one(
    fake_browser: FakeBrowser, registry: TabRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The old reading was right at a scale of 1: the check only fails where the defect is.
    monkeypatch.setattr(perception, "_device_scale", lambda metrics: 1.0)
    assert await discrepancies(fake_browser, registry, 1.0) == []


@pytest.mark.parametrize("scale", SCALES)
async def test_the_viewport_is_in_css_pixels_at_every_scale(fake_browser: FakeBrowser, registry: TabRegistry, scale: float) -> None:
    tab = await showing(fake_browser, registry, page_at(scale))
    observation = await perceiver(registry).observe(tab)
    assert observation.server["viewport"] == {"width": 800, "height": 600, "scroll_x": 0, "scroll_y": 0, "content_height": 900}


@pytest.mark.parametrize("scale", SCALES)
async def test_the_elements_in_view_are_those_of_the_css_viewport_at_every_scale(
    fake_browser: FakeBrowser, registry: TabRegistry, scale: float
) -> None:
    page = login_page(device_scale=scale, viewport=(0, 0, 800, 70), content_size=(800, 600))
    tab = await showing(fake_browser, registry, page)
    observation = await perceiver(registry).observe(tab)
    assert observation.page_content is not None
    assert {e["name"]: e["in_viewport"] for e in observation.page_content["elements"]} == {
        "Username": True,
        "Password": True,
        "Card number": False,
        "Sign in": False,
    }


# --- the factor itself --------------------------------------------------------------------------


def metrics(device_width: Any, css_width: Any) -> dict[str, Any]:
    return {"visualViewport": {"clientWidth": device_width}, "cssVisualViewport": {"clientWidth": css_width}}


@pytest.mark.parametrize(
    ("device", "css", "expected"),
    [
        (1280, 1280, 1.0),
        (1920, 1280, 1.5),
        (2560, 1280, 2.0),
        # A pinch zoom divides both widths by the same number.
        (960, 640, 1.5),
        # The fractional width measured on a screen at 1.5, as the browser writes it.
        (2765, 1843.3333740234375, 1.5),
    ],
)
def test_the_factor_is_the_ratio_of_the_two_viewport_widths(device: float, css: float, expected: float) -> None:
    assert perception._device_scale(metrics(device, css)) == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize(
    "unreadable",
    [
        {},
        {"cssVisualViewport": {"clientWidth": 1280}},
        {"visualViewport": {"clientWidth": 1920}},
        metrics(0, 1280),
        metrics(1920, 0),
        metrics(-1920, 1280),
        metrics(float("nan"), 1280),
        metrics(float("inf"), 1280),
        metrics("wide", 1280),
        metrics(None, 1280),
    ],
)
def test_a_factor_that_cannot_be_read_is_refused_not_read_as_one(unreadable: dict[str, Any]) -> None:
    with pytest.raises(SnapshotFormatError):
        perception._device_scale(unreadable)


async def test_a_browser_that_gives_no_device_viewport_is_refused(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Page.layout_metrics

    def without_device_viewport(self: Page) -> dict[str, Any]:
        answer = real(self)
        del answer["visualViewport"]
        return answer

    monkeypatch.setattr(Page, "layout_metrics", without_device_viewport)
    tab = await showing(fake_browser, registry, page_at(1.5))
    with pytest.raises(SnapshotFormatError):
        await perceiver(registry).observe(tab)


@pytest.mark.parametrize("scale", [0.0, -1.0, 0.05, 25.0, float("nan"), float("inf")])
def test_a_snapshot_is_not_read_at_a_factor_outside_the_range(scale: float) -> None:
    with pytest.raises(SnapshotFormatError):
        Dom(page_at(1.0).dom_snapshot(), scale)


def test_the_snapshot_boxes_are_divided_by_the_factor() -> None:
    raw = page_at(2.0).dom_snapshot()
    unscaled = Dom(raw)
    scaled = Dom(raw, 2.0)
    assert unscaled.content_height == 1800 and scaled.content_height == 900
    password = next(node for node in scaled.nodes if node.attributes.get("type") == "password")
    twin = unscaled.nodes[password.index]
    assert (twin.box.x, twin.box.width) == (20, 400)  # type: ignore[union-attr]
    assert (password.box.x, password.box.width) == (10, 200)  # type: ignore[union-attr]


# --- every snapshot the perception reads says its factor ----------------------------------------


PERCEPTION_SOURCE = Path(perception.__file__)


def dom_reads_without_a_factor(source: str) -> list[int]:
    """Lines where a ``Dom`` is built with the snapshot alone."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Dom"
        and len(node.args) + len(node.keywords) < 2
    ]


def test_every_snapshot_read_in_the_perception_is_given_its_factor() -> None:
    assert dom_reads_without_a_factor(PERCEPTION_SOURCE.read_text()) == []


def test_factor_check_catches_a_snapshot_read_without_it() -> None:
    checks: list[Callable[[str], list[int]]] = [dom_reads_without_a_factor]
    forgotten = "def f(raw):\n    return Dom(raw)\n"
    given = "def f(raw, metrics):\n    return Dom(raw, _device_scale(metrics))\n"
    keyword = "def f(raw):\n    return Dom(raw, device_scale=2.0)\n"
    for check in checks:
        assert check(forgotten) == [2]
        assert check(given) == [] and check(keyword) == []
