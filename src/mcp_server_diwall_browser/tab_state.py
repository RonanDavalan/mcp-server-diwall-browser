"""Le fichier d'état des onglets du serveur : ce qu'un processus arrêté a laissé ouvert.

Pourquoi ce module existe :
    Un processus qui s'arrête laisse ses onglets ouverts, et le suivant n'en connaît aucun. Le serveur
    note l'identifiant de cible de chaque onglet qu'il tient dans le volume chiffré ; le processus
    suivant retrouve ceux qui existent encore pour les fermer, jamais pour les piloter. Le fichier ne
    porte que des identifiants de cible et le processus qui les tient : ni adresse, ni titre.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .mount import Volume, VolumeFileError
from .output import ToolError

STATE_DIR = "run"
STATE_FILE = "tabs.json"
STATE_MAX_BYTES = 64 * 1024
MAX_ENTRIES = 200
SCHEMA_VERSION = 1
_log = logging.getLogger(__name__)


def process_start_time(pid: int) -> int | None:
    """L'instant de démarrage d'un processus en ticks du noyau, ou ``None`` quand il n'existe pas.

    Un numéro de processus reprend vie sous un autre programme ; le couple numéro et instant de démarrage
    ne se répète pas.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    fields = text[text.rfind(")") + 2 :].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


@dataclass(frozen=True)
class Owner:
    """Le processus du serveur qui tient un onglet : son numéro, son instant de démarrage, son instance."""

    pid: int
    started: int
    instance: str


@dataclass(frozen=True)
class Entry:
    target_id: str
    owner: Owner

    def as_document(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "pid": self.owner.pid,
            "started": self.owner.started,
            "instance": self.owner.instance,
        }


def _entry_of(document: Any) -> Entry | None:
    if not isinstance(document, dict):
        return None
    target_id, pid, started, instance = (document.get(key) for key in ("target_id", "pid", "started", "instance"))
    if not isinstance(target_id, str) or not target_id or len(target_id) > 128:
        return None
    if isinstance(pid, bool) or not isinstance(pid, int) or isinstance(started, bool) or not isinstance(started, int):
        return None
    if not isinstance(instance, str) or len(instance) > 64:
        return None
    return Entry(target_id, Owner(pid, started, instance))


class TabState:
    """Le fichier ``run/tabs.json`` du volume : lu pour retrouver les onglets d'un processus arrêté, écrit à chaque changement.

    Le volume fermé, rien n'est lu ni écrit. Un fichier absent, trop gros ou abîmé vaut un fichier vide ;
    une écriture qui échoue est dite au journal du processus et n'arrête jamais un outil.
    """

    def __init__(self, volume: Volume, *, instance: str | None = None) -> None:
        self._volume = volume
        self._own = Owner(os.getpid(), process_start_time(os.getpid()) or 0, instance or secrets.token_hex(8))

    def previous(self) -> list[str] | None:
        """Les cibles notées par un processus qui n'existe plus, ou ``None`` quand le volume n'a pas pu être lu.

        Les cibles d'un autre processus encore en vie ne sont pas rendues : elles sont à lui.
        """
        entries = self._read()
        if entries is None:
            return None
        return [entry.target_id for entry in entries if not self._alive(entry.owner)]

    def save(self, held: Iterable[str], *, dropped: Iterable[str] = ()) -> None:
        """Noter les cibles que ce processus tient, en gardant ce que les autres ont noté, sauf ``dropped``."""
        held_ids = list(dict.fromkeys(held))
        gone = set(held_ids) | set(dropped)
        entries = self._read()
        if entries is None:
            return
        kept = [entry for entry in entries if entry.target_id not in gone]
        merged = kept + [Entry(target_id, self._own) for target_id in held_ids]
        document = {"schema_version": SCHEMA_VERSION, "tabs": [entry.as_document() for entry in merged[-MAX_ENTRIES:]]}
        data = json.dumps(document, ensure_ascii=True, indent=2).encode("ascii") + b"\n"
        try:
            self._volume.ensure_dir(STATE_DIR)
            self._volume.replace_file(STATE_DIR, STATE_FILE, data=data)
        except (ToolError, OSError) as exc:
            _log.warning("the tab state file could not be written (%s)", type(exc).__name__)

    def _alive(self, owner: Owner) -> bool:
        if owner.pid == self._own.pid and owner.started == self._own.started:
            # Notre propre processus : une autre instance y est finie, la nôtre est vivante.
            return owner.instance == self._own.instance
        return process_start_time(owner.pid) == owner.started

    def _read(self) -> list[Entry] | None:
        try:
            self._volume.require_mounted()
        except ToolError:
            return None
        try:
            stored = self._volume.read_file(STATE_DIR, STATE_FILE, max_bytes=STATE_MAX_BYTES)
            document = json.loads(stored.data)
        except (ToolError, VolumeFileError, OSError, ValueError):
            return []
        tabs = document.get("tabs") if isinstance(document, dict) else None
        if not isinstance(tabs, list):
            return []
        entries = [entry for entry in map(_entry_of, tabs) if entry is not None]
        return entries[-MAX_ENTRIES:]
