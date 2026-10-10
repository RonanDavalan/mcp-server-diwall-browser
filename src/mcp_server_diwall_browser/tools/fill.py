"""Outil fill : taper un texte, ou un identifiant du répertoire chiffré, dans un champ repéré.

Pourquoi ce module existe :
    Un secret tapé par le modèle passe par son contexte et par celui du fournisseur. Un champ sensible
    n'est rempli que depuis le répertoire chiffré, lié à l'origine que le navigateur rapporte au moment
    de la frappe ; la valeur ne revient jamais dans aucun résultat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, model_validator

from ..output import ToolOutput, tool
from ..server import ToolArguments, intent_field

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "fill"


class CredentialReference(ToolArguments):
    key: Literal["username", "password"] = Field(description="Which value of the credential to type.")
    name: str | None = Field(default=None, max_length=64, description="Name of the credential file, when several exist.")


class Arguments(ToolArguments):
    tab_id: str = Field(description="A tab of this server, from open_tab or list_tabs.")
    snapshot_id: str = Field(description="The observation the mark comes from.")
    mark: int = Field(ge=1, description="The mark of the field, from that observation.")
    text: str | None = Field(default=None, description="Text for an ordinary field.")
    credential_ref: CredentialReference | None = Field(
        default=None, description="A credential of the encrypted directory, for a sensitive field."
    )
    intent: str = intent_field()

    @model_validator(mode="after")
    def _one_value(self) -> Arguments:
        if (self.text is None) == (self.credential_ref is None):
            raise ValueError("either text or credential_ref")
        return self


@tool(NAME)
async def run(
    ctx: ToolContext, *, tab_id: str, snapshot_id: str, mark: int, text: str | None, credential_ref: Any, intent: str
) -> ToolOutput:
    """Type into a marked text field of an allowed site: either text, or credential_ref for a credential.

    A sensitive field (password, card, code) refuses text: only credential_ref fills it, from the encrypted
    directory, and only on the origin the credential is bound to. The value is never returned; the result
    says <typed> or <secret:username>/<secret:password>. Refused at once while a dialog is open
    (element_obscured, action_expected handle_dialog).
    Errors: sensitive_value_refused, credential_not_bound, credential_ambiguous, credential_invalid,
    encrypted_dir_closed, unsupported_element, element_stale, snapshot_stale, origin_not_allowed.
    """
    reference = None
    if credential_ref is not None:
        reference = {key: value for key, value in dict(credential_ref).items() if value is not None}
    actions = await ctx.actions()
    outcome = await actions.fill(tab_id, snapshot_id, mark, text=text, credential_ref=reference, intent=intent)
    return ctx.outcome(outcome)
