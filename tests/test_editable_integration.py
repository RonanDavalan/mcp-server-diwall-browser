"""Les zones éditables (contenteditable de rôle textbox) rejouées contre un vrai navigateur de la famille Chromium."""

from __future__ import annotations

import pytest
from test_actions_integration import (  # noqa: F401 - les fixtures se déclarent par leur import
    Rig,
    browser_home,
    launched,
    mark_of,
    other_pages,
    pages,
    rig,
)

from mcp_server_diwall_browser.output import ToolError
from mcp_server_diwall_browser.perception import Snapshot

pytestmark = pytest.mark.browser

LONG_NOTE_LENGTH = 800
THREE_LINES = "first line\nsecond line\nthird line"


def element_of(snapshot: Snapshot, name: str):  # noqa: ANN201 - ElementRef n'est pas exporté pour les tests
    return next(e for e in snapshot.elements if e.name == name)


async def block_lines(rig: Rig, tab, selector: str) -> list[str]:  # noqa: ANN001
    """Les lignes de texte non vides d'une zone, telles que le navigateur les affiche."""
    async with rig.raw(tab) as raw:
        text = await raw.evaluate(f"document.querySelector({selector!r}).innerText")
    return [line for line in text.split("\n") if line]


async def sent_count(rig: Rig, tab) -> str:  # noqa: ANN001
    return next(line for line in await rig.tree(tab) if "Sent:" in line).strip()


async def test_only_a_textbox_with_contenteditable_is_editable(rig: Rig) -> None:
    _tab, snapshot = await rig.open("editable.html")
    assert element_of(snapshot, "Rich editor").editable is True
    assert element_of(snapshot, "Plain zone").editable is True
    assert element_of(snapshot, "Chat message").editable is True
    assert element_of(snapshot, "Fake textbox").editable is False
    assert element_of(snapshot, "Long note").editable is False
    assert element_of(snapshot, "Short field").editable is False


async def test_an_editable_zone_is_filled_and_read_back_in_one_line(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    outcome = await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Rich editor"), text="Bonjour à tous")
    assert outcome.typed_marker == "<typed>"
    fresh = await rig.observe(tab)
    assert element_of(fresh, "Rich editor").public["value"] == "Bonjour à tous"
    assert await block_lines(rig, tab, "#rich") == ["Bonjour à tous"]


async def test_filling_an_editable_zone_replaces_its_paragraphs(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    before = element_of(snapshot, "Rich editor").public["value"]
    assert before == "First paragraph Second paragraph"
    await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Rich editor"), text="alone")
    assert await block_lines(rig, tab, "#rich") == ["alone"]


async def test_a_plaintext_only_editable_zone_is_filled(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Plain zone"), text="new plain text")
    fresh = await rig.observe(tab)
    assert element_of(fresh, "Plain zone").public["value"] == "new plain text"
    assert await block_lines(rig, tab, "#plain") == ["new plain text"]


async def test_three_lines_in_an_editable_zone_send_nothing(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    assert await sent_count(rig, tab) == 'StaticText "Sent: 0"'
    await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Chat message"), text=THREE_LINES)
    assert await block_lines(rig, tab, "#chat") == THREE_LINES.split("\n")
    assert await sent_count(rig, tab) == 'StaticText "Sent: 0"'


async def test_three_lines_in_a_plaintext_only_zone_stay_three_lines(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Plain zone"), text=THREE_LINES)
    assert await block_lines(rig, tab, "#plain") == THREE_LINES.split("\n")


async def test_the_enter_key_alone_still_sends_in_the_chat_zone(rig: Rig) -> None:
    # Le contrôle que la page de test compte bien un envoi : sans lui, un compteur à 0 ne prouverait rien.
    tab, snapshot = await rig.open("editable.html")
    await rig.actions.press_key(tab.tab_id, "Enter", snapshot_id=snapshot.snapshot_id, mark=mark_of(snapshot, "Chat message"))
    assert await sent_count(rig, tab) == 'StaticText "Sent: 1"'
    assert element_of(snapshot, "Chat message").editable is True


async def test_a_secret_never_enters_an_editable_zone(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    assert element_of(snapshot, "Chat message").editable is True
    with pytest.raises(ToolError) as caught:
        await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Chat message"), credential_ref={"key": "password"})
    assert caught.value.code == "unsupported_element"
    assert "identifiant" in caught.value.message
    assert await block_lines(rig, tab, "#chat") == []


async def test_a_textbox_that_is_not_editable_is_still_refused(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    assert element_of(snapshot, "Fake textbox").editable is False
    with pytest.raises(ToolError) as caught:
        await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Fake textbox"), text="x")
    assert caught.value.code == "unsupported_element"
    assert await block_lines(rig, tab, "#fake") == ["not editable"]


async def test_the_value_of_a_textarea_is_read_in_full_and_an_input_keeps_its_cut(rig: Rig) -> None:
    _tab, snapshot = await rig.open("editable.html")
    assert len(element_of(snapshot, "Long note").public["value"]) == LONG_NOTE_LENGTH
    short = element_of(snapshot, "Short field").public["value"]
    assert len(short) == 120
    assert short.endswith("…")


async def test_the_value_of_an_editable_zone_stops_at_four_thousand_characters(rig: Rig) -> None:
    tab, snapshot = await rig.open("editable.html")
    await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Rich editor"), text="abcd " * 900)
    value = element_of(await rig.observe(tab), "Rich editor").public["value"]
    assert len(value) == 4000
    assert value.endswith("…")
