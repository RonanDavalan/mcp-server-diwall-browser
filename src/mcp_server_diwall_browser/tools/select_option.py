"""Tool select_option: choose an option of a native list.

Why this module exists:
    Choosing an option changes a form but commits nothing: it is not
    locked, and asks for a confirmation only on a site where the operator
    wants one for every change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ..output import ToolOutput, tool
from ..server import ToolArguments, intent_field

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "select_option"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    snapshot_id: str = Field(description="The observation the mark comes from.")
    mark: int = Field(ge=1, description="The mark of the list, from that observation.")
    option: str = Field(description="The text or the value of the option.")
    intent: str = intent_field()


@tool(NAME)
async def run(ctx: ToolContext, *, tab_id: str, snapshot_id: str, mark: int, option: str, intent: str) -> ToolOutput:
    """Choose an option, by its text or its value, in a marked native list of an allowed site.

    Only a native list (select). Refused at once while a dialog is open (element_obscured,
    action_expected handle_dialog). Errors: option_not_found, unsupported_element, element_stale,
    snapshot_stale, origin_not_allowed.
    """
    actions = await ctx.actions()
    outcome = await actions.select_option(tab_id, snapshot_id, mark, option, intent=intent)
    return ctx.outcome(outcome)
