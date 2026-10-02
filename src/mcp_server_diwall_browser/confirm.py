"""Ask the operator, in a window the server opens itself, before an armed action.

Why this module exists:
    The MCP client may approve a request on its own, and the model writes
    what it wants in the parameters of a tool. A confirmation that the
    model, the client or a page can answer is no confirmation: the window is
    opened by the server on the operator's desktop, its text is built by the
    server from what it read in the browser, and only the operator can
    press its button.
"""

from __future__ import annotations

import asyncio
import enum
import os
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .output import ToolError, bounded_text

WINDOW_TITLE = "Confirmation du serveur MCP"
GRANT_LABEL = "Autoriser"
DENY_LABEL = "Refuser"
TIMEOUT_S = 90.0
HELP_TIMEOUT_S = 5.0
NAME_LIMIT = 120
MAX_AMOUNTS = 3

GESTURES = {
    "click": "clic",
    "enter_key": "touche Entrée",
    "dialog_accept": "acceptation de la boîte de dialogue",
    "fill": "saisie dans un champ",
    "select_option": "choix d'une option",
}
CATEGORY_LABELS = {"payment": "paiement", "deletion": "suppression irréversible", "secret_change": "changement de secret"}
REASONS = frozenset({"armed", "operator_setting"})

_NUMBER = r"\d+(?:[   .,']\d+)*"
_CURRENCY = r"(?:[€$£]|\b(?:EUR|USD|GBP|CHF)\b)"
_AMOUNT_RE = re.compile(rf"{_CURRENCY}\s?{_NUMBER}|{_NUMBER}\s?{_CURRENCY}")


class ConfirmationDecision(enum.Enum):
    GRANTED = "granted"
    DENIED = "denied"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ConfirmRequest:
    """What the window says. Every field comes from the browser or the server, none from the model."""

    origin: str
    tool: str
    gesture: str
    element_role: str
    element_name: str
    form_target_origin: str | None
    amounts: tuple[str, ...]
    categories: tuple[str, ...]
    signals: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.gesture not in GESTURES:
            raise ValueError(f"unknown gesture: {self.gesture!r}")
        if self.reason not in REASONS:
            raise ValueError(f"unknown reason: {self.reason!r}")


def find_amounts(text: object, limit: int = MAX_AMOUNTS) -> tuple[str, ...]:
    """Numbers next to a currency sign or code: a heuristic, said as such in the window."""
    if not isinstance(text, str):
        return ()
    found: list[str] = []
    for match in _AMOUNT_RE.finditer(text):
        amount = bounded_text(match.group(0), 32)
        if amount and amount not in found:
            found.append(amount)
        if len(found) == limit:
            break
    return tuple(found)


def build_text(request: ConfirmRequest) -> str:
    """The French text of the window, built from the state read in the browser at this moment."""
    name = bounded_text(request.element_name, NAME_LIMIT)
    role = bounded_text(request.element_role, 40)
    lines = [
        f"Origine : {request.origin}",
        f"Outil : {request.tool} — geste : {GESTURES[request.gesture]}",
        f'Texte tiré de la page : {role} "{name}"',
    ]
    if request.form_target_origin:
        lines.append(f"Cible du formulaire : {request.form_target_origin}")
    if request.amounts:
        amounts = ", ".join(bounded_text(amount, 32) for amount in request.amounts[:MAX_AMOUNTS])
        lines.append(f"Montants détectés (heuristique) : {amounts}")
    if request.categories:
        lines.append("Catégories : " + ", ".join(CATEGORY_LABELS.get(c, c) for c in request.categories))
    if request.signals:
        lines.append("Signaux : " + ", ".join(request.signals))
    if request.reason == "operator_setting":
        lines.append("Demandé par le réglage de l'opérateur pour cette origine (confirm_each_change).")
    return "\n".join(lines)


class Channel(Protocol):
    def available(self) -> bool: ...

    async def ask(self, text: str) -> ConfirmationDecision: ...


Spawn = Callable[..., Awaitable[Any]]


class NativeDialogChannel:
    """A kdialog or zenity window, started without a shell, that the server waits for and kills."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        spawn: Spawn = asyncio.create_subprocess_exec,
        environ: Mapping[str, str] = os.environ,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._which = which
        self._spawn = spawn
        self._environ = environ
        self._timeout_s = timeout_s

    def _tool(self) -> tuple[str, str] | None:
        for name in ("kdialog", "zenity"):
            path = self._which(name)
            if path:
                return name, path
        return None

    def available(self) -> bool:
        has_display = bool(self._environ.get("WAYLAND_DISPLAY") or self._environ.get("DISPLAY"))
        return has_display and self._tool() is not None

    async def ask(self, text: str) -> ConfirmationDecision:
        tool = self._tool()
        if tool is None or not self.available():
            return ConfirmationDecision.UNAVAILABLE
        name, path = tool
        arguments = await self._command(name, path, text)
        try:
            process = await self._spawn(
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=dict(self._environ),
            )
        except OSError:
            return ConfirmationDecision.UNAVAILABLE
        try:
            code = await asyncio.wait_for(process.wait(), self._timeout_s)
        except TimeoutError:
            await _kill(process)
            return ConfirmationDecision.EXPIRED
        except BaseException:
            await _kill(process)
            raise
        if code == 0:
            return ConfirmationDecision.GRANTED
        if name == "zenity" and code == 5:
            return ConfirmationDecision.EXPIRED
        return ConfirmationDecision.DENIED

    async def _command(self, name: str, path: str, text: str) -> list[str]:
        if name == "kdialog":
            command = [path, "--title", WINDOW_TITLE, "--yes-label", GRANT_LABEL, "--no-label", DENY_LABEL]
            if await self._help_mentions(path, ["--help"], "--defaultno"):
                command.append("--defaultno")
            # kdialog reads its text as rich text: what the page wrote is shown, never interpreted.
            return [*command, "--yesno", _escape_markup(text)]
        command = [
            path,
            "--question",
            f"--title={WINDOW_TITLE}",
            "--text=" + text,
            f"--ok-label={GRANT_LABEL}",
            f"--cancel-label={DENY_LABEL}",
            "--no-markup",
            f"--timeout={int(self._timeout_s)}",
        ]
        if await self._help_mentions(path, ["--help-question"], "--default-cancel"):
            command.append("--default-cancel")
        return command

    async def _help_mentions(self, path: str, arguments: list[str], option: str) -> bool:
        try:
            process = await self._spawn(
                path,
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=dict(self._environ),
            )
        except OSError:
            return False
        try:
            output, _ = await asyncio.wait_for(process.communicate(), HELP_TIMEOUT_S)
        except TimeoutError:
            await _kill(process)
            return False
        return option.encode() in (output or b"")


async def _kill(process: Any) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


_MARKUP_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})


def _escape_markup(text: str) -> str:
    return text.translate(_MARKUP_ESCAPES)


Elicit = Callable[[str], Awaitable[bool | None]]


class ElicitationChannel:
    """The client's own confirmation, used only when the operator declared it does not auto-approve."""

    def __init__(self, elicit: Elicit, *, announced: bool, timeout_s: float = TIMEOUT_S) -> None:
        self._elicit = elicit
        self._announced = announced
        self._timeout_s = timeout_s

    def available(self) -> bool:
        return self._announced

    async def ask(self, text: str) -> ConfirmationDecision:
        if not self._announced:
            return ConfirmationDecision.UNAVAILABLE
        try:
            answer = await asyncio.wait_for(self._elicit(text), self._timeout_s)
        except TimeoutError:
            return ConfirmationDecision.EXPIRED
        except Exception:  # noqa: BLE001 - a failed request is no consent
            return ConfirmationDecision.UNAVAILABLE
        if answer is True:
            return ConfirmationDecision.GRANTED
        if answer is False:
            return ConfirmationDecision.DENIED
        return ConfirmationDecision.UNAVAILABLE


class UnavailableChannel:
    def available(self) -> bool:
        return False

    async def ask(self, text: str) -> ConfirmationDecision:
        return ConfirmationDecision.UNAVAILABLE


class _Declarations(Protocol):
    client_does_not_auto_confirm: bool


class _ProfileWithDeclarations(Protocol):
    @property
    def declarations(self) -> _Declarations: ...


def choose_channel(
    profile: _ProfileWithDeclarations, native: Channel | None, elicitation: Channel | None
) -> Channel:
    """The native window if it can be shown; the client's confirmation only on the operator's declaration."""
    if native is not None and native.available():
        return native
    if elicitation is not None and profile.declarations.client_does_not_auto_confirm and elicitation.available():
        return elicitation
    return UnavailableChannel()


async def request_confirmation(channel: Channel, request: ConfirmRequest) -> ConfirmationDecision:
    """Ask once, for this action only; nothing of the answer is kept for another one."""
    if not channel.available():
        return ConfirmationDecision.UNAVAILABLE
    return await channel.ask(build_text(request))


_REFUSALS = {
    ConfirmationDecision.DENIED: ("confirmation_denied", "l'opérateur a refusé l'action : rien n'a été exécuté", None),
    ConfirmationDecision.EXPIRED: (
        "confirmation_expired",
        "la confirmation n'a pas été donnée dans le délai : rien n'a été exécuté",
        None,
    ),
    ConfirmationDecision.UNAVAILABLE: (
        "confirmation_unavailable",
        "aucune fenêtre de confirmation ne peut être ouverte (kdialog ou zenity et un affichage sont requis) : "
        "rien n'a été exécuté",
        "operator_installs_kdialog_or_zenity",
    ),
}


def refusal(decision: ConfirmationDecision, *, extra_server: Mapping[str, Any] | None = None) -> ToolError:
    """The refusal that matches a decision other than granted."""
    code, message, action = _REFUSALS[decision]
    return ToolError(code, message, action_expected=action, extra_server=dict(extra_server or {}))
