"""Outil close_tab : fermer un onglet que le serveur possède.

Pourquoi ce module existe :
    Un onglet que le serveur a ouvert peut être fermé par lui ; tout autre onglet du navigateur est
    celui de l'opérateur et n'est jamais touché.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ..output import ToolOutput, tool
from ..server import ToolArguments

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "close_tab"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")


@tool(NAME)
async def run(ctx: ToolContext, *, tab_id: str) -> ToolOutput:
    """Close a tab this server opened. Never closes another tab of the browser, nor the browser.

    Errors: tab_not_owned.
    """
    session = await ctx.browser()
    await session.registry.close_tab(tab_id)
    return ToolOutput(server={"tab_id": tab_id, "closed": True, "notices": session.registry.drain_notices()})
