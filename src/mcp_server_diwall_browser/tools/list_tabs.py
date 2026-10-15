"""Outil list_tabs : les onglets que le serveur possède, par identifiant et origine seulement.

Pourquoi ce module existe :
    Le modèle doit savoir dans quels onglets il peut agir. Seuls sont listés les onglets que le serveur
    a ouverts, ou que leurs pages ont ouverts, et ceux qu'une exécution précédente a laissés ouverts (à
    fermer seulement) ; un autre onglet du navigateur, son titre et son adresse ne quittent jamais le
    serveur.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..output import ToolOutput, tool
from ..server import ToolArguments

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "list_tabs"


class Arguments(ToolArguments):
    pass


@tool(NAME)
async def run(ctx: ToolContext) -> ToolOutput:
    """List the tabs this server opened, and the windows their pages opened: tab_id, origin, adopted.

    A tab that an earlier run of the server left open comes with previous_session true and no adopted:
    it can only be closed (close_tab); every other tool refuses it with tab_not_owned.
    Never lists another tab of the browser, never gives a title or a full address.
    Errors: not_configured, browser_unreachable.
    """
    session = await ctx.browser()
    registry = session.registry
    tabs: list[dict[str, Any]] = [
        {"tab_id": info.tab_id, "origin": info.origin, "adopted": info.adopted} for info in await registry.list_tabs()
    ]
    tabs += [
        {"tab_id": info.tab_id, "origin": info.origin, "previous_session": True}
        for info in await registry.list_previous_tabs()
    ]
    return ToolOutput(server={"tabs": tabs, "notices": registry.drain_notices()})
