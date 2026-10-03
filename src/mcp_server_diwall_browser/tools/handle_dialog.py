"""Tool handle_dialog: dismiss or accept the dialog open in a tab.

Why this module exists:
    While a dialog is open the page answers nothing else. Dismissing it is
    always possible; accepting it may be the irreversible step itself, so
    its message goes through the gate like a button's text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ..output import ToolOutput, tool
from ..server import ToolArguments

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "handle_dialog"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    accept: bool = Field(description="false dismisses the dialog, true accepts it.")


@tool(NAME)
async def run(ctx: ToolContext, *, tab_id: str, accept: bool) -> ToolOutput:
    """Answer the dialog open in a tab: accept false dismisses it, always allowed; accept true accepts it.

    Accepting needs an allowed site. A dialog whose message is a payment, a deletion or a secret change
    is never accepted here: the answer is capture_unavailable, as no evidence can be taken while it is
    open. Dismiss it, or tell the operator to answer the dialog themselves.
    Errors: capture_unavailable, origin_not_allowed, unsupported_element (no dialog open), limit_reached.
    """
    actions = await ctx.actions()
    outcome = await actions.handle_dialog(tab_id, accept)
    return ctx.outcome(outcome)
