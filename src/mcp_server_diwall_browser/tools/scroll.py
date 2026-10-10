"""Outil scroll : déplacer la vue d'un onglet de quelques pixels, jusqu'à une extrémité, ou jusqu'à un élément repéré.

Pourquoi ce module existe :
    Aller voir n'est jamais verrouillé : défiler ne dépend d'aucune liste de sites et ne demande aucune
    confirmation. C'est tout de même un geste, il compte donc contre les plafonds, et un repère est
    contrôlé avant que la vue s'y déplace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from ..actions import MAX_SCROLL
from ..output import ToolOutput, tool
from ..server import ToolArguments

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "scroll"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    dy: int | None = Field(default=None, ge=-MAX_SCROLL, le=MAX_SCROLL, description="CSS pixels, positive downwards.")
    to: Literal["top", "bottom"] | None = Field(default=None, description="Scroll to the top or the bottom.")
    mark: int | None = Field(default=None, ge=1, description="Bring this marked element into view.")
    snapshot_id: str | None = Field(default=None, description="The observation the mark comes from.")

    @model_validator(mode="after")
    def _one_way(self) -> Arguments:
        if sum(value is not None for value in (self.dy, self.to, self.mark)) != 1:
            raise ValueError("exactly one of dy, to and mark")
        if (self.mark is None) != (self.snapshot_id is None):
            raise ValueError("snapshot_id goes with mark, and only with it")
        return self


@tool(NAME)
async def run(
    ctx: ToolContext, *, tab_id: str, dy: int | None, to: str | None, mark: int | None, snapshot_id: str | None
) -> ToolOutput:
    """Scroll a tab: by dy CSS pixels (-5000 to 5000), to the top or bottom, or to a marked element.

    Exactly one of dy, to and mark; mark goes with its snapshot_id. Never asks for a confirmation and
    depends on no list of sites. Refused at once while a dialog is open (element_obscured,
    action_expected handle_dialog). Errors: element_stale, snapshot_stale, limit_reached, tab_not_owned.
    """
    actions = await ctx.actions()
    outcome = await actions.scroll(tab_id, dy=dy, to=to, mark=mark, snapshot_id=snapshot_id)
    return ctx.outcome(outcome)
