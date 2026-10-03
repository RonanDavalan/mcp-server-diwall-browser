"""Tool click: one click on a marked element, through the security gate.

Why this module exists:
    A click is where an injected text or an error can spend money or delete
    data. The click goes to the element the model saw, lands only where the
    human sees it, and stops for a human confirmation when the gate says so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ..output import ToolOutput, tool
from ..server import ToolArguments, intent_field

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "click"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    snapshot_id: str = Field(description="The observation the mark comes from.")
    mark: int = Field(ge=1, description="The mark of the element, from that observation.")
    intent: str = intent_field()
    intent_category: Literal["read", "edit", "submit", "payment", "deletion", "secret_change"] | None = Field(
        default=None, description="What the click does; payment, deletion and secret_change ask the operator. It never removes a confirmation."
    )


@tool(NAME)
async def run(
    ctx: ToolContext, *, tab_id: str, snapshot_id: str, mark: int, intent: str, intent_category: str | None
) -> ToolOutput:
    """Click a marked element of an allowed site, after checking it is still the one observed.

    A payment, an irreversible deletion or a secret change waits for the operator's confirmation in a
    window of the server; no parameter can approve it. The click lands on what is drawn at that point.
    A download the click starts goes to the encrypted directory; one that a page starts more than half a
    second after a load ends is refused, and the operator downloads it.
    Refused at once while a dialog is open (element_obscured, action_expected handle_dialog).
    Errors: element_stale, snapshot_stale, element_obscured, origin_not_allowed, confirmation_denied,
    confirmation_expired, confirmation_unavailable, journal_unavailable, capture_unavailable, limit_reached.
    """
    actions = await ctx.actions()
    outcome = await actions.click(tab_id, snapshot_id, mark, intent=intent, intent_category=intent_category)
    return ctx.outcome(outcome)
