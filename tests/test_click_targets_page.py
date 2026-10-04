"""The target page of the click tests and the check that a click landed on its target, without a browser.

The browser tests trust two things: the page counts clicks and is laid out as they assume, and the
check that judges a click tells a click that landed from every way of missing. Both are checked here
on their own, each with a variant that has the defect and must be refused.
"""

from __future__ import annotations

import http.client
from collections.abc import Callable, Iterator

import pytest
from fixtures.click_targets import (
    NAMES,
    PAGE,
    SCROLLED_NAMES,
    SCROLLED_PAGE,
    TARGETS,
    Received,
    assert_landed,
    assert_refused,
    landing_problems,
    page_problems,
    refusal_problems,
    scrolled_page_problems,
)
from fixtures.page_server import PageServer

QUIET = {name: 0 for name in NAMES}
# What the scrolled page counts: its two buttons and its banner.
SCROLLED_QUIET = {"distant": 0, "tall": 0, "cover": 0}


def received(target: str | None = None, *, times: int = 1, stray: int = 0, error: str | None = None, **others: int) -> Received:
    clicks = dict(QUIET)
    if target is not None:
        clicks[target] = times
    clicks.update({name.replace("_", "-"): number for name, number in others.items()})
    return Received(clicks, stray, error)


# --- the check that a click landed ----------------------------------------------------------------


@pytest.mark.parametrize("target", TARGETS)
def test_one_click_on_the_target_and_nothing_else_is_a_click_that_landed(target: str) -> None:
    assert landing_problems(received(target), target) == []
    assert_landed(received(target), target)


@pytest.mark.parametrize(
    ("attempt", "target", "named"),
    [
        (received(), "large", "large received 0 clicks"),
        (received("large", times=2), "large", "large received 2 clicks"),
        (received("decoy-left"), "small", "decoy-left received 1 clicks"),
        (received("small", decoy_right=1), "small", "decoy-right received 1 clicks"),
        (received("far", large=1), "far", "large received 1 clicks"),
        (received(stray=1), "small", "1 clicks reached no button"),
        (received("small", stray=1), "small", "1 clicks reached no button"),
        (received("large", error="element_obscured"), "large", "returned the error element_obscured"),
    ],
    ids=("none", "twice", "on-a-decoy", "target-and-decoy", "on-another-target", "on-no-button", "target-and-stray", "refused-by-the-server"),
)
def test_the_check_refuses_every_way_a_click_can_miss_or_overshoot(attempt: Received, target: str, named: str) -> None:
    assert any(named in problem for problem in landing_problems(attempt, target)), landing_problems(attempt, target)
    with pytest.raises(AssertionError, match=named):
        assert_landed(attempt, target)


def test_a_check_that_only_counts_the_clicks_of_the_target_would_take_a_click_on_a_decoy_for_a_hit() -> None:
    """The counter-proof of the cases above: the defective check passes where the real one refuses."""

    def counts_the_target_only(attempt: Received, target: str) -> list[str]:
        return [] if attempt.clicks.get(target, 0) >= 1 else [f"{target} received no click"]

    assert counts_the_target_only(received("small", decoy_right=1), "small") == []
    assert landing_problems(received("small", decoy_right=1), "small") != []


def refused(*, error: str | None = "element_obscured", stray: int = 0, **clicks: int) -> Received:
    return Received({**SCROLLED_QUIET, **clicks}, stray, error)


def test_a_click_refused_as_covered_with_nothing_clicked_is_a_refusal_as_expected() -> None:
    assert refusal_problems(refused(), "element_obscured") == []
    assert_refused(refused(), "element_obscured")


@pytest.mark.parametrize(
    ("attempt", "named"),
    [
        (refused(error=None, cover=1), "the click returned None"),
        (refused(error=None, tall=1), "tall received 1 clicks"),
        (refused(error="snapshot_stale"), "the click returned 'snapshot_stale'"),
        (refused(tall=1), "tall received 1 clicks"),
        (refused(cover=1), "cover received 1 clicks"),
        (refused(stray=1), "1 clicks reached no button"),
    ],
    ids=("accepted-and-landed-on-the-banner", "accepted-and-landed-on-the-target", "another-refusal", "refused-but-clicked-the-target", "refused-but-clicked-the-banner", "refused-but-clicked-the-page"),
)
def test_the_check_refuses_a_click_that_was_accepted_or_that_clicked_something_though_refused(attempt: Received, named: str) -> None:
    assert any(named in problem for problem in refusal_problems(attempt, "element_obscured")), refusal_problems(attempt, "element_obscured")
    with pytest.raises(AssertionError, match=named):
        assert_refused(attempt, "element_obscured")


# --- the page --------------------------------------------------------------------------------------


def fetch(server: PageServer, name: str) -> str:
    """The text of a page as the browser tests get it, over HTTP on the loopback address."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        connection.request("GET", f"/{name}")
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200
        assert response.getheader("Content-Type", "").startswith("text/html")
    finally:
        connection.close()
    return body.decode("utf-8")


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    with PageServer() as server:
        yield fetch(server, PAGE)


@pytest.fixture(scope="module")
def served_scrolled() -> Iterator[str]:
    with PageServer() as server:
        yield fetch(server, SCROLLED_PAGE)


def test_the_page_holds_what_the_click_tests_rely_on(served: str) -> None:
    assert page_problems(served) == []


def test_the_accessible_names_of_the_buttons_differ_from_each_other() -> None:
    assert len(set(NAMES.values())) == len(NAMES)
    assert set(TARGETS) <= set(NAMES)


def swapped(old: str, new: str) -> Callable[[str], str]:
    return lambda text: text.replace(old, new)


DEFECTS = {
    "no stray counter": (swapped("__stray_clicks", "__other_clicks"), "does not count its clicks"),
    "no counter of the buttons": (swapped("window.__clicks", "window.__counts"), "does not count its clicks"),
    "no listener on the document": (swapped('document.addEventListener("click"', 'document.addEventListener("keyup"'), "does not count its clicks"),
    "two buttons with one name": (swapped('aria-label="Left decoy"', 'aria-label="Right decoy"'), "buttons and their names"),
    "a button with no name": (swapped('aria-label="Small target"', 'aria-label=""'), "buttons and their names"),
    "a button more": (swapped("</body>", '<button id="extra" type="button">Extra</button></body>'), "buttons and their names"),
    "an address outside the page": (swapped("</body>", '<img src="https://example.invalid/x.png"></body>'), "outside itself"),
    "a large button too narrow": (swapped("top: 500px; width: 120px;", "top: 500px; width: 60px;"), "large button"),
    "a large button near the corner": (swapped("#large { left: 320px;", "#large { left: 100px;"), "large button"),
    "a large button placed by the view": (swapped("top: 500px;", "top: 50vh;"), "large button"),
    "a decoy of another size": (swapped("#decoy-right { left: 540px; top: 310px; width: 16px;", "#decoy-right { left: 540px; top: 310px; width: 20px;"), "16x16"),
    "a decoy on another line": (swapped("#decoy-left { left: 500px; top: 310px;", "#decoy-left { left: 500px; top: 330px;"), "one line"),
    "a decoy too close": (swapped("#small { left: 520px;", "#small { left: 518px;"), "4 pixels"),
    "a decoy too far": (swapped("#decoy-right { left: 540px;", "#decoy-right { left: 560px;"), "4 pixels"),
    "a far button within one view": (swapped("top: 250vh;", "top: 150vh;"), "far button"),
    "a far button in pixels": (swapped("top: 250vh;", "top: 2500px;"), "far button"),
    "a body with no height": (swapped("body { height: 300vh; }", ""), "body has no height"),
    "a body shorter than the far button is deep": (swapped("body { height: 300vh; }", "body { height: 200vh; }"), "body has no height"),
}


@pytest.mark.parametrize("defect", DEFECTS)
def test_the_page_check_refuses_a_page_that_has_the_defect(served: str, defect: str) -> None:
    change, named = DEFECTS[defect]
    broken = change(served)
    assert broken != served, "the variant is the page itself: the defect was not applied"
    assert any(named in problem for problem in page_problems(broken)), page_problems(broken)


def test_the_scrolled_page_holds_what_the_scrolled_click_tests_rely_on(served_scrolled: str) -> None:
    assert scrolled_page_problems(served_scrolled) == []


def test_the_accessible_names_of_the_scrolled_page_are_not_those_of_the_first_page() -> None:
    assert not set(SCROLLED_NAMES) & set(NAMES)
    assert not set(SCROLLED_NAMES.values()) & set(NAMES.values())


SCROLLED_DEFECTS = {
    "no stray counter": (swapped("__stray_clicks", "__other_clicks"), "does not count its clicks"),
    "no listener on the document": (swapped('document.addEventListener("click"', 'document.addEventListener("keyup"'), "does not count its clicks"),
    "no banner shown by the address": (swapped('location.hash === "#covered"', 'location.hash === "#other"'), "show its banner"),
    "a button more": (swapped("</body>", '<button id="extra" type="button">Extra</button></body>'), "buttons and their names"),
    "a button with no name": (swapped(">Tall target<", "><"), "buttons and their names"),
    "an address outside the page": (swapped("</body>", '<img src="https://example.invalid/x.png"></body>'), "outside itself"),
    "a body that is no larger than the view": (swapped("body { width: 4000px; height: 3000px; }", "body { width: 4000px; height: 700px; }"), "larger than the view"),
    "a body with no box of its own": (swapped("body { width: 4000px; height: 3000px; }", ""), "larger than the view"),
    "a banner shown from the start": (swapped("display: none;", "display: block;"), "banner is not fixed and hidden"),
    "a banner that scrolls with the page": (swapped("position: fixed;", "position: absolute;"), "banner is not fixed and hidden"),
}


@pytest.mark.parametrize("defect", SCROLLED_DEFECTS)
def test_the_scrolled_page_check_refuses_a_page_that_has_the_defect(served_scrolled: str, defect: str) -> None:
    change, named = SCROLLED_DEFECTS[defect]
    broken = change(served_scrolled)
    assert broken != served_scrolled, "the variant is the page itself: the defect was not applied"
    assert any(named in problem for problem in scrolled_page_problems(broken)), scrolled_page_problems(broken)
