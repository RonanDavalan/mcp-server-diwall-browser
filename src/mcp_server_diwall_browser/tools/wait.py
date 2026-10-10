"""Outil wait : attendre le chargement, un texte ou un élément, jamais un réseau calme.

Pourquoi ce module existe :
    Les pages qui interrogent sans cesse ne deviennent jamais calmes, et un délai fixe est soit trop
    court soit gaspillé. Attendre sert à quelque chose que la page montre ; textes et éléments ne sont
    lus que sur un site autorisé.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, model_validator

from ..output import ToolOutput, tool
from ..perception import DEFAULT_WAIT_S, MAX_WAIT_S
from ..server import ToolArguments

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "wait"


class ElementQuery(ToolArguments):
    role: str = Field(description="Accessibility role, as in the tree.")
    name: str = Field(description="Accessible name, as in the tree.")


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    for_: Literal["load", "text", "element"] = Field(alias="for", description="load, text or element.")
    value: str | ElementQuery | None = Field(
        default=None, description="The text for text; {role, name} for element; nothing for load."
    )
    timeout_s: float = Field(default=DEFAULT_WAIT_S, gt=0, le=MAX_WAIT_S, description="At most 60 seconds.")

    @model_validator(mode="after")
    def _value_fits(self) -> Arguments:
        expected = {"load": type(None), "text": str, "element": ElementQuery}[self.for_]
        if not isinstance(self.value, expected):
            raise ValueError("value does not fit what is waited for")
        return self


@tool(NAME)
async def run(ctx: ToolContext, *, tab_id: str, for_: str, value: Any, timeout_s: float) -> ToolOutput:
    """Wait until a tab has loaded, shows a text, or shows an element with a role and a name.

    Polls the page every 300 ms up to timeout_s (at most 60 s); never waits for a quiet network.
    Text and element are read only on an allowed site. Returns met, and dialog_open when a dialog
    interrupted the wait. Errors: wait_timeout, origin_not_allowed, tab_not_owned.
    """
    session = await ctx.browser()
    tab = session.registry.get(tab_id)
    if isinstance(value, dict):
        value = {"role": value["role"], "name": value["name"]}
    result = await session.perception.wait_for(tab, kind=for_, value=value, timeout_s=timeout_s)
    return ToolOutput(
        server={
            "tab_id": tab.tab_id,
            "met": result.met,
            "dialog_open": result.dialog_open,
            "notices": session.registry.drain_notices(),
        }
    )
