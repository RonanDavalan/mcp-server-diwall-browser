"""What the click tests know of their target page, and the check that a click landed on its target.

Why this module exists:
    The page click-targets.html counts the clicks each of its buttons receives. Whether a click
    "landed" is a judgement over those counts. It lives here as a function of data alone, so that it
    is tested by itself against clicks that missed, doubled or hit a neighbour, and so that the real
    clicks and the wrong ones of the browser tests are judged by the very same question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

PAGE = "click-targets.html"

# The accessible name of each button of the page, by id: what the observation shows and the tests ask for.
NAMES = {
    "large": "Large target",
    "decoy-left": "Left decoy",
    "small": "Small target",
    "decoy-right": "Right decoy",
    "far": "Far target",
}
TARGETS = ("large", "small", "far")


@dataclass(frozen=True)
class Received:
    """What a page received from one click attempt, read apart from the server."""

    clicks: dict[str, int]
    stray: int
    # The error code the click returned, when it returned one.
    error: str | None = None


def landing_problems(received: Received, target: str) -> list[str]:
    """What is wrong with a click meant for ``target``; empty when it landed there, once, and nowhere else."""
    found = []
    if received.error is not None:
        found.append(f"the click returned the error {received.error}")
    count = received.clicks.get(target, 0)
    if count != 1:
        found.append(f"{target} received {count} clicks, exactly one is expected")
    found += [f"{name} received {number} clicks" for name, number in received.clicks.items() if name != target and number]
    if received.stray:
        found.append(f"{received.stray} clicks reached no button")
    return found


def assert_landed(received: Received, target: str) -> None:
    problems = landing_problems(received, target)
    if problems:
        raise AssertionError("; ".join(problems))


# --- the contract of the page itself -------------------------------------------------------------


class _Buttons(HTMLParser):
    """The id and the accessible name of each button: its label, else its text."""

    def __init__(self) -> None:
        super().__init__()
        self.found: dict[str, str] = {}
        self._open: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "button":
            values = dict(attrs)
            self._open = values.get("id") or ""
            self.found[self._open] = values.get("aria-label") or ""

    def handle_data(self, data: str) -> None:
        if self._open is not None and not self.found[self._open]:
            self.found[self._open] = data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "button":
            self._open = None


_RULE = re.compile(r"#([\w-]+)\s*\{([^}]*)\}")
_DECLARATION = re.compile(r"([\w-]+)\s*:\s*(\d+(?:\.\d+)?)(px|vh)\s*;")
_OUTSIDE_WORLD = re.compile(r"https?://|\bsrc\s*=|\bhref\s*=|url\(|@import")
_COUNTING = ("window.__clicks", "window.__stray_clicks", 'document.addEventListener("click"')


def _boxes(source: str) -> dict[str, dict[str, tuple[float, str]]]:
    style = re.search(r"<style>(.*?)</style>", source, re.DOTALL)
    rules = _RULE.findall(style.group(1) if style else "")
    return {name: {key: (float(value), unit) for key, value, unit in _DECLARATION.findall(body)} for name, body in rules}


def page_problems(source: str) -> list[str]:
    """What is wrong with the text of the target page; empty when it holds what the click tests rely on."""
    found = []
    parser = _Buttons()
    parser.feed(source)
    if parser.found != NAMES:
        found.append(f"the buttons and their names are {parser.found}, the tests ask for {NAMES}")
    for needle in _COUNTING:
        if needle not in source:
            found.append(f"the page does not count its clicks: {needle} is missing")
    if _OUTSIDE_WORLD.search(source):
        found.append("the page refers to something outside itself")
    boxes = _boxes(source)

    def px(name: str, key: str) -> float | None:
        value = boxes.get(name, {}).get(key)
        return value[0] if value is not None and value[1] == "px" else None

    large = [px("large", key) for key in ("left", "top", "width", "height")]
    if None in large:
        found.append("the large button is not placed in pixels")
    elif not (large[0] > 300 and large[1] > 300 and large[2] >= 80 and large[3] >= 32):  # type: ignore[operator]
        found.append(f"the large button is {large[2]}x{large[3]} at ({large[0]}, {large[1]}): at least 80x32, beyond 300 from the corner")
    small = ("decoy-left", "small", "decoy-right")
    if any(px(name, key) != 16 for name in small for key in ("width", "height")):
        found.append("the small target and its decoys are not all 16x16")
    if len({px(name, "top") for name in small}) != 1:
        found.append("the small target and its decoys are not on one line")
    left, middle, right = (px(name, "left") for name in small)
    if None in (left, middle, right) or middle - (left + 16) != 4 or right - (middle + 16) != 4:  # type: ignore[operator]
        found.append("a decoy is not 4 pixels from the small target on each side")
    far = boxes.get("far", {}).get("top")
    if far is None or far[1] != "vh" or far[0] < 200:
        found.append("the far button is not placed at two heights of the view or more")
    return found
