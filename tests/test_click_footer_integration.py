"""Vingt clics sur un lien de pied de page sous la vue, chacun dans un onglet neuf, sur un vrai navigateur.

Le lien exige un défilement avant le clic. Un clic dont la lecture de la boîte échoue avant le mouvement
du pointeur ne doit jamais sortir en ``internal_error`` : il est refusé en ``element_obscured`` sans rien
presser, ou il atterrit sur le lien.
"""

from __future__ import annotations

from collections import Counter

import pytest
from fixtures.raw_cdp import RawSession
from test_actions_integration import Rig, browser_home, launched, mark_of, other_pages, pages, rig  # noqa: F401 - fixtures

from mcp_server_diwall_browser.output import ToolError

pytestmark = pytest.mark.browser

PAGE = "click-footer.html"
ATTEMPTS = 20


async def one_click(rig: Rig) -> tuple[str, int]:
    """Le résultat d'un clic dans un onglet neuf, et le nombre de clics reçus par le lien."""
    tab = await rig.registry.open_tab(rig.pages.url(PAGE))
    async with rig.raw(tab) as raw:
        snapshot = await rig.observe(tab)
        outcome = "landed"
        try:
            await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Footer link"))
        except ToolError as caught:
            outcome = caught.code
        except Exception as caught:  # noqa: BLE001 - une exception qui sort est un internal_error pour l'appelant
            outcome = f"internal_error ({type(caught).__name__})"
        received = await _received(raw)
    return outcome, received


async def _received(raw: RawSession) -> int:
    return int(await raw.evaluate("window.__clicks"))


async def test_twenty_clicks_on_a_footer_link_under_the_view_never_end_in_an_internal_error(rig: Rig) -> None:
    outcomes: Counter[str] = Counter()
    landed = 0
    for _ in range(ATTEMPTS):
        outcome, received = await one_click(rig)
        outcomes[outcome] += 1
        landed += received
        assert (outcome == "landed") == (received == 1), (outcome, received)
    print(f"\nclick-footer: {ATTEMPTS} clicks -> {dict(outcomes)}, link received {landed}")
    assert not [name for name in outcomes if name.startswith("internal_error")], dict(outcomes)
