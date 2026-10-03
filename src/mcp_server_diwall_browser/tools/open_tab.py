"""Tool open_tab: a new tab of the server, blank or on an http or https address.

Why this module exists:
    The server acts only in tabs it owns. Opening one is never locked, as
    going to see is never locked; it counts against the page ceiling, and
    only http and https addresses are opened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from ..browser import DialogBlocking, check_navigable, origin_label
from ..output import ToolOutput, tool
from ..server import ToolArguments, intent_field

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "open_tab"


class Arguments(ToolArguments):
    url: str | None = Field(default=None, description="http or https address to open; a blank tab without it.")
    intent: str = intent_field()


@tool(NAME)
async def run(ctx: ToolContext, *, url: str | None, intent: str) -> ToolOutput:
    """Open a new tab, blank or on an http or https address, and wait for its load (never for a quiet network).

    Returns tab_id, the origin the browser reports, and loaded (false when the load did not end in time).
    Does not read the page: observe does. Counts against the action and page ceilings.
    Errors: forbidden_scheme, navigation_failed, limit_reached, not_configured, browser_unreachable.
    """
    session = await ctx.browser()
    await ctx.pacer.before_action(NAME)
    if url is not None:
        check_navigable(url)
    registry = session.registry
    tab = await registry.open_tab()
    loaded = True
    if url is not None:
        loaded = (await registry.navigate(tab, url)).loaded
    server: dict[str, Any] = {"tab_id": tab.tab_id, "loaded": loaded}
    try:
        server["origin"] = origin_label(await registry.main_frame_info(tab, stop_on_dialog=True))
    except DialogBlocking:
        server["origin"] = None
        server["dialog_open"] = True
    server["notices"] = registry.drain_notices()
    return ToolOutput(server=server)
