"""Outil status : ce qui est configuré, ouvert, rattaché et autorisé, sans agir.

Pourquoi ce module existe :
    Avant toute chose, le modèle et l'opérateur doivent savoir ce qui bloque : un profil manquant,
    un répertoire chiffré fermé, un navigateur qui ne répond pas. Cet outil fonctionne même sans
    rien de configuré.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..cdp import CdpError
from ..mount import verdict_error
from ..operator_profile import ENV_VAR, standard_profile_path
from ..origin import entry_listed
from ..output import ToolError, ToolOutput, tool
from ..server import ToolArguments, server_version

if TYPE_CHECKING:
    from ..server import ToolContext

NAME = "status"

PROFILE_ENCRYPTED_NOTE = (
    "The browser profile path the operator declared lies in the mounted volume. This is what the profile "
    "says, not a proof of where the browser keeps its data."
)


class Arguments(ToolArguments):
    pass


@tool(NAME)
async def run(ctx: ToolContext) -> ToolOutput:
    """State of the server: profile, encrypted directory, browser, allowed sites, ceilings, confirmation.

    Works even when nothing is configured: then it says what is missing and the two ways to provide it.
    It changes nothing in the browser; it attaches to the browser if it is not attached yet.
    It never shows the content of the profile beyond the sites, their settings and the operator's declarations.
    """
    server: dict[str, Any] = {"server_version": server_version()}
    state = ctx.state
    try:
        profile = ctx.profile()
    except ToolError as exc:
        server["configured"] = False
        server["profile_error"] = {"code": exc.code, "message": exc.message}
        if exc.code == "not_configured":
            server["missing"] = "profile"
            server["how_to_provide"] = {
                "environment_variable": ENV_VAR,
                "standard_location": str(standard_profile_path()),
            }
        server["operations_not_journaled"] = state.operations_not_journaled
        server["counters"] = ctx.pacer.counters()
        return ToolOutput(server=server)

    server["configured"] = True
    verdict = state.deps.mount_checker.check(profile.secrets_dir, profile.secrets_crypt_dir)
    volume: dict[str, Any] = {"state": verdict.state.value}
    if verdict.error_code is not None:
        refusal = verdict_error(verdict)
        volume["code"] = refusal.code
        if refusal.action_expected:
            volume["action_expected"] = refusal.action_expected
    server["encrypted_directory"] = volume
    server["operations_not_journaled"] = state.operations_not_journaled

    notices: list[str] = []
    try:
        session = await ctx.browser()
    except (ToolError, CdpError, OSError) as exc:
        code = exc.code if isinstance(exc, ToolError) else "browser_unreachable"
        server["browser"] = {"attached": False, "code": code}
        if isinstance(exc, ToolError):
            server["browser"]["message"] = exc.message
    else:
        described = session.attachment.describe()
        server["browser"] = {"attached": True, "product": described["product"], "version": described["version"]}
        notices.extend(session.registry.drain_notices())
        notices.extend(session.downloads.drain_notices())
    server["profile_encrypted"] = state.volume(profile).holds(profile.browser.profile_dir)
    server["profile_encrypted_note"] = PROFILE_ENCRYPTED_NOTE

    server["allowed_origins"] = [
        {
            "origin": origin.canonical,
            "confirm_each_change": entry_listed(origin, profile.confirm_each_change),
            "local_model_only": origin in profile.local_model_origins,
        }
        for origin in profile.allowed_origins
    ]
    server["declarations"] = {
        "local_model_client": profile.declarations.local_model_client,
        "client_does_not_auto_confirm": profile.declarations.client_does_not_auto_confirm,
    }
    server["counters"] = ctx.pacer.counters()
    server["confirmation_channel"] = ctx.channel_name(profile)
    server["notices"] = notices
    return ToolOutput(server=server)
