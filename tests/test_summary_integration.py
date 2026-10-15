"""Le résumé d'un `<details>` reçoit un repère, et un clic dessus le déplie, sur un vrai navigateur."""

from __future__ import annotations

import pytest
from test_actions_integration import Rig, browser_home, launched, mark_of, other_pages, pages, rig  # noqa: F401 - fixtures

pytestmark = pytest.mark.browser

PAGE = "details.html"


async def test_the_summary_of_a_details_element_gets_a_mark(rig: Rig) -> None:
    tab = await rig.registry.open_tab(rig.pages.url(PAGE))
    observation = await rig.perception.observe(tab, image=False)
    assert observation.page_content is not None
    summary = [e for e in observation.page_content["elements"] if e["name"] == "Plus"]
    assert len(summary) == 1
    assert summary[0]["role"] == "DisclosureTriangle"
    assert "expanded" not in summary[0].get("state", [])


async def test_a_click_on_the_summary_makes_expanded_true(rig: Rig) -> None:
    tab, snapshot = await rig.open(PAGE)
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Plus"))
    observation = await rig.perception.observe(tab, image=False)
    assert observation.page_content is not None
    summary = next(e for e in observation.page_content["elements"] if e["name"] == "Plus")
    assert "expanded" in summary["state"]
    assert any('"Texte"' in line for line in observation.page_content["tree"])
