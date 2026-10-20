"""Décider si une origine est lue et agie : la liste du profil, et la politique que l'opérateur a choisie.

Pourquoi ce module existe :
    La liste des sites autorisés borne ce que le modèle lit et ce sur quoi il agit, jamais la navigation. Trois
    politiques déplacent cette frontière : ``strict`` (la liste seule), ``discovery`` (l'opérateur répond dans une
    fenêtre à chaque origine nouvelle) et ``open`` (toute origine publique). Cette décision vit ici, à un seul
    endroit : la perception et les actions l'appellent et ne comparent jamais une origine à la liste elle-même.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import Callable, Mapping
from typing import Protocol

from .confirm import OriginAnswer, build_origin_text
from .operator_profile import Profile, add_allowed_origin
from .origin import Origin, origin_allowed
from .output import ToolError

# Au plus cinq fenêtres par séance : l'opérateur qui répond par lassitude est le maillon faible.
MAX_WINDOWS = 5

_log = logging.getLogger(__name__)


class OriginWindow(Protocol):
    def available(self) -> bool: ...

    async def ask_origin(self, text: str) -> OriginAnswer: ...


Record = Callable[[str, Mapping[str, str]], None]


def is_local_origin(origin: Origin) -> bool:
    """Vrai pour une origine qui désigne la machine ou son réseau : ``localhost``, les noms en ``.local`` et
    ``.localhost``, et toute adresse IP littérale qui n'est pas routable publiquement (boucle locale, réseau privé,
    lien local, partage d'adresses, documentation, réservée).

    Un nom public qui résout vers une adresse privée n'est pas détecté : le serveur ne résout aucun nom.
    """
    host = origin.host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or host.endswith((".localhost", ".local"))
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global


def origin_not_allowed(origin: str | None) -> ToolError:
    """Le refus d'une origine, le même pour une lecture et pour une action : il nomme l'origine et dit à l'opérateur
    ce qu'il peut faire, sans nom de fichier."""
    if origin is None:
        message = "cette page n'a pas d'origine web lisible : le serveur n'en lit rien et n'y agit pas"
    else:
        message = (
            f"{origin} n'est pas dans les sites autorisés par l'opérateur : le serveur n'en lit rien et n'y agit pas. "
            f"L'opérateur peut ajouter {origin} à allowed_origins dans son profil, "
            "ou choisir la politique d'origines (origin_policy : discovery ou open)"
        )
    return ToolError("origin_not_allowed", message, extra_server={"origin": origin})


class OriginPolicy:
    """Ce que le serveur sait des origines admises, d'un appel à l'autre.

    Le profil est relu à chaque décision : une modification de la liste ou de ``origin_policy`` prend effet sans
    redémarrage. Ce qu'une séance a admis ou refusé vit en mémoire, avec le nombre de fenêtres montrées.
    """

    def __init__(self, *, profile: Callable[[], Profile], window: Callable[[], OriginWindow], record: Record | None = None) -> None:
        self._profile = profile
        self._window = window
        self._record = record
        self._session: set[str] = set()
        self._refused: set[str] = set()
        self._windows = 0
        self._lock = asyncio.Lock()

    @property
    def session_origins(self) -> tuple[str, ...]:
        return tuple(sorted(self._session))

    async def admit(self, origin: Origin | None) -> bool:
        """Vrai si le modèle peut lire cette origine et y agir. Aucune origine (``about:blank``, une adresse
        illisible) n'est jamais admise."""
        if not isinstance(origin, Origin):
            return False
        profile = self._profile()
        if origin_allowed(origin, profile.allowed_origins):
            return True
        if profile.origin_policy == "open":
            return not is_local_origin(origin)
        if profile.origin_policy == "discovery":
            return await self._ask(origin, profile)
        return False

    async def _ask(self, origin: Origin, profile: Profile) -> bool:
        key = origin.canonical
        if key in self._session:
            return True
        if key in self._refused:
            return False
        async with self._lock:
            # Un autre appel a pu répondre pendant l'attente du verrou : une origine ne reçoit qu'une fenêtre.
            if key in self._session:
                return True
            if key in self._refused or self._windows >= MAX_WINDOWS:
                return False
            window = self._window()
            if not window.available():
                return False
            answer = await window.ask_origin(build_origin_text(key))
            if answer is OriginAnswer.UNAVAILABLE:
                return False
            self._windows += 1
            self._say("origin_decision", {"origin": key, "answer": answer.value, "policy": profile.origin_policy})
            if answer is OriginAnswer.ADD:
                self._session.add(key)
                self._add_to_the_list(origin, profile)
                return True
            if answer is OriginAnswer.SESSION:
                self._session.add(key)
                return True
            self._refused.add(key)
            return False

    def _add_to_the_list(self, origin: Origin, profile: Profile) -> None:
        """Écrire l'origine dans le profil. Un échec ne retire pas ce que l'opérateur vient d'accorder : l'origine
        reste admise pour la séance, et le journal dit que la liste n'a pas été écrite."""
        try:
            add_allowed_origin(profile, origin)
        except (OSError, ToolError) as exc:
            _log.warning("the origin could not be added to the profile (%s)", type(exc).__name__)
            self._say("origin_not_saved", {"origin": origin.canonical, "policy": profile.origin_policy})
            return
        self._session.discard(origin.canonical)

    def _say(self, event: str, changes: Mapping[str, str]) -> None:
        if self._record is not None:
            self._record(event, changes)
