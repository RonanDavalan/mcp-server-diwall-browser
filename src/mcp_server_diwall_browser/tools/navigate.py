"""Tool navigate: load an http or https address in a tab of the server.

Why this module exists:
    Going to see is never locked: any site can be loaded, only actions are
    bound to the operator's list of sites. The scheme is checked, the load
    is waited for, never a quiet network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from ..browser import DialogBlocking, origin_label
from ..output import ToolOutput, tool
from ..server import ToolArguments, intent_field

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "navigate"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    url: str = Field(description="http or https address to load.")
    intent: str = intent_field()


@tool(NAME)
async def run(ctx: ToolContext, *, tab_id: str, url: str, intent: str) -> ToolOutput:
    """Load an http or https address in a tab of this server and wait for its load.

    Returns the origin the browser reports and loaded (false when the load did not end in time).
    Every mark of the tab becomes stale. Counts against the action and page ceilings.
    Refused at once while a dialog is open (element_obscured, action_expected handle_dialog).
    Errors: forbidden_scheme, navigation_failed, tab_not_owned, limit_reached.
    """
    session = await ctx.browser()
    await ctx.pacer.before_action(NAME)
    registry = session.registry
    tab = registry.get(tab_id)
    registry.ensure_no_dialog(tab)
    result = await registry.navigate(tab, url)
    server: dict[str, Any] = {"tab_id": tab.tab_id, "loaded": result.loaded}
    try:
        server["origin"] = origin_label(await registry.main_frame_info(tab, stop_on_dialog=True))
    except DialogBlocking:
        server["origin"] = None
        server["dialog_open"] = True
    server["notices"] = registry.drain_notices()
    return ToolOutput(server=server)
