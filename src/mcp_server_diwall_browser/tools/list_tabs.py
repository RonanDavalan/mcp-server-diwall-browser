"""Outil list_tabs : les onglets que le serveur possède, par identifiant et origine seulement.

Pourquoi ce module existe :
    Le modèle doit savoir dans quels onglets il peut agir. Seuls sont listés les onglets que le serveur
    a ouverts, ou que leurs pages ont ouverts ; un autre onglet du navigateur, son titre et son adresse
    ne quittent jamais le serveur.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

    Never lists another tab of the browser, never gives a title or a full address.
    Errors: not_configured, browser_unreachable.
    """
    session = await ctx.browser()
    tabs = await session.registry.list_tabs()
    return ToolOutput(
        server={
            "tabs": [{"tab_id": info.tab_id, "origin": info.origin, "adopted": info.adopted} for info in tabs],
            "notices": session.registry.drain_notices(),
        }
    )
