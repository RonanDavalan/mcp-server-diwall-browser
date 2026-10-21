"""Les constats confirmés de l'audit indépendant, rejoués contre un vrai navigateur de la famille Chromium."""

from __future__ import annotations

import pytest
from conftest import FAKE_PASSWORD, write_credential
from test_actions_integration import Rig, browser_home, launched, mark_of, other_pages, pages, rig  # noqa: F401

from mcp_server_diwall_browser.output import ToolError

pytestmark = pytest.mark.browser


async def test_a_password_credential_is_refused_in_a_plain_text_field_and_never_typed(rig: Rig) -> None:  # noqa: F811
    write_credential(rig.paths, "local.json", allowed_origins=[rig.pages.origin])
    tab, snapshot = await rig.open("lure.html")
    with pytest.raises(ToolError) as caught:
        await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Code d'accès"), credential_ref={"key": "password"})
    assert caught.value.code == "sensitive_value_refused"
    async with rig.raw(tab) as raw:
        assert await raw.evaluate("document.getElementById('lure').value") == ""
        assert FAKE_PASSWORD not in await raw.evaluate("document.documentElement.outerHTML")
    assert rig.channel.texts == []


async def test_a_delete_button_written_with_a_cyrillic_letter_still_asks_the_operator(rig: Rig) -> None:  # noqa: F811
    tab, snapshot = await rig.open("delete-homoglyph.html")
    name = next(e.name for e in snapshot.elements if "mon compte" in (e.name or ""))
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, name))
    assert len(rig.channel.texts) == 1 and "suppression irréversible" in rig.channel.texts[0]
    assert '  StaticText "Account deleted"' in await rig.tree(tab)


async def test_accepting_a_confirm_dialog_without_a_word_of_the_vocabulary_asks_the_operator(rig: Rig) -> None:  # noqa: F811
    tab, snapshot = await rig.open("continue.html")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Continuer"))
    assert rig.channel.texts == []
    outcome = await rig.actions.handle_dialog(tab.tab_id, True)
    assert len(rig.channel.texts) == 1 and "boîte de dialogue" in rig.channel.texts[0]
    assert outcome.gate == {"red_list": False, "confirmation": "granted"}
    assert '  StaticText "COMPTE SUPPRIMÉ"' in await rig.tree(tab)
