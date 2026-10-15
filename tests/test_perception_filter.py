"""The filtered observation (`region`, `contains`) on a simulated page with three landmarks.

The filter reads the tree after the sensitive fields were withheld: `contains` must never be a way to
probe the value of a password field. The last test is the counter-proof: a filter placed before the
withholding makes that same check fail.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fixtures.page_model import El, Page, button, div, field_input, link, text
from test_perception import ORIGIN, built, fake, fake_browser, perceiver, registry, serialized, showing  # noqa: F401 - fixtures

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.origin import parse_origin
from mcp_server_diwall_browser.perception import Box, Dom, build

PASSWORD_VALUE = "hunter2-probe-value"


def landmarks_page(*, password: str = PASSWORD_VALUE) -> Page:
    navigation = div(
        link("Home", (10, 10, 80, 20)),
        link("Pricing", (100, 10, 80, 20)),
        link("Contact", (190, 10, 80, 20)),
        box=(0, 0, 800, 40),
        role="navigation",
    )
    main = div(
        El("H1", role="heading", name="Welcome to the shop", box=(10, 50, 400, 30), properties={"level": 1}, children=[text("Welcome to the shop", (10, 50, 300, 20))]),
        text("Delivery within two days", (10, 90, 300, 16)),
        button("Order now", (10, 120, 120, 30)),
        field_input("Password", (10, 160, 200, 20), attrs={"type": "password"}, value=password),
        field_input("Comment", (10, 190, 200, 20), value="plain comment"),
        text("Returns accepted for thirty days", (10, 230, 300, 16)),
        box=(0, 40, 800, 320),
        role="main",
    )
    footer = div(
        text("Copyright 2026 Example", (10, 380, 300, 16)),
        link("Legal notice", (10, 400, 120, 20)),
        box=(0, 370, 800, 100),
        role="contentinfo",
    )
    return Page("Landmarks", [navigation, main, footer], viewport=(0, 0, 800, 600), content_size=(800, 600))


def run(page: Page, **kwargs: Any) -> perception.Built:
    x, y, width, height = page.viewport
    return build(page.ax_tree(), Dom(page.dom_snapshot()), Box(x, y, width, height), parse_origin(ORIGIN), **kwargs)


def tree_text(result: perception.Built) -> str:
    return "\n".join(result.tree_lines)


# --- region -----------------------------------------------------------------------------------------


def test_without_a_parameter_the_output_is_the_one_of_before() -> None:
    page = landmarks_page()
    plain = built(page)
    again = run(page, region=None, contains=None)
    assert again.tree_lines == plain.tree_lines
    assert [e.public for e in again.elements] == [e.public for e in plain.elements]
    assert again.counters == plain.counters
    assert "lines_before_filter" not in again.counters and "region_found" not in again.counters


def test_region_main_renders_neither_the_navigation_nor_the_footer() -> None:
    result = run(landmarks_page(), region="main")
    text_ = tree_text(result)
    assert 'main ""' in text_ and "Order now" in text_ and "Returns accepted" in text_
    for absent in ("Home", "Pricing", "Copyright", "Legal notice"):
        assert absent not in text_
    assert [e.public["name"] for e in result.elements] == ["Order now", "Password", "Comment"]
    assert result.counters["region_found"] is True
    assert result.counters["lines_after_filter"] < result.counters["lines_before_filter"]


def test_region_marks_are_numbered_from_one_among_those_that_remain() -> None:
    result = run(landmarks_page(), region="contentinfo")
    assert [(e.public["mark"], e.public["name"]) for e in result.elements] == [(1, "Legal notice")]
    assert result.tree_lines[0] == 'contentinfo ""'


def test_region_navigation_keeps_its_links_in_document_order() -> None:
    result = run(landmarks_page(), region="navigation")
    assert [(e.public["mark"], e.public["name"]) for e in result.elements] == [(1, "Home"), (2, "Pricing"), (3, "Contact")]


def test_a_region_the_page_does_not_have_gives_an_empty_tree_and_no_error() -> None:
    result = run(landmarks_page(), region="complementary")
    assert result.tree_lines == [] and result.elements == []
    assert result.counters["region_found"] is False
    assert result.counters["lines_after_filter"] == 0


def test_several_landmarks_of_the_same_name_are_all_rendered() -> None:
    page = landmarks_page()
    page.body.append(div(link("Terms", (10, 480, 80, 20)), box=(0, 470, 800, 60), role="navigation"))
    result = run(page, region="navigation")
    assert [e.public["name"] for e in result.elements] == ["Home", "Pricing", "Contact", "Terms"]


def test_the_page_keeps_its_card_field_flag_whatever_the_region() -> None:
    page = landmarks_page()
    page.body[2].children.append(field_input("Card number", (300, 400, 200, 20), attrs={"type": "text", "autocomplete": "cc-number"}))
    assert run(page, region="main").page_has_card_field is True


# --- contains ---------------------------------------------------------------------------------------


def test_contains_renders_the_line_its_ancestors_and_two_lines_of_context() -> None:
    result = run(landmarks_page(), contains="Order now")
    lines = result.tree_lines
    matched = next(i for i, line in enumerate(lines) if "Order now" in line and line.strip().startswith("button"))
    assert lines[0] == 'RootWebArea "Landmarks"'
    assert any(line.strip() == 'main ""' for line in lines[:matched])
    assert not any("Home" in line or "Copyright" in line for line in lines)
    full = built(landmarks_page()).tree_lines
    at = full.index(next(line for line in full if line.strip().startswith("button") and "Order now" in line))
    assert full[at - 2] in lines and full[at + 2] in lines
    assert full[at + 3] not in lines
    assert result.counters["lines_after_filter"] == len(lines)


def test_contains_compares_like_the_gate_lowercase_without_accents_with_spaces_reduced() -> None:
    names = ["Régler   la Facture", "Annuler", "Aide", "Contact", "Mentions", "Plan du site"]
    page = Page("Accents", [button(name, (10, 10 + 40 * n, 160, 30)) for n, name in enumerate(names)], content_size=(800, 600))
    result = run(page, contains="  regler la FACTURE ")
    assert [e.public["name"] for e in result.elements] == ["Régler la Facture", "Annuler", "Aide"]


def test_contains_with_no_match_renders_nothing() -> None:
    result = run(landmarks_page(), contains="zzz-not-on-the-page")
    assert result.tree_lines == [] and result.elements == []
    assert result.counters["lines_after_filter"] == 0


def test_an_empty_text_filters_nothing() -> None:
    page = landmarks_page()
    assert run(page, contains="   ").tree_lines == built(page).tree_lines


def test_region_and_contains_combine_inside_the_region() -> None:
    result = run(landmarks_page(), region="main", contains="returns accepted")
    text_ = tree_text(result)
    assert "Returns accepted" in text_ and 'main ""' in text_
    assert "Home" not in text_ and "Copyright" not in text_
    assert result.counters["region_found"] is True
    outside = run(landmarks_page(), region="navigation", contains="returns accepted")
    assert outside.tree_lines == [] and outside.counters["region_found"] is True


def probe_leaks(result: perception.Built) -> bool:
    """Vrai quand la sonde de la valeur d'un champ mot de passe a rendu quelque chose."""
    return bool(result.tree_lines or result.elements or result.counters["lines_after_filter"])


def test_contains_on_the_value_of_a_password_field_renders_nothing() -> None:
    result = run(landmarks_page(), contains=PASSWORD_VALUE)
    assert not probe_leaks(result)
    assert result.counters["redacted_fields"] == 0
    assert PASSWORD_VALUE not in json.dumps([e.public for e in result.elements] + result.tree_lines + [result.counters])


def test_contains_on_a_piece_of_the_password_value_renders_nothing_either() -> None:
    for fragment in ("hunter2", "probe", PASSWORD_VALUE.upper()):
        assert not probe_leaks(run(landmarks_page(), contains=fragment)), fragment


def test_the_name_of_a_sensitive_field_is_page_text_and_stays_searchable() -> None:
    result = run(landmarks_page(), contains="Password")
    assert any('textbox "Password"' in line for line in result.tree_lines)
    assert [e.public["value"] for e in result.elements if e.public["name"] == "Password"] == [""]


def test_the_counter_proof_a_filter_placed_before_the_withholding_fails_the_password_check() -> None:
    """Le contrôle précédent doit pouvoir échouer : un filtre qui lit la valeur brute, avant l'expurgation, le fait échouer."""

    def before_the_withholding(item: Any) -> str:
        node = item.dom
        raw = (node.input_value or "") if node.tag in perception.FIELD_TAGS else ""
        return f"{item.line or ''} {raw}"

    result = run(landmarks_page(), contains=PASSWORD_VALUE, filter_text=before_the_withholding)
    assert probe_leaks(result)
    assert any("Password" in line for line in result.tree_lines)


# --- plafonds ---------------------------------------------------------------------------------------


def test_the_ceilings_apply_after_the_filter() -> None:
    many = div(*[link(f"Item {n}", (4 + (n % 50) * 15, 60 + (n // 50) * 20, 12, 12)) for n in range(200)], box=(0, 50, 800, 400), role="main")
    head = div(*[link(f"Menu {n}", (4 + n * 15, 4, 12, 12)) for n in range(30)], box=(0, 0, 800, 40), role="navigation")
    page = Page("Many", [head, many])
    assert built(page).counters["marks_truncated"] is True
    result = run(page, region="navigation")
    assert result.counters["marks_truncated"] is False and len(result.elements) == 30


# --- par l'outil complet, sur le navigateur simulé -------------------------------------------------


async def test_observe_serves_the_filter_through_the_perception(fake_browser, registry) -> None:  # noqa: F811
    tab = await showing(fake_browser, registry, landmarks_page())
    percept = perceiver(registry)
    observation = await percept.observe(tab, image=False, region="main", contains="order now")
    assert observation.page_content is not None
    names = [e["name"] for e in observation.page_content["elements"]]
    assert names == ["Order now", "Password", "Comment"]
    assert not any("Returns accepted" in line or "Home" in line for line in observation.page_content["tree"])
    assert observation.server["counters"]["region_found"] is True
    assert PASSWORD_VALUE not in serialized(observation)
    snapshot = observation.snapshot
    assert snapshot is not None and [e.mark for e in snapshot.elements] == [1, 2, 3]
