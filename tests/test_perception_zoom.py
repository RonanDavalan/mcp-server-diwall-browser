"""The image of an observation at a page zoom other than 100 %, on a simulated browser.

The browser reads the cut of a screenshot in CSS pixels times the page zoom. A cut given in CSS
pixels shows another part of the page than the one the masks are laid on, and the image would be
published with its sensitive fields in view. The checks below are written as functions of the
implementation, so that each is also run on a defective one and shown to fail.
"""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Callable
from typing import Any

import pytest
from conftest import FAKE_PASSWORD
from fixtures.fake_cdp import FakeBrowser, FakeCdp  # noqa: F401 - FakeCdp is the type of a fixture
from fixtures.page_model import Page, button, div, field_input
from fixtures.zoomed_page import ZoomedPage, at_zoom, clip_with_the_offset_unscaled, clip_without_the_zoom
from PIL import Image
from test_perception import (  # noqa: F401 - fakes and fixtures
    fake,
    fake_browser,
    perceiver,
    registry,
    showing,
)

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.browser import TabRegistry
from mcp_server_diwall_browser.output import ToolError
from mcp_server_diwall_browser.perception import MASK_COLOUR, Box, Observation, render

ZOOMS = (1.0, 1.25, 1.5, 2.0)
SCREEN_SCALES = (1.0, 1.5)
CASES = [(zoom, scale) for scale in SCREEN_SCALES for zoom in ZOOMS]
CASE_IDS = [f"zoom-{zoom:g}-scale-{scale:g}" for zoom, scale in CASES]
ZOOMED_CASES = [case for case in CASES if case[0] != 1.0]
ZOOMED_IDS = [f"zoom-{zoom:g}-scale-{scale:g}" for zoom, scale in ZOOMED_CASES]
# The view is 200 CSS pixels tall and starts 300 CSS pixels down the page.
VIEWPORT = (0.0, 300.0, 800.0, 200.0)
PASSWORD = Box(300, 340, 200, 24)
LANDMARK = Box(40, 330, 120, 40)
LANDMARK_COLOUR = (10, 160, 10)
COVERED = 0.99
Check = Callable[..., Any]


def scrolled_page() -> Page:
    """A page scrolled down, with a password field and a block of a colour that is nothing else in the image."""
    return Page(
        "Scrolled",
        [
            div(box=(LANDMARK.x, LANDMARK.y, LANDMARK.width, LANDMARK.height), paint=LANDMARK_COLOUR),
            field_input("Password", (PASSWORD.x, PASSWORD.y, PASSWORD.width, PASSWORD.height), attrs={"type": "password"}, value=FAKE_PASSWORD),
            button("Sign in", (300, 380, 80, 24)),
        ],
        viewport=VIEWPORT,
        content_size=(800, 1200),
    )


def share_of(png: bytes, box: Box, factor: float, colour: tuple[int, int, int], margin: int = 4) -> float:
    """The share of the inside of ``box`` (CSS pixels of the page) that is ``colour`` in the image.

    ``factor`` is image pixels per CSS pixel; the edges are left out, where the reduction blends.
    """
    image = Image.open(io.BytesIO(png)).convert("RGB")
    left = math.ceil((box.x - VIEWPORT[0]) * factor) + margin
    top = math.ceil((box.y - VIEWPORT[1]) * factor) + margin
    right = math.floor((box.right - VIEWPORT[0]) * factor) - margin
    bottom = math.floor((box.bottom - VIEWPORT[1]) * factor) - margin
    assert right > left and bottom > top, (left, top, right, bottom)
    pixels = list(image.crop((left, top, right, bottom)).getdata())
    near = sum(1 for pixel in pixels if all(abs(a - b) <= 3 for a, b in zip(pixel, colour, strict=True)))
    return near / len(pixels)


async def observe_at(fake_browser: FakeBrowser, registry: TabRegistry, page: Page) -> Observation:
    tab = await showing(fake_browser, registry, page)
    return await perceiver(registry).observe(tab)


async def image_problems(fake_browser: FakeBrowser, registry: TabRegistry, zoom: float, scale: float) -> list[str]:
    """What is wrong with the image of the scrolled page at this zoom and screen scale; empty when it is right."""
    observation = await observe_at(fake_browser, registry, at_zoom(scrolled_page(), zoom, scale))
    if observation.server["capture"] != "ok" or observation.image_png is None:
        return [f"the image is not published: capture is {observation.server['capture']}"]
    # The published image may be reduced; the page is painted at its box times the device pixels per CSS pixel.
    factor = zoom * scale * observation.server["image"]["scale"]
    found = []
    if (share := share_of(observation.image_png, PASSWORD, factor, MASK_COLOUR)) < COVERED:
        found.append(f"the mask covers {share:.0%} of the password field")
    if (share := share_of(observation.image_png, LANDMARK, factor, LANDMARK_COLOUR)) < COVERED:
        found.append(f"the block that the browser paints in view shows in {share:.0%} of its place")
    return found


def screenshot_requests(fake: FakeCdp) -> list[dict[str, Any]]:
    return [message for message in fake.received if message.get("method") == "Page.captureScreenshot"]


# --- the cut is in CSS pixels times the zoom ----------------------------------------------------


@pytest.mark.parametrize(("zoom", "scale"), CASES, ids=CASE_IDS)
async def test_the_cut_is_given_in_css_pixels_times_the_page_zoom(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, zoom: float, scale: float
) -> None:
    await observe_at(fake_browser, registry, at_zoom(scrolled_page(), zoom, scale))
    [request] = screenshot_requests(fake)
    wanted = {"x": 0.0, "y": 300.0 * zoom, "width": 800.0 * zoom, "height": 200.0 * zoom, "scale": 1}
    assert request["params"]["clip"] == pytest.approx(wanted), request["params"]["clip"]


@pytest.mark.parametrize(("zoom", "scale"), CASES, ids=CASE_IDS)
async def test_the_image_follows_the_view_and_the_mask_covers_the_field_at_every_page_zoom(
    fake_browser: FakeBrowser, registry: TabRegistry, zoom: float, scale: float
) -> None:
    assert await image_problems(fake_browser, registry, zoom, scale) == []


@pytest.mark.parametrize(("zoom", "scale"), CASES, ids=CASE_IDS)
async def test_the_image_check_catches_a_cut_whose_offset_is_not_multiplied_by_the_zoom(
    fake_browser: FakeBrowser, registry: TabRegistry, monkeypatch: pytest.MonkeyPatch, zoom: float, scale: float
) -> None:
    monkeypatch.setattr(perception, "_screenshot_clip", clip_with_the_offset_unscaled)
    found = await image_problems(fake_browser, registry, zoom, scale)
    if zoom == 1.0:
        assert found == []  # the cut is right at a zoom of 100 %: the check fails only where the defect is
    else:
        assert found and all("shows in" in line or "covers" in line for line in found), found


# --- an image cut without the zoom is withheld, and without the guard it would leave ------------


@pytest.mark.parametrize(("zoom", "scale"), CASES, ids=CASE_IDS)
async def test_a_cut_made_without_the_zoom_withholds_the_image_and_serves_the_tree(
    fake_browser: FakeBrowser, registry: TabRegistry, monkeypatch: pytest.MonkeyPatch, zoom: float, scale: float
) -> None:
    monkeypatch.setattr(perception, "_screenshot_clip", clip_without_the_zoom)
    observation = await observe_at(fake_browser, registry, at_zoom(scrolled_page(), zoom, scale))
    if zoom == 1.0:
        assert observation.server["capture"] == "ok"
        return
    assert observation.server["capture"] == "unavailable" and observation.image_png is None
    assert observation.page_content is not None and observation.page_content["elements"]


@pytest.mark.parametrize(("zoom", "scale"), ZOOMED_CASES, ids=ZOOMED_IDS)
async def test_without_the_size_guard_a_cut_made_without_the_zoom_would_publish_a_misplaced_mask(
    fake_browser: FakeBrowser, registry: TabRegistry, monkeypatch: pytest.MonkeyPatch, zoom: float, scale: float
) -> None:
    monkeypatch.setattr(perception, "_screenshot_clip", clip_without_the_zoom)
    monkeypatch.setattr(perception, "_image_fits_view", lambda png, metrics: True)
    found = await image_problems(fake_browser, registry, zoom, scale)
    assert found and not found[0].startswith("the image is not published"), found


# --- a zoom the metrics do not give, or an image of another size, gives no image -----------------


def with_zoom_reported(page: ZoomedPage, value: Any) -> ZoomedPage:
    """The same page whose browser reports ``value`` as the zoom; ``...`` leaves the key out."""
    real = page.layout_metrics

    def metrics() -> dict[str, Any]:
        answer = real()
        if value is ...:
            del answer["visualViewport"]["zoom"]
        else:
            answer["visualViewport"]["zoom"] = value
        return answer

    page.layout_metrics = metrics  # type: ignore[method-assign]
    return page


UNUSABLE_ZOOMS = [
    pytest.param(..., id="absent"),
    pytest.param(0, id="zero"),
    pytest.param(0.0, id="zero-float"),
    pytest.param(-1.25, id="negative"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinite"),
    pytest.param("1.25", id="text"),
    pytest.param(None, id="null"),
    pytest.param(True, id="boolean"),
    pytest.param([1.25], id="list"),
]


@pytest.mark.parametrize("value", UNUSABLE_ZOOMS)
async def test_a_zoom_that_cannot_be_used_gives_no_image_and_no_cut_is_asked_for(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, value: Any
) -> None:
    page = with_zoom_reported(at_zoom(scrolled_page(), 1.25), value)
    observation = await observe_at(fake_browser, registry, page)
    assert observation.server["capture"] == "unavailable" and observation.image_png is None
    assert observation.page_content is not None and observation.page_content["elements"]
    assert "image" not in observation.server
    assert screenshot_requests(fake) == []
    assert any(notice.startswith("capture_unavailable") for notice in observation.server["notices"])


def resized_screenshot(page: ZoomedPage, extra_width: int, extra_height: int) -> ZoomedPage:
    """The same page whose browser answers an image larger (or smaller) than the view by these pixels."""
    real = page.screenshot

    def screenshot(clip: dict[str, Any] | None = None) -> dict[str, Any]:
        image = Image.open(io.BytesIO(base64.b64decode(real(clip)["data"]))).convert("RGB")
        canvas = Image.new("RGB", (image.width + extra_width, image.height + extra_height), (255, 255, 255))
        canvas.paste(image, (0, 0))
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return {"data": base64.b64encode(output.getvalue()).decode("ascii")}

    page.screenshot = screenshot  # type: ignore[method-assign]
    return page


@pytest.mark.parametrize(
    ("extra_width", "extra_height", "published"),
    [
        (0, 0, True),
        (-1, 0, True),
        (0, -1, True),
        (2, 0, True),
        (0, 2, True),
        (-2, -2, True),
        (3, 0, False),
        (-3, 0, False),
        (0, 3, False),
        (0, -3, False),
        (3, 3, False),
        (400, 400, False),
        (-400, -100, False),
    ],
)
async def test_an_image_more_than_two_pixels_from_the_size_of_the_view_on_either_axis_is_withheld(
    fake_browser: FakeBrowser, registry: TabRegistry, extra_width: int, extra_height: int, published: bool
) -> None:
    page = resized_screenshot(at_zoom(scrolled_page(), 1.25), extra_width, extra_height)
    observation = await observe_at(fake_browser, registry, page)
    assert (observation.server["capture"] == "ok") is published
    assert (observation.image_png is not None) is published
    assert observation.page_content is not None and observation.page_content["elements"]


async def test_bytes_that_are_not_an_image_give_no_image(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    page = at_zoom(scrolled_page(), 1.25)
    page.screenshot = lambda clip=None: {"data": base64.b64encode(b"not an image at all").decode("ascii")}  # type: ignore[method-assign]
    observation = await observe_at(fake_browser, registry, page)
    assert observation.server["capture"] == "unavailable" and observation.image_png is None
    assert observation.page_content is not None and observation.page_content["elements"]


async def test_one_browser_that_goes_wrong_three_ways_in_a_row_never_publishes_an_image(
    fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    """A zoom the metrics leave out, then a zoom of 0, then an image of the wrong size: no image, each time."""
    pages = [
        with_zoom_reported(at_zoom(scrolled_page(), 1.5), ...),
        with_zoom_reported(at_zoom(scrolled_page(), 1.5), 0),
        resized_screenshot(at_zoom(scrolled_page(), 1.5), 120, 80),
    ]
    tab = await showing(fake_browser, registry, pages[0])
    perception_ = perceiver(registry)
    for page in pages:
        fake_browser.show(tab.target_id, page)
        observation = await perception_.observe(tab)
        assert observation.server["capture"] == "unavailable" and observation.image_png is None
        assert observation.page_content is not None and observation.page_content["elements"]
        assert perception_.latest_snapshot(tab.tab_id) is not None
        assert perception_.latest_snapshot(tab.tab_id).image_png is None  # type: ignore[union-attr]


# --- the guards, run on a defective version ----------------------------------------------------


def png_of_size(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(output, format="PNG")
    return output.getvalue()


def size_guard_problems(fits: Check) -> list[str]:
    """What a size guard gets wrong for a view of 1000 by 750 device pixels; empty when it is right."""
    metrics = {"visualViewport": {"clientWidth": 1000.0, "clientHeight": 750.0}}
    wrong = []
    for size, accepted in (
        ((1000, 750), True),
        ((999, 749), True),
        ((1002, 748), True),
        ((1003, 750), False),
        ((1000, 747), False),
        ((997, 750), False),
        ((500, 375), False),
    ):
        if fits(png_of_size(*size), metrics) is not accepted:
            wrong.append(f"{size} is {'refused' if accepted else 'accepted'}")
    for bad_metrics in (
        {},
        {"visualViewport": {}},
        {"visualViewport": {"clientWidth": 1000.0}},
        {"visualViewport": {"clientWidth": float("nan"), "clientHeight": 750.0}},
        {"visualViewport": {"clientWidth": 1000.0, "clientHeight": 0}},
        {"visualViewport": {"clientWidth": "wide", "clientHeight": 750.0}},
    ):
        if fits(png_of_size(1000, 750), bad_metrics):
            wrong.append(f"{bad_metrics} is accepted")
    if fits(b"not an image", metrics):
        wrong.append("bytes that are not an image are accepted")
    return wrong


def guard_accepting_any_size(png: bytes, metrics: dict[str, Any]) -> bool:
    return True


def guard_looking_at_the_width_only(png: bytes, metrics: dict[str, Any]) -> bool:
    try:
        return abs(Image.open(io.BytesIO(png)).width - float(metrics["visualViewport"]["clientWidth"])) <= 2
    except Exception:  # noqa: BLE001
        return False


def guard_with_a_wide_tolerance(png: bytes, metrics: dict[str, Any]) -> bool:
    try:
        size = Image.open(io.BytesIO(png)).size
        wanted = (float(metrics["visualViewport"]["clientWidth"]), float(metrics["visualViewport"]["clientHeight"]))
    except Exception:  # noqa: BLE001
        return False
    return all(abs(a - b) <= 20 for a, b in zip(size, wanted, strict=True))


def test_the_size_guard_accepts_the_size_of_the_view_within_two_pixels_and_nothing_else() -> None:
    assert size_guard_problems(perception._image_fits_view) == []


@pytest.mark.parametrize("defective", [guard_accepting_any_size, guard_looking_at_the_width_only, guard_with_a_wide_tolerance])
def test_the_size_guard_check_fails_on_a_defective_guard(defective: Check) -> None:
    assert size_guard_problems(defective) != []


def zoom_reading_problems(page_zoom: Check) -> list[str]:
    """What a reader of the page zoom gets wrong; empty when it is right."""
    wrong = []
    for zoom in (1, 1.25, 1.5, 2, 0.5, 5.0):
        if page_zoom({"visualViewport": {"zoom": zoom}}) != zoom:
            wrong.append(f"{zoom} is not read as {zoom}")
    for metrics in (
        {},
        {"visualViewport": {}},
        {"visualViewport": None},
        {"visualViewport": {"zoom": 0}},
        {"visualViewport": {"zoom": -1}},
        {"visualViewport": {"zoom": float("nan")}},
        {"visualViewport": {"zoom": float("inf")}},
        {"visualViewport": {"zoom": "1.25"}},
        {"visualViewport": {"zoom": True}},
        {"cssVisualViewport": {"zoom": 1.25}},
    ):
        if page_zoom(metrics) is not None:
            wrong.append(f"{metrics} is read as {page_zoom(metrics)}")
    return wrong


def zoom_read_as_one_when_missing(metrics: dict[str, Any]) -> float | None:
    try:
        zoom = float(metrics["visualViewport"]["zoom"])
    except (KeyError, TypeError, ValueError):
        return 1.0
    return zoom if math.isfinite(zoom) and zoom > 0 else 1.0


def zoom_read_without_a_lower_bound(metrics: dict[str, Any]) -> float | None:
    try:
        zoom = metrics["visualViewport"]["zoom"]
    except (KeyError, TypeError):
        return None
    return float(zoom) if isinstance(zoom, (int, float)) and math.isfinite(zoom) else None


def test_the_zoom_is_read_from_the_visual_viewport_and_from_nothing_that_is_not_a_usable_number() -> None:
    assert zoom_reading_problems(perception._page_zoom) == []


@pytest.mark.parametrize("defective", [zoom_read_as_one_when_missing, zoom_read_without_a_lower_bound])
def test_the_zoom_reading_check_fails_on_a_defective_reader(defective: Check) -> None:
    assert zoom_reading_problems(defective) != []


# --- the zoom must be the same when the boxes are read again -------------------------------------


async def zoom_change_during_the_capture_is_refused(fake_browser: FakeBrowser, registry: TabRegistry) -> bool:
    """True when an image taken while the zoom changes is withheld.

    The CSS view is the same in both readings, so that the zoom alone tells them apart.
    """
    page = at_zoom(scrolled_page(), 1.25)
    tab = await showing(fake_browser, registry, page)

    def zoom_the_page(target_id: str) -> None:
        fake_browser.targets[target_id]["page"] = at_zoom(scrolled_page(), 1.5)
        fake_browser.after.pop("Page.captureScreenshot")

    fake_browser.after["Page.captureScreenshot"] = zoom_the_page
    observation = await perceiver(registry).observe(tab)
    return observation.server["capture"] == "unavailable" and observation.image_png is None


async def test_an_image_taken_while_the_zoom_changes_is_withheld(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    assert await zoom_change_during_the_capture_is_refused(fake_browser, registry)


async def test_the_zoom_change_check_fails_when_the_second_reading_does_not_compare_the_zoom(
    fake_browser: FakeBrowser, registry: TabRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perception, "_page_zoom", lambda metrics: 1.25)  # a reader that sees the same zoom twice
    assert not await zoom_change_during_the_capture_is_refused(fake_browser, registry)


# --- the evidence image follows the same cut -----------------------------------------------------


@pytest.mark.parametrize(("zoom", "scale"), CASES, ids=CASE_IDS)
async def test_the_evidence_image_is_cut_at_the_zoom_too(
    fake_browser: FakeBrowser, registry: TabRegistry, zoom: float, scale: float
) -> None:
    tab = await showing(fake_browser, registry, at_zoom(scrolled_page(), zoom, scale))
    png = await perceiver(registry).capture_masked_png(tab)
    factor = zoom * scale
    assert share_of(png, PASSWORD, factor, MASK_COLOUR) >= COVERED
    assert share_of(png, LANDMARK, factor, LANDMARK_COLOUR) >= COVERED


async def test_the_evidence_image_is_refused_when_the_zoom_cannot_be_used(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, with_zoom_reported(at_zoom(scrolled_page(), 1.25), 0))
    with pytest.raises(ToolError) as caught:
        await perceiver(registry).capture_masked_png(tab)
    assert caught.value.code == "capture_unavailable"


# --- a box is placed with the view the browser states, not with the size of an image that is a pixel short ----


def mask_rect_problems(place: Callable[[bytes, Box, Box, tuple[float, float]], tuple[int, int, int, int]]) -> list[str]:
    """What a placement of a mask gets wrong when the image is a pixel short of the view; empty when it is right.

    ``place`` takes the image, the view in CSS pixels, the box in CSS pixels and the view in device
    pixels, and gives the rectangle of the mask in the published image. The box must be covered whole.
    """
    wrong = []
    # A view of 800 by 600 CSS pixels; the image is one pixel narrower, or one pixel shorter, than the view.
    # The far edges of the boxes fall half way between two device pixels, where the size of the image
    # taken for the size of the view puts them a pixel short.
    for ratio, (extra_width, extra_height), box in (
        (1.5, (-1, 0), Box(759.75, 100, 36, 20)),
        (1.5, (0, -1), Box(100, 559.75, 36, 36)),
        (1.25, (-1, -1), Box(700.4, 500.4, 96, 96)),
        (2.0, (-1, -1), Box(742.75, 562.75, 56, 36)),
    ):
        view = Box(0, 0, 800, 600)
        device_view = (800 * ratio, 600 * ratio)
        png = png_of_size(round(device_view[0]) + extra_width, round(device_view[1]) + extra_height)
        x0, y0, x1, y1 = place(png, view, box, device_view)
        if x0 > box.x * ratio or y0 > box.y * ratio or x1 < box.right * ratio or y1 < box.bottom * ratio:
            wrong.append(f"ratio {ratio}, image {extra_width:+d}/{extra_height:+d}: the mask {(x0, y0, x1, y1)} does not cover {box}")
    return wrong


def placed_with_the_view_of_the_browser(png: bytes, view: Box, box: Box, device_view: tuple[float, float]) -> tuple[int, int, int, int]:
    return render(png, view, sensitive_boxes=[box], frame_boxes=[], marks=[], max_width=None, device_view=device_view).mask_rects[0]


def placed_with_the_size_of_the_image(png: bytes, view: Box, box: Box, device_view: tuple[float, float]) -> tuple[int, int, int, int]:
    return render(png, view, sensitive_boxes=[box], frame_boxes=[], marks=[], max_width=None).mask_rects[0]


def test_a_mask_covers_its_box_whole_when_the_image_is_a_pixel_short_of_the_view() -> None:
    assert mask_rect_problems(placed_with_the_view_of_the_browser) == []


def test_the_mask_placement_check_fails_on_a_placement_that_stretches_the_image_over_the_view() -> None:
    assert mask_rect_problems(placed_with_the_size_of_the_image) != []


@pytest.mark.parametrize(("zoom", "scale"), CASES, ids=CASE_IDS)
async def test_the_observation_places_the_masks_with_the_view_the_browser_states(
    fake_browser: FakeBrowser, registry: TabRegistry, monkeypatch: pytest.MonkeyPatch, zoom: float, scale: float
) -> None:
    seen: list[tuple[float, float] | None] = []
    real = perception.render

    def watching(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("device_view"))
        return real(*args, **kwargs)

    monkeypatch.setattr(perception, "render", watching)
    await observe_at(fake_browser, registry, at_zoom(scrolled_page(), zoom, scale))
    assert seen == [pytest.approx((800 * zoom * scale, 200 * zoom * scale))]
