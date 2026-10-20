"""Trace d'un changement des listes d'origines du profil et de la politique d'origines : la comparaison, l'empreinte, l'avis.

Pourquoi ce module existe :
    Le profil est relu à chaque appel, et un client qui a un shell peut l'éditer. Le serveur ne peut pas
    l'en empêcher ; il laisse une trace après coup (``profile_changed`` au journal, un avis dans
    ``status``). La comparaison des trois listes et de ``origin_policy`` vit ici, à un seul endroit, comme
    celle de l'empreinte gardée dans le volume chiffré à côté du journal. Aucun autre champ du profil n'est
    lu ici.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .journal import Journal
from .mount import Volume, VolumeFileError
from .operator_profile import DEFAULT_ORIGIN_POLICY, Profile
from .output import ToolError

LIST_NAMES = ("allowed_origins", "confirm_each_change", "local_model_origins")
POLICY_NAME = "origin_policy"
COMPARED_NAMES = (*LIST_NAMES, POLICY_NAME)

Changes = dict[str, dict[str, list[str]]]


@dataclass(frozen=True)
class OriginLists:
    """Les trois listes d'origines du profil, chacune triée et sans doublon, en texte canonique, et la politique
    d'origines, comparée de la même façon (une valeur seule)."""

    allowed_origins: tuple[str, ...] = ()
    confirm_each_change: tuple[str, ...] = ()
    local_model_origins: tuple[str, ...] = ()
    origin_policy: tuple[str, ...] = (DEFAULT_ORIGIN_POLICY,)

    @classmethod
    def of_profile(cls, profile: Profile) -> OriginLists:
        return cls(
            allowed_origins=_canonical(profile.allowed_origins),
            confirm_each_change=_canonical(profile.confirm_each_change),
            local_model_origins=_canonical(profile.local_model_origins),
            origin_policy=(profile.origin_policy,),
        )

    @classmethod
    def of_document(cls, document: Any) -> OriginLists | None:
        """Les listes d'une empreinte lue sur le disque, ou ``None`` quand elle n'a pas la forme attendue."""
        if not isinstance(document, dict):
            return None
        values: dict[str, tuple[str, ...]] = {}
        for name in LIST_NAMES:
            entries = document.get(name)
            if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
                return None
            values[name] = tuple(sorted(set(entries)))
        # Une empreinte d'avant la politique d'origines n'a pas la clé : le profil d'alors était ``strict``.
        policy = document.get(POLICY_NAME, [DEFAULT_ORIGIN_POLICY])
        if not isinstance(policy, list) or len(policy) != 1 or not isinstance(policy[0], str):
            return None
        return cls(**values, origin_policy=(policy[0],))

    def as_document(self) -> dict[str, list[str]]:
        return {name: list(getattr(self, name)) for name in COMPARED_NAMES}


def _canonical(entries: Any) -> tuple[str, ...]:
    return tuple(sorted({entry.canonical for entry in entries}))


def compare(previous: OriginLists, current: OriginLists) -> Changes:
    """Pour chaque liste qui diffère, les origines ajoutées et retirées, et pour la politique la valeur qui entre et
    celle qui sort ; vide quand rien n'a changé."""
    changes: Changes = {}
    for name in COMPARED_NAMES:
        before, after = set(getattr(previous, name)), set(getattr(current, name))
        if before != after:
            changes[name] = {"added": sorted(after - before), "removed": sorted(before - after)}
    return changes


def notice_text(changes: Changes) -> str:
    """L'avis de ``status`` : les mêmes origines que la ligne du journal, sur une ligne."""
    parts = []
    for name, moved in changes.items():
        signed = [f"+{origin}" for origin in moved["added"]] + [f"-{origin}" for origin in moved["removed"]]
        parts.append(f"{name} {' '.join(signed)}")
    return "profile_changed : " + " ; ".join(parts)


FINGERPRINT_DIR = "journal"
FINGERPRINT_FILE = "profile-origins.json"
FINGERPRINT_MAX_BYTES = 64 * 1024
_log = logging.getLogger(__name__)


class ProfileTrace:
    """Ce que le serveur a vu des listes d'origines, d'un appel à l'autre, et ce qu'il en dit.

    Au premier appel qui trouve le volume monté, la référence est l'empreinte de la dernière exécution ;
    ensuite, c'est ce que l'appel précédent a vu. Le volume fermé, rien n'est lu ni écrit : la référence
    n'avance pas, et le changement est dit quand le volume s'ouvre.
    """

    def __init__(self) -> None:
        self._seen: OriginLists | None = None
        self._notices: list[str] = []

    def check(self, profile: Profile, volume: Volume, journal: Journal) -> None:
        current = OriginLists.of_profile(profile)
        if current == self._seen:
            return
        try:
            volume.require_mounted()
        except ToolError:
            return
        previous = self._seen if self._seen is not None else _read_fingerprint(volume)
        if previous is None or previous == current:
            self._seen = current
            if previous is None:
                _write_fingerprint(volume, current)
            return
        changes = compare(previous, current)
        if not journal.write_event("profile_changed", changes):
            return
        self._seen = current
        _write_fingerprint(volume, current)
        self._notices.append(notice_text(changes))

    def drain_notices(self) -> list[str]:
        notices, self._notices = self._notices, []
        return notices


def _read_fingerprint(volume: Volume) -> OriginLists | None:
    try:
        stored = volume.read_file(FINGERPRINT_DIR, FINGERPRINT_FILE, max_bytes=FINGERPRINT_MAX_BYTES)
        return OriginLists.of_document(json.loads(stored.data))
    except (ToolError, VolumeFileError, OSError, ValueError):
        return None


def _write_fingerprint(volume: Volume, lists: OriginLists) -> None:
    data = json.dumps(lists.as_document(), ensure_ascii=True, indent=2).encode("ascii") + b"\n"
    try:
        volume.ensure_dir(FINGERPRINT_DIR)
        volume.replace_file(FINGERPRINT_DIR, FINGERPRINT_FILE, data=data)
    except (ToolError, OSError) as exc:
        _log.warning("the profile fingerprint could not be written (%s)", type(exc).__name__)
