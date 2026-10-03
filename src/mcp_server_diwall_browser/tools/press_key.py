"""Tool press_key: one key that moves or validates, Enter going through the gate.

Why this module exists:
    Enter submits a form as a click does, so it is checked like one: on a
    marked field, through the gate. The other keys only move, and are
    limited to a fixed list: no shortcut, no character.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ..output import ToolOutput, tool
from ..server import ToolArguments, intent_field

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "press_key"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    key: str = Field(description="Enter, Tab, Escape, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, PageUp, PageDown, Home or End.")
    snapshot_id: str | None = Field(default=None, description="With mark, required for Enter.")
    mark: int | None = Field(default=None, ge=1, description="The field Enter is pressed in, required for Enter.")
    intent: str = intent_field()


@tool(NAME)
async def run(
    ctx: ToolContext, *, tab_id: str, key: str, snapshot_id: str | None, mark: int | None, intent: str
) -> ToolOutput:
    """Press one key in a tab of an allowed site: Enter, Tab, Escape, the arrows, PageUp, PageDown, Home, End.

    Enter needs a mark: the field gets the focus after its identity is checked, and Enter in a payment
    form waits for the operator's confirmation, like a click. Other keys need no mark. No shortcut, no
    character typing. Refused at once while a dialog is open (element_obscured, action_expected
    handle_dialog). Errors: unsupported_element (with the allowed keys), element_stale, snapshot_stale,
    origin_not_allowed, confirmation_denied, confirmation_expired, confirmation_unavailable, limit_reached.
    """
    actions = await ctx.actions()
    outcome = await actions.press_key(tab_id, key, snapshot_id=snapshot_id, mark=mark, intent=intent)
    return ctx.outcome(outcome)
