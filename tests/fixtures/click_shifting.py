"""What the shifting-page tests know of their page, checked without a browser.

Why this module exists:
    The page click-shifting.html moves its targets at the first movement of the pointer. The browser
    tests are worth something only if the page really does that, once, and puts an intruder where the
    target was. The contract of the page is a function of its text, tested here with variants that
    break it. The judgement of a click is the one of click_targets, which this page shares.
"""

from __future__ import annotations

import re

from fixtures.click_targets import _COUNTING, _OUTSIDE_WORLD, _boxes, _Buttons

PAGE = "click-shifting.html"
INTRUDERS = {"large": "large-intruder", "small": "small-intruder", "far": "far-intruder"}
NAMES = {
    "large": "Large target",
    "decoy-left": "Left decoy",
    "small": "Small target",
    "decoy-right": "Right decoy",
    "far": "Far target",
    "large-intruder": "Large intruder",
    "small-intruder": "Small intruder",
    "far-intruder": "Far intruder",
}
SHIFT = 60


def shifting_page_problems(source: str) -> list[str]:
    """What is wrong with the text of the shifting page; empty when it holds what the shifting-click tests rely on."""
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
    for target, intruder in INTRUDERS.items():
        for key in ("left", "top", "width", "height"):
            if boxes.get(target, {}).get(key) is None or boxes.get(target, {}).get(key) != boxes.get(intruder, {}).get(key):
                found.append(f"the intruder {intruder} does not take the {key} of {target}")
                break
    hidden = {name: "display: none;" in rule for name, rule in _rules(source).items()}
    for intruder in INTRUDERS.values():
        if not hidden.get(intruder):
            found.append(f"the intruder {intruder} is not out of the page before the first movement")
    if 'addEventListener("mousemove"' not in source or "{ once: true }" not in source:
        found.append("the shift is not tied to one first movement of the pointer")
    if f'style.marginTop = "{SHIFT}px"' not in source:
        found.append(f"the targets do not move {SHIFT} pixels down")
    if 'style.display = "block"' not in source:
        found.append("the intruders do not appear at the first movement")
    for target in INTRUDERS:
        height = boxes.get(target, {}).get("height")
        if height is not None and height[1] == "px" and height[0] >= SHIFT:
            found.append(f"{target} is as high as the shift: the intruder would not stand apart from it")
    return found


def _rules(source: str) -> dict[str, str]:
    style = re.search(r"<style>(.*?)</style>", source, re.DOTALL)
    return dict(re.findall(r"#([\w-]+)\s*\{([^}]*)\}", style.group(1) if style else ""))
