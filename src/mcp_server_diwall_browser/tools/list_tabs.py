"""Tool list_tabs: the tabs the server owns, by identifier and origin only.

Why this module exists:
    The model needs to know which tabs it can act in. Only the tabs the
    server opened, or that their pages opened, are listed; another tab of
    the browser, its title and its address never leave the server.
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
