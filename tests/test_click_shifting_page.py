"""The page whose targets move at the first movement of the pointer, checked without a browser.

The browser tests of the shifting page trust that it moves its targets once and leaves an intruder
where each was. That is checked here on the text of the page, with variants that lack each part and
must be refused.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fixtures.click_shifting import INTRUDERS, NAMES, PAGE, assert_landed_or_refused, shifting_click_problems, shifting_page_problems
from fixtures.click_targets import NAMES as STILL_NAMES
from fixtures.click_targets import Received
from fixtures.page_server import PageServer
from test_click_targets_page import fetch


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    with PageServer() as server:
        yield fetch(server, PAGE)


def test_the_shifting_page_holds_what_the_shifting_click_tests_rely_on(served: str) -> None:
    assert shifting_page_problems(served) == []


def test_the_shifting_page_keeps_the_buttons_and_names_of_the_still_page() -> None:
    assert {name: NAMES[name] for name in STILL_NAMES} == STILL_NAMES
    assert set(INTRUDERS.values()) == set(NAMES) - set(STILL_NAMES)
    assert len(set(NAMES.values())) == len(NAMES)


def swapped(old: str, new: str) -> Callable[[str], str]:
    return lambda text: text.replace(old, new)


DEFECTS = {
    "no stray counter": (swapped("__stray_clicks", "__other_clicks"), "does not count its clicks"),
    "no counter of the buttons": (swapped("window.__clicks", "window.__counts"), "does not count its clicks"),
    "an intruder with the name of its target": (swapped(">Large intruder<", ">Large target<"), "buttons and their names"),
    "an intruder more": (swapped("</body>", '<button id="extra" type="button">Extra</button></body>'), "buttons and their names"),
    "an address outside the page": (swapped("</body>", '<img src="https://example.invalid/x.png"></body>'), "outside itself"),
    "an intruder beside its target": (swapped("#small-intruder { left: 520px;", "#small-intruder { left: 524px;"), "does not take the left"),
    "an intruder of another size": (swapped("#far-intruder { left: 320px; top: 250vh; width: 120px;", "#far-intruder { left: 320px; top: 250vh; width: 100px;"), "does not take the width"),
    "an intruder shown from the start": (swapped("#large-intruder { left: 320px; top: 500px; width: 120px; height: 32px; display: none; }", "#large-intruder { left: 320px; top: 500px; width: 120px; height: 32px; }"), "out of the page"),
    "a shift at every movement": (swapped("}, { once: true });", "});"), "one first movement"),
    "a shift at a click": (swapped('addEventListener("mousemove"', 'addEventListener("mouseup"'), "one first movement"),
    "no shift": (swapped('style.marginTop = "60px"', 'style.marginTop = "0px"'), "pixels down"),
    "intruders that stay hidden": (swapped('style.display = "block"', 'style.display = "none"'), "do not appear"),
    "a target as high as the shift": (swapped("#large { left: 320px; top: 500px; width: 120px; height: 32px; }", "#large { left: 320px; top: 500px; width: 120px; height: 60px; }"), "as high as the shift"),
}


@pytest.mark.parametrize("defect", DEFECTS)
def test_the_shifting_page_check_refuses_a_page_that_has_the_defect(served: str, defect: str) -> None:
    change, named = DEFECTS[defect]
    broken = change(served)
    assert broken != served, "the variant is the page itself: the defect was not applied"
    assert any(named in problem for problem in shifting_page_problems(broken)), shifting_page_problems(broken)


# --- the judgement of a click on a page that moves --------------------------------------------------

QUIET = {name: 0 for name in NAMES}


def received(target: str | None = None, *, stray: int = 0, error: str | None = None, **others: int) -> Received:
    clicks = dict(QUIET)
    if target is not None:
        clicks[target] = 1
    clicks.update({name.replace("_", "-"): number for name, number in others.items()})
    return Received(clicks, stray, error)


@pytest.mark.parametrize(
    "attempt",
    [received("large"), received("small"), received("far"), received(error="element_obscured")],
    ids=("landed-large", "landed-small", "landed-far", "refused-nothing-clicked"),
)
def test_a_click_that_landed_or_that_was_refused_with_nothing_clicked_is_accepted(attempt: Received) -> None:
    target = next((name for name, number in attempt.clicks.items() if number), "large")
    assert shifting_click_problems(attempt, target) == []
    assert_landed_or_refused(attempt, target)


@pytest.mark.parametrize(
    ("attempt", "target"),
    [
        (received("large-intruder"), "large"),
        (received("small-intruder"), "small"),
        (received("decoy-left"), "small"),
        (received(stray=1), "far"),
        (received(), "large"),
        (received("large", large_intruder=1), "large"),
        (received("large", error="element_obscured"), "large"),
        (received(error="element_obscured", stray=1), "far"),
        (received(error="element_obscured", far_intruder=1), "far"),
        (received(error="navigation_failed"), "large"),
        (received(error="TypeError"), "large"),
    ],
    ids=(
        "landed-on-the-intruder",
        "landed-on-the-small-intruder",
        "landed-on-a-decoy",
        "lost-with-no-error",
        "nothing-and-no-error",
        "target-and-intruder",
        "refused-yet-clicked",
        "refused-yet-a-click-was-lost",
        "refused-yet-an-intruder-was-clicked",
        "another-error",
        "an-escaped-exception",
    ),
)
def test_a_click_that_went_elsewhere_or_nowhere_is_a_failure_whatever_the_server_said(attempt: Received, target: str) -> None:
    assert shifting_click_problems(attempt, target)
    with pytest.raises(AssertionError):
        assert_landed_or_refused(attempt, target)
