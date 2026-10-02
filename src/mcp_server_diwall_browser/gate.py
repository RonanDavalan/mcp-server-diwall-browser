"""The security gate: which actions need a human, and how fast the model may act.

Why this module exists:
    A page writes the text of its buttons and may lie to the model about
    what a click does. Whatever the model believes, a payment, an
    irreversible deletion or a change of secret must not leave without a
    human's consent; and a hostile page must not drive the model into an
    endless loop. Both rules are fixed here, in code, and nowhere else.
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from .operator_profile import Limits
from .output import ToolError

CATEGORIES = ("payment", "deletion", "secret_change")
INTENT_CATEGORIES = frozenset({"read", "edit", "submit", *CATEGORIES})

# Gestures the gate looks at. Filling a field, choosing an option, going to
# a page, scrolling and waiting are never locked.
EVALUATED_GESTURES = frozenset({"click", "enter_key", "dialog_accept"})

# Phrases that arm the gate, compared word by word after normalization. A
# false positive adds a confirmation, a false negative lets an action leave
# unconfirmed: the list leans towards arming.
VOCABULARY: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "payment": (
            "payer",
            "paiement",
            "regler",
            "reglement",
            "valider la commande",
            "confirmer la commande",
            "confirmer le paiement",
            "commander",
            "acheter",
            "faire un don",
            "pay",
            "pay now",
            "payment",
            "checkout",
            "place order",
            "confirm order",
            "buy now",
            "purchase",
            "donate",
        ),
        "deletion": (
            "supprimer definitivement",
            "supprimer le compte",
            "supprimer",
            "effacer",
            "vider la corbeille",
            "delete",
            "delete account",
            "delete permanently",
            "erase",
            "empty trash",
            "remove permanently",
        ),
        "secret_change": (
            "changer le mot de passe",
            "modifier le mot de passe",
            "reinitialiser le mot de passe",
            "regenerer la cle",
            "regenerer le jeton",
            "revoquer",
            "change password",
            "reset password",
            "regenerate key",
            "regenerate token",
            "rotate key",
            "revoke",
        ),
    }
)


def normalize(text: object) -> str:
    """Lower case, accents removed, anything but letters and digits made a space."""
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in plain).split())


_PHRASES: dict[str, tuple[tuple[str, ...], ...]] = {
    category: tuple(tuple(normalize(phrase).split()) for phrase in phrases) for category, phrases in VOCABULARY.items()
}


def phrase_in(phrase: tuple[str, ...], words: list[str]) -> bool:
    """Whole words in a row: ``commander`` is not found in ``recommander``."""
    size = len(phrase)
    return any(tuple(words[i : i + size]) == phrase for i in range(len(words) - size + 1))


def vocabulary_categories(texts: Iterable[object]) -> list[str]:
    found = []
    for text in texts:
        words = normalize(text).split()
        for category, phrases in _PHRASES.items():
            if category not in found and any(phrase_in(phrase, words) for phrase in phrases):
                found.append(category)
    return found


class GateElement(Protocol):
    """What the gate reads of a marked element (filled by the perception)."""

    name: str
    form_has_card_field: bool
    is_submit_like: bool
    form_backend_id: int | None

    @property
    def gate_texts(self) -> tuple[str, ...]: ...


class GateSnapshot(Protocol):
    page_has_card_field: bool


@dataclass(frozen=True)
class GateDecision:
    armed: bool
    categories: tuple[str, ...]
    signals: tuple[str, ...]


NOT_ARMED = GateDecision(armed=False, categories=(), signals=())


def classify(
    ref: GateElement | None,
    *,
    action_kind: str,
    intent_category: str | None,
    snapshot: GateSnapshot | None,
    dialog_message: str | None = None,
) -> GateDecision:
    """Whether an action needs a human's confirmation, and why.

    ``action_kind`` is the gesture: ``click``, ``enter_key`` (Enter on a
    marked element) or ``dialog_accept``; any other gesture is never
    evaluated. The model's declared category can arm the gate, never disarm
    it: every signal only adds.
    """
    if action_kind not in EVALUATED_GESTURES:
        return NOT_ARMED
    categories: list[str] = []
    signals: list[str] = []

    def add(category: str, signal: str) -> None:
        if category not in categories:
            categories.append(category)
        if signal not in signals:
            signals.append(signal)

    texts: tuple[object, ...]
    if action_kind == "dialog_accept":
        texts = (dialog_message,)
    else:
        texts = ref.gate_texts if ref is not None else ()
    for category in vocabulary_categories(texts):
        add(category, f"vocabulary_{category}")
    if snapshot is not None and snapshot.page_has_card_field:
        add("payment", "card_field_on_page")
    if ref is not None and action_kind != "dialog_accept":
        # Enter in any field of a form may submit it; a click submits only
        # through a submit control.
        submits = ref.is_submit_like or (action_kind == "enter_key" and ref.form_backend_id is not None)
        if ref.form_has_card_field and submits:
            add("payment", "card_field_in_form")
    if intent_category in CATEGORIES:
        add(intent_category, "declared_category")
    ordered = tuple(category for category in CATEGORIES if category in categories)
    return GateDecision(armed=bool(ordered), categories=ordered, signals=tuple(signals))


# --- Pace and ceilings ------------------------------------------------------------------------

ACTION_KINDS = frozenset({"open_tab", "navigate", "scroll", "click", "fill", "select_option", "press_key", "handle_dialog"})
PAGE_KINDS = frozenset({"open_tab", "navigate"})

_LIMIT_MESSAGE = (
    "le plafond de la session est atteint ({what}) : le serveur n'agit plus ; "
    "l'opérateur relance le serveur pour repartir, ou relève le plafond dans son profil"
)


class Pacer:
    """Minimal delay between two actions, and ceilings of actions and pages per process.

    The defaults hold with no profile at all: a protection that depends on an
    optional file is missing exactly where nobody wrote it. The limits are
    read again on every call, so a change of the profile applies at once.
    """

    def __init__(
        self,
        limits_provider: Callable[[], Limits] = Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._limits_provider = limits_provider
        self._clock = clock
        self._sleep = sleep
        self._actions = 0
        self._pages = 0
        self._last: float | None = None

    async def before_action(self, kind: str) -> None:
        """Count an attempt, or refuse it at a ceiling; then keep the pace."""
        if kind not in ACTION_KINDS:
            raise ValueError(f"not an action that counts: {kind!r}")
        limits = self._limits_provider()
        if self._actions >= limits.max_actions_per_session:
            raise self._refusal(limits, "actions")
        if kind in PAGE_KINDS and self._pages >= limits.max_pages_per_session:
            raise self._refusal(limits, "pages")
        self._actions += 1
        if kind in PAGE_KINDS:
            self._pages += 1
        if self._last is not None:
            wait = self._last + limits.min_action_delay_ms / 1000 - self._clock()
            if wait > 0:
                await self._sleep(wait)
        self._last = self._clock()

    def counters(self) -> dict[str, int]:
        return self._counters(self._limits_provider())

    def _counters(self, limits: Limits) -> dict[str, int]:
        return {
            "actions": self._actions,
            "pages": self._pages,
            "max_actions": limits.max_actions_per_session,
            "max_pages": limits.max_pages_per_session,
            "min_action_delay_ms": limits.min_action_delay_ms,
        }

    def _refusal(self, limits: Limits, what: str) -> ToolError:
        return ToolError(
            "limit_reached",
            _LIMIT_MESSAGE.format(what=what),
            action_expected="operator_restarts_or_raises_limit",
            extra_server={"counters": self._counters(limits)},
        )
