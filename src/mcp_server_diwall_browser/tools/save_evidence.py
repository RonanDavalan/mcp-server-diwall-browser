"""Outil save_evidence : garder une capture masquée d'un onglet dans le répertoire chiffré.

Pourquoi ce module existe :
    L'opérateur peut vouloir une trace de ce qu'une page a montré, un reçu ou un numéro de
    confirmation. La capture a les mêmes masques qu'une observation et n'est écrite que dans le
    répertoire chiffré ouvert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from ..output import ToolOutput, tool
from ..server import ToolArguments

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "save_evidence"


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    label: str = Field(min_length=1, max_length=64, description="A short name, reduced to safe characters.")


@tool(NAME)
async def run(ctx: ToolContext, *, tab_id: str, label: str) -> ToolOutput:
    """Save a masked capture of a tab of an allowed site in the encrypted directory, as manual-<label>.png.

    Returns the path relative to the server's directory in the volume, never an absolute path.
    Sensitive fields and frames are masked as in observe. Errors: encrypted_dir_closed,
    capture_unavailable, origin_not_allowed, tab_not_owned.
    """
    actions = await ctx.actions()
    outcome = await actions.save_evidence(tab_id, label)
    return ctx.outcome(outcome)
