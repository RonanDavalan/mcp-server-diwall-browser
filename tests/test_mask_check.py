"""The check that reads a mask back from an image, tried on images whose right answer is known.

Why this module exists:
    The browser tests read the published image to see whether a mask covers its field and goes no
    further. A check that takes page text for a mask failed on a browser whose anti-aliasing gives
    text pixels exactly the mask colour. This checks the check, without a browser, on images drawn
    here: a defective check must fail the same cases the real one passes.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from PIL import Image
from test_scale_integration import TOLERANCE, Rect, mask_found_apart

from mcp_server_diwall_browser.perception import MASK_COLOUR

Check = Callable[[Image.Image, Rect], list[str]]

# A box with fractional edges, as the browser gives them; the mask is drawn on the whole pixels around it.
BOX: Rect = (100.5, 40.2, 350.5, 67.2)
DRAWN = (100, 40, 351, 68)
SIZE = (460, 120)
GROWTH = 3
# Pixels of the mask colour that belong to the page, inside the 12-pixel window and not touching the mask:
# at the left edge of the window, near the left, at the top edge, at the bottom edge, at the right edge.
STRAY = {
    "window-left-edge": (88, 50),
    "near-left": (93, 55),
    "window-top-edge": (200, 28),
    "window-bottom-edge": (200, 79),
    "window-right-edge": (362, 50),
}


def drawn(mask: tuple[int, int, int, int] = DRAWN, *, stray: tuple[tuple[int, int], ...] = ()) -> Image.Image:
    image = Image.new("RGB", SIZE, (255, 255, 255))
    image.paste(MASK_COLOUR, mask)
    for point in stray:
        image.putpixel(point, MASK_COLOUR)
    return image


def moved(side: str, by: int) -> tuple[int, int, int, int]:
    """The drawn mask with one side moved outwards by ``by`` pixels (inwards when negative)."""
    left, top, right, bottom = DRAWN
    return {
        "left": (left - by, top, right, bottom),
        "top": (left, top - by, right, bottom),
        "right": (left, top, right + by, bottom),
        "bottom": (left, top, right, bottom + by),
    }[side]


SIDES = ("left", "top", "right", "bottom")


def counting_every_pixel(image: Image.Image, box: Rect) -> list[str]:
    """The check as it was first written: any pixel of the mask colour in the window is the mask."""
    x0, y0, x1, y1 = box
    inside = (math.ceil(x0), math.ceil(y0), math.floor(x1), math.floor(y1))
    colours = set(image.crop(inside).getdata())
    found = [] if colours == {MASK_COLOUR} else [f"the box is not one colour: {len(colours)} colours"]
    margin = 12
    window = (max(0, math.floor(x0) - margin), max(0, math.floor(y0) - margin), min(image.width, math.ceil(x1) + margin), min(image.height, math.ceil(y1) + margin))
    region = image.crop(window)
    dark = [
        (window[0] + index % region.width, window[1] + index // region.width)
        for index, pixel in enumerate(region.getdata())
        if pixel == MASK_COLOUR
    ]
    if dark:
        left, right = min(p[0] for p in dark), max(p[0] for p in dark) + 1
        top, bottom = min(p[1] for p in dark), max(p[1] for p in dark) + 1
        overflow = max(x0 - left, right - x1, y0 - top, bottom - y1)
        if overflow > TOLERANCE:
            found.append(f"the mask goes {overflow:.1f} pixels beyond the box")
    return found


def never_reproaching_overflow(image: Image.Image, box: Rect) -> list[str]:
    """A check that only looks inside the box: it cannot see a mask that goes further."""
    x0, y0, x1, y1 = box
    colours = set(image.crop((math.ceil(x0), math.ceil(y0), math.floor(x1), math.floor(y1))).getdata())
    return [] if colours == {MASK_COLOUR} else [f"the box is not one colour: {len(colours)} colours"]


def problems_with(check: Check) -> list[str]:
    """What ``check`` gets wrong on images whose right answer is known; empty when it gets none wrong."""
    wrong: list[str] = []
    if check(drawn(), BOX):
        wrong.append("it reproaches an exact mask")
    if check(drawn(stray=tuple(STRAY.values())), BOX):
        wrong.append("it reproaches an exact mask because page pixels share its colour")
    for side in SIDES:
        if not check(drawn(moved(side, GROWTH)), BOX):
            wrong.append(f"it misses a mask that goes {GROWTH} pixels beyond the box on the {side} side")
        if not check(drawn(moved(side, -GROWTH)), BOX):
            wrong.append(f"it misses a mask that stops {GROWTH} pixels short of the box on the {side} side")
    return wrong


# --- the real check ------------------------------------------------------------------------------


def test_the_mask_check_passes_an_exact_mask_and_fails_every_mask_that_is_three_pixels_off() -> None:
    assert problems_with(mask_found_apart) == []


@pytest.mark.parametrize("where", STRAY)
def test_a_page_pixel_of_the_mask_colour_apart_from_the_mask_is_not_a_reproach(where: str) -> None:
    assert mask_found_apart(drawn(stray=(STRAY[where],)), BOX) == []


@pytest.mark.parametrize("side", SIDES)
def test_a_mask_that_goes_three_pixels_beyond_the_box_is_reproached_even_with_page_pixels_around(side: str) -> None:
    found = mask_found_apart(drawn(moved(side, GROWTH), stray=tuple(STRAY.values())), BOX)
    assert len(found) == 1 and found[0].startswith("the mask goes 3."), found


@pytest.mark.parametrize("side", SIDES)
def test_a_mask_that_stops_three_pixels_short_of_the_box_is_reproached(side: str) -> None:
    found = mask_found_apart(drawn(moved(side, -GROWTH)), BOX)
    assert found and found[0].startswith("the box is not one colour"), found


def test_a_pixel_touching_the_mask_within_the_tolerance_is_not_a_reproach() -> None:
    # One pixel joined to the mask is a pixel the mask may reach; the tolerance is the slack the check allows.
    assert mask_found_apart(drawn(stray=((99, 50),)), BOX) == []


# --- counter-proofs: the same cases on checks that have the defect -------------------------------


def test_the_check_counting_every_pixel_of_the_colour_fails_on_page_pixels_around_an_exact_mask() -> None:
    assert problems_with(counting_every_pixel) == ["it reproaches an exact mask because page pixels share its colour"]


@pytest.mark.parametrize("where", STRAY)
def test_the_check_counting_every_pixel_of_the_colour_reproaches_each_page_pixel_alone(where: str) -> None:
    assert counting_every_pixel(drawn(stray=(STRAY[where],)), BOX) != []


def test_a_check_that_looks_only_inside_the_box_fails_on_a_mask_that_goes_beyond_it() -> None:
    wrong = problems_with(never_reproaching_overflow)
    assert len(wrong) == len(SIDES) and all(line.startswith("it misses a mask that goes") for line in wrong), wrong
