"""Une page qui échange ce qui reçoit le geste (le bouton sous le pointeur, l'élément qui a le focus), contre un vrai navigateur : le geste n'atteint jamais l'autre bouton."""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import write_credential
from test_actions_integration import Rig, browser_home, launched, other_pages, pages, rig  # noqa: F401

from mcp_server_diwall_browser.operator_profile import Limits
from mcp_server_diwall_browser.output import ToolError

pytestmark = pytest.mark.browser

ATTEMPTS = 300


async def counts(raw: object) -> tuple[int, int]:
    value = await raw.evaluate("[window.__audit.innocent, window.__audit.evil]")  # type: ignore[attr-defined]
    return value[0], value[1]


async def test_a_page_swapping_the_button_under_the_pointer_never_receives_the_click(rig: Rig) -> None:  # noqa: F811
    rig.context.settle_start_s = 0.02
    rig.profile = replace(rig.profile, limits=Limits(max_actions_per_session=ATTEMPTS + 10))
    tab = await rig.registry.open_tab(rig.pages.url("swap.html"))
    innocent = evil = refused = 0
    async with rig.raw(tab) as raw:
        for _attempt in range(ATTEMPTS):
            await raw.evaluate("window.__reset()")
            snapshot = await rig.observe(tab)
            mark = next(e.mark for e in snapshot.elements if e.name == "Continuer")
            before = await counts(raw)
            try:
                await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark)
            except ToolError as error:
                assert error.code == "element_obscured", error.message
                refused += 1
            after = await counts(raw)
            evil += after[1] - before[1]
            innocent += after[0] - before[0]
    assert evil == 0, f"{evil} clicks reached the other button ({innocent} reached the right one, {refused} refused)"
    assert innocent >= 10, f"only {innocent} clicks were delivered ({refused} refused): the check refuses everything"


async def test_a_page_moving_the_focus_to_another_button_never_receives_the_enter_key(rig: Rig) -> None:  # noqa: F811
    rig.context.settle_start_s = 0.02
    rig.profile = replace(rig.profile, limits=Limits(max_actions_per_session=ATTEMPTS + 10))
    tab = await rig.registry.open_tab(rig.pages.url("focus-swap.html"))
    benign = evil = refused = 0
    async with rig.raw(tab) as raw:
        for _attempt in range(ATTEMPTS):
            await raw.evaluate("window.__reset()")
            snapshot = await rig.observe(tab)
            mark = next(e.mark for e in snapshot.elements if e.name == "Recherche")
            before = await raw.evaluate("[window.__audit.evil, window.__audit.benign]")
            try:
                await rig.actions.press_key(tab.tab_id, "Enter", snapshot_id=snapshot.snapshot_id, mark=mark)
            except ToolError as error:
                assert error.code == "element_obscured", error.message
                refused += 1
            after = await raw.evaluate("[window.__audit.evil, window.__audit.benign]")
            evil += after[0] - before[0]
            benign += after[1] - before[1]
    assert evil == 0, f"{evil} Enter keys activated the other button ({benign} reached the form, {refused} refused)"
    assert benign >= 10, f"only {benign} keys were delivered ({refused} refused): the check refuses everything"


async def test_a_page_moving_the_focus_to_a_plain_field_never_receives_the_password(rig: Rig) -> None:  # noqa: F811
    write_credential(rig.paths, "local.json", allowed_origins=[rig.pages.origin])
    rig.context.settle_start_s = 0.02
    rig.profile = replace(rig.profile, limits=Limits(max_actions_per_session=ATTEMPTS + 10))
    tab = await rig.registry.open_tab(rig.pages.url("fill-swap.html"))
    leaked = typed = refused = 0
    async with rig.raw(tab) as raw:
        for _attempt in range(ATTEMPTS):
            await raw.evaluate("window.__reset()")
            snapshot = await rig.observe(tab)
            mark = next(e.mark for e in snapshot.elements if e.name == "Mot de passe")
            try:
                await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark, credential_ref={"key": "password"})
            except ToolError as error:
                assert error.code == "element_obscured", error.message
                refused += 1
            if await raw.evaluate("document.getElementById('leak').value"):
                leaked += 1
            elif await raw.evaluate("document.getElementById('benign').value"):
                typed += 1
    assert leaked == 0, f"the password reached the plain field {leaked} times ({typed} typed in the right one, {refused} refused)"
    assert typed >= 10, f"the password was typed only {typed} times ({refused} refused): the check refuses everything"
