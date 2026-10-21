"""La porte de sécurité : quelles actions exigent un humain, et à quelle vitesse le modèle peut agir.

Pourquoi ce module existe :
    Une page écrit le texte de ses boutons et peut mentir au modèle sur ce que fait un clic. Quoi
    que croie le modèle, un paiement, une suppression irréversible ou un changement de secret ne
    doit pas partir sans le consentement d'un humain ; et une page hostile ne doit pas entraîner le
    modèle dans une boucle sans fin. Les deux règles sont fixées ici, dans le code, et nulle part ailleurs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from .confusables import fold
from .operator_profile import Limits
from .output import ToolError

CATEGORIES = ("payment", "deletion", "secret_change")
INTENT_CATEGORIES = frozenset({"read", "edit", "submit", *CATEGORIES})

# Gestes que la porte regarde. Remplir un champ, choisir une option, aller à une page, défiler et
# attendre ne sont jamais verrouillés.
EVALUATED_GESTURES = frozenset({"click", "enter_key", "dialog_accept"})

# Les boîtes de dialogue par lesquelles la page pose une question : l'accepter lui répond oui, que son message
# dise ou non ce que ce oui déclenche.
QUESTION_DIALOGS = frozenset({"confirm", "prompt"})

# Expressions qui arment la porte, comparées mot à mot après normalisation. Un faux positif ajoute
# une confirmation, un faux négatif laisse partir une action sans confirmation : la liste penche
# vers l'armement.
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
            "finaliser l'achat",
            "valider mon panier",
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
            "reset your password",
            "regenerate key",
            "regenerate api key",
            "regenerate token",
            "rotate key",
            "revoke",
        ),
    }
)


def normalize(text: object) -> str:
    """Texte plié (minuscules, sans accents, lettres latines), tout ce qui n'est pas lettre ni chiffre devenu une espace."""
    if not isinstance(text, str):
        return ""
    plain = fold(text)
    return " ".join("".join(char if char.isalnum() else " " for char in plain).split())


_PHRASES: dict[str, tuple[tuple[str, ...], ...]] = {
    category: tuple(tuple(normalize(phrase).split()) for phrase in phrases) for category, phrases in VOCABULARY.items()
}


def phrase_in(phrase: tuple[str, ...], words: list[str]) -> bool:
    """Des mots entiers à la suite : ``commander`` n'est pas trouvé dans ``recommander``."""
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
    """Ce que la porte lit d'un élément repéré (rempli par la perception)."""

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
    """``armed`` : une catégorie de la liste rouge, donc une preuve et un humain. ``asks`` : un humain sans preuve."""

    armed: bool
    categories: tuple[str, ...]
    signals: tuple[str, ...]
    asks: bool = False


NOT_ARMED = GateDecision(armed=False, categories=(), signals=())


def classify(
    ref: GateElement | None,
    *,
    action_kind: str,
    intent_category: str | None,
    snapshot: GateSnapshot | None,
    dialog_message: str | None = None,
    dialog_type: str | None = None,
) -> GateDecision:
    """Dit si une action exige la confirmation d'un humain, et pourquoi.

    ``action_kind`` est le geste : ``click``, ``enter_key`` (Entrée sur un élément repéré) ou
    ``dialog_accept`` (``dialog_type`` : ``confirm`` et ``prompt`` demandent un humain, quel que soit le message) ;
    tout autre geste n'est jamais évalué. La catégorie déclarée par le modèle peut
    armer la porte, jamais la désarmer : chaque signal ne fait qu'ajouter.
    """
    if action_kind not in EVALUATED_GESTURES:
        return NOT_ARMED
    categories: list[str] = []
    signals: list[str] = []

    def add_signal(signal: str) -> None:
        if signal not in signals:
            signals.append(signal)

    def add(category: str, signal: str) -> None:
        if category not in categories:
            categories.append(category)
        add_signal(signal)

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
        # Entrée dans n'importe quel champ d'un formulaire peut le soumettre ; un clic ne soumet que par
        # un contrôle de soumission.
        submits = ref.is_submit_like or (action_kind == "enter_key" and ref.form_backend_id is not None)
        if ref.form_has_card_field and submits:
            add("payment", "card_field_in_form")
    if intent_category in CATEGORIES:
        add(intent_category, "declared_category")
    ordered = tuple(category for category in CATEGORIES if category in categories)
    asks = action_kind == "dialog_accept" and dialog_type in QUESTION_DIALOGS
    if asks:
        add_signal("dialog_question")
    return GateDecision(armed=bool(ordered), categories=ordered, signals=tuple(signals), asks=asks)


# --- Rythme et plafonds -------------------------------------------------------------------

ACTION_KINDS = frozenset({"open_tab", "navigate", "scroll", "click", "fill", "select_option", "press_key", "handle_dialog"})
PAGE_KINDS = frozenset({"open_tab", "navigate"})

_LIMIT_MESSAGE = (
    "le plafond de la session est atteint ({what}) : le serveur n'agit plus ; "
    "l'opérateur relance le serveur pour repartir, ou relève le plafond dans son profil"
)


class Pacer:
    """Délai minimal entre deux actions, et plafonds d'actions et de pages par processus.

    Les valeurs par défaut tiennent sans aucun profil : une protection qui dépend d'un fichier
    facultatif manque exactement là où personne ne l'a écrit. Les limites sont relues à chaque appel,
    si bien qu'un changement du profil s'applique aussitôt.
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
        """Compter une tentative, ou la refuser à un plafond ; puis tenir le rythme."""
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
