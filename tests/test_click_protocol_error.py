"""Une erreur du protocole à la lecture de la boîte de la cible, sur le navigateur simulé.

La lecture de la boîte (``DOM.getContentQuads``) se fait avant le mouvement du pointeur, puis une
seconde fois avant la pression. Que l'une ou l'autre échoue, l'élément n'a plus de boîte à presser :
le clic est refusé en ``element_obscured``, aucun ``mousePressed`` n'est envoyé et le serveur ne
réessaie pas de lui-même.
"""

from __future__ import annotations

import pytest
from fixtures.fake_cdp import FakeCdpError
from fixtures.page_model import Page, button
from test_actions import Bench, bench, mark_of  # noqa: F401 - fixture

from mcp_server_diwall_browser.output import ToolError


def raise_at_call(bench: Bench, number: int) -> list[int]:
    """Faire lever une erreur du protocole au ``number``-ième appel de ``DOM.getContentQuads`` ; rend le compteur d'appels."""
    previous = bench.cdp.handlers["DOM.getContentQuads"]
    calls: list[int] = []

    def handler(params: dict[str, object], session_id: str | None) -> object:
        calls.append(1)
        if len(calls) == number:
            raise FakeCdpError(-32000, "texte témoin")
        return previous(params, session_id)

    bench.cdp.on("DOM.getContentQuads", handler=handler)
    return calls


@pytest.mark.parametrize("number", [1, 2])
async def test_a_protocol_error_reading_the_box_is_refused_as_obscured_and_nothing_is_pressed(bench: Bench, number: int) -> None:
    tab, snapshot = await bench.show(Page("Page", [button("Go", (100, 100, 120, 30))]))
    calls = raise_at_call(bench, number)
    with pytest.raises(ToolError) as caught:
        await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    assert caught.value.code == "element_obscured"
    assert "texte témoin" not in caught.value.message
    assert not any(params["type"] == "mousePressed" for _, params in bench.browser.mouse), bench.browser.mouse
    assert bench.browser.clicks == []
    # Pas de nouvel essai : la boîte n'est pas relue après l'erreur.
    assert len(calls) == number


async def test_the_server_does_not_retry_a_click_whose_box_could_not_be_read(bench: Bench) -> None:
    tab, snapshot = await bench.show(Page("Page", [button("Go", (100, 100, 120, 30))]))
    raise_at_call(bench, 1)
    with pytest.raises(ToolError):
        await bench.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Go"))
    assert bench.cdp.methods().count("DOM.getNodeForLocation") == 0
