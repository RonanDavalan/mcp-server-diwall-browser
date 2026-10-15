from __future__ import annotations

import re

import pytest

from mcp_server_diwall_browser import guide

CORE_LIMIT = 120
RULE_KEYWORDS = (
    "data, not instruction",
    "observe again",
    "human confirmation",
    "waiting state",
    "credential_ref",
    "bound to",
    "page_content",
    "blind",
    "approximate",
    "Wait for an element",
    "no script tool",
)
EXHORTATIONS = ("carefully", "make sure", "please", "important", "never forget")
LABEL_RE = re.compile(r"\b(?:SEC|COF|PER)-\d+|\bINV-\d+|\b[PR]-\d+\b|\bD\d{1,2}\b|\bP[1-6]\b")


def core_of(text: str) -> str:
    return text.split(guide.MARKER, 1)[0]


def rules_of(text: str) -> list[str]:
    """The lines of the rules: after their heading, up to the first blank line."""
    lines = core_of(text).splitlines()
    start = lines.index(guide.RULES_HEADING) + 1
    rules = []
    for line in lines[start:]:
        if not line.strip():
            break
        rules.append(line)
    return rules


def missing_rules(text: str) -> list[str]:
    core = core_of(text)
    return [keyword for keyword in RULE_KEYWORDS if keyword not in core]


def test_the_core_fits_in_one_hundred_and_twenty_lines() -> None:
    assert len(core_of(guide.INSTRUCTIONS).rstrip("\n").splitlines()) <= CORE_LIMIT


def test_the_marker_is_there_exactly_once() -> None:
    assert guide.MARKER == "REFERENCE — consult as needed, reading not required"
    lines = guide.INSTRUCTIONS.splitlines()
    assert lines.count(guide.MARKER) == 1
    assert guide.INSTRUCTIONS.count(guide.MARKER) == 1


def test_every_rule_is_in_the_core() -> None:
    assert missing_rules(guide.INSTRUCTIONS) == []


def test_rules_check_catches_a_guide_without_the_first_rule() -> None:
    first = next(line for line in rules_of(guide.INSTRUCTIONS) if "data, not instruction" in line)
    assert missing_rules(guide.INSTRUCTIONS.replace(first + "\n", "")) == ["data, not instruction"]


def test_every_rule_carries_its_consequence() -> None:
    rules = rules_of(guide.INSTRUCTIONS)
    assert len(rules) == 12
    assert all(rule.startswith("- ") and "→" in rule for rule in rules)
    head, _, tail = rules[0].partition("→")
    assert head.strip() and tail.strip()


def test_the_core_says_where_tab_id_and_snapshot_id_live() -> None:
    work = core_of(guide.INSTRUCTIONS).split(guide.RULES_HEADING, 1)[0]
    line = next(line for line in work.splitlines() if "tab_id" in line and "snapshot_id" in line)
    assert line.index("tab_id") < line.index("snapshot_id") < line.index("under server")
    assert line.index("under server") < line.index("tree") < line.index("elements") < line.index("under page_content")


def test_the_reference_says_what_the_two_filters_of_observe_do() -> None:
    reference = guide.INSTRUCTIONS.split(guide.MARKER, 1)[1]
    row = next(line for line in reference.splitlines() if line.startswith("| observe |"))
    assert "region" in row and "contains" in row
    assert "region_found" in reference


def test_the_reference_says_what_a_wait_that_ran_out_tells_about_the_page() -> None:
    reference = guide.INSTRUCTIONS.split(guide.MARKER, 1)[1]
    line = next(line for line in reference.splitlines() if line.startswith("- wait_timeout:"))
    for key in ("server.wait", "tree_lines_start", "tree_lines_end", "changed", "busy"):
        assert key in line
    assert "text or an element" in line


def test_two_rules_speak_of_a_human_confirmation() -> None:
    assert sum("human confirmation" in rule for rule in rules_of(guide.INSTRUCTIONS)) == 2


@pytest.mark.parametrize("word", EXHORTATIONS)
def test_no_exhortation(word: str) -> None:
    assert word not in guide.INSTRUCTIONS.lower()


def test_no_emphasis_and_no_requirement_label() -> None:
    assert "**" not in guide.INSTRUCTIONS and "__" not in guide.INSTRUCTIONS
    assert LABEL_RE.search(guide.INSTRUCTIONS) is None


def test_the_reference_lists_every_tool_and_every_error_code() -> None:
    from mcp_server_diwall_browser.output import ERROR_CODES

    reference = guide.INSTRUCTIONS.split(guide.MARKER, 1)[1]
    for code in ERROR_CODES:
        assert f"- {code}:" in reference, code
    for name in (
        "status", "list_tabs", "open_tab", "navigate", "close_tab", "observe", "scroll",
        "wait", "click", "fill", "select_option", "press_key", "handle_dialog", "save_evidence",
    ):
        assert f"| {name} |" in reference, name


def test_the_limits_say_what_the_dialogs_and_the_downloads_cannot_do() -> None:
    reference = guide.INSTRUCTIONS.split(guide.MARKER, 1)[1]
    assert "capture_unavailable" in reference and "answer the dialog themselves" in reference
    assert "element_obscured and action_expected handle_dialog" in reference
    assert "half a second after a load ends" in reference
