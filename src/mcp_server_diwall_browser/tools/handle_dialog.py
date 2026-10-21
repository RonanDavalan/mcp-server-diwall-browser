"""Outil handle_dialog : fermer ou accepter la boîte de dialogue ouverte dans un onglet.

Pourquoi ce module existe :
    Tant qu'une boîte de dialogue est ouverte, la page ne répond à rien d'autre. La fermer est
    toujours possible ; l'accepter peut être l'étape irréversible elle-même, si bien que son message
    passe par la porte comme le texte d'un bouton.
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

    Accepting needs an allowed site, and asks the operator in a window when the dialog is a confirm or a
    prompt, whatever its message says. A dialog whose message is a payment, a deletion or a secret change
    is never accepted here: the answer is capture_unavailable, as no evidence can be taken while it is
    open. Dismiss it, or tell the operator to answer the dialog themselves.
    Errors: capture_unavailable, origin_not_allowed, unsupported_element (no dialog open), confirmation_denied,
    confirmation_expired, confirmation_unavailable, limit_reached.
    """
    actions = await ctx.actions()
    outcome = await actions.handle_dialog(tab_id, accept)
    return ctx.outcome(outcome)
