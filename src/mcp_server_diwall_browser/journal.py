"""Journal d'audit : une ligne JSON par opération, à l'intérieur du volume chiffré.

Pourquoi ce module existe :
    L'opérateur a besoin de savoir après coup ce que le modèle a fait, sur quel site et avec quelle
    confirmation. Cette trace ne doit jamais contenir de secret, ne doit pas faire échouer une
    opération ordinaire quand elle ne peut pas être écrite, et doit faire échouer une opération
    irréversible quand elle ne le peut pas.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pwd
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .credentials import Secret
from .mount import Volume
from .operator_profile import DEFAULT_ORIGIN_POLICY, ORIGIN_POLICIES
from .origin import OriginError, origin_of_url
from .output import ToolError, redact_text, sanitize_url

JOURNAL_DIR = "journal"
JOURNAL_FILE = "operations.jsonl"
INTENT_LIMIT = 500
CLIENT_FIELD_LIMIT = 64
TYPED_MARKER = "<typed>"
# "unavailable" : aucune fenêtre n'a pu être ouverte, donc l'action a été refusée.
CONFIRMATION_STATES = frozenset({"none", "requested", "granted", "denied", "expired", "unavailable"})
FIELDS = (
    "ts",
    "operation_id",
    "tool",
    "origin",
    "result",
    "mutating",
    "intent",
    "gates",
    "client",
    "host",
    "user",
)

_CLIENT_CHAR_RE = re.compile(r"[^A-Za-z0-9._ -]")
_log = logging.getLogger(__name__)

ClientInfo = tuple[str | None, str | None]


@dataclass(frozen=True)
class JournalEntry:
    """Ce qu'un outil rapporte d'une opération ; le journal ajoute le reste."""

    operation_id: str
    tool: str
    result: str
    mutating: bool
    origin: str | None = None
    intent: str | Secret = ""
    red_list: bool = False
    confirmation: str = "none"
    origin_policy: str = DEFAULT_ORIGIN_POLICY
    # Ce que le refus a rencontré (balise ou type de pseudo-élément) : du texte court, jamais le contenu de la page.
    touched: Mapping[str, str | None] | None = None


class Journal:
    def __init__(self, volume: Volume, client_info_provider: Callable[[], ClientInfo | None]) -> None:
        self._volume = volume
        self._client_info_provider = client_info_provider
        self._not_journaled = 0
        self._warned = False

    @property
    def operations_not_journaled(self) -> int:
        return self._not_journaled

    def write(self, entry: JournalEntry) -> bool:
        """Ajouter l'entrée ; en cas d'échec, le compter et avertir une fois, sans jamais lever."""
        line = self._line(entry)
        try:
            self._append(line)
        except Exception as exc:  # noqa: BLE001 - une opération ordinaire ne doit pas échouer ici
            self._record_failure(exc)
            return False
        return True

    def write_event(self, event: str, changes: Mapping[str, Any]) -> bool:
        """Ajouter une ligne d'événement du serveur, qui n'est l'appel d'aucun outil ; ``changes`` n'est que du texte.

        Même discipline que ``write`` : l'échec est compté, dit une fois, jamais levé.
        """
        name, version = self._client_info()
        record = {
            "ts": _text(datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")),
            "event": _text(event),
            "changes": _plain_text(changes),
            "client": {"name": _text(name), "version": _text(version)},
            "host": _text(socket.gethostname()),
            "user": _text(_user_name()),
        }
        try:
            self._append((json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
        except Exception as exc:  # noqa: BLE001 - une ligne d'événement ne doit jamais faire échouer un appel
            self._record_failure(exc)
            return False
        return True

    def write_required(self, entry: JournalEntry) -> None:
        """Ajouter l'entrée ou refuser : employé avant une action irréversible."""
        line = self._line(entry)
        try:
            self._append(line)
        except ToolError as exc:
            self._record_failure(exc)
            raise
        except Exception as exc:  # noqa: BLE001
            self._record_failure(exc)
            raise ToolError(
                "journal_unavailable",
                "le journal d'audit ne peut pas être écrit : action refusée par précaution",
            ) from None

    def _record_failure(self, exc: BaseException) -> None:
        self._not_journaled += 1
        if not self._warned:
            self._warned = True
            _log.warning("audit journal unavailable (%s); operations are not journaled", type(exc).__name__)

    def _append(self, line: bytes) -> None:
        self._volume.ensure_dir(JOURNAL_DIR)
        fd = self._volume.open_append(JOURNAL_DIR, JOURNAL_FILE)
        try:
            written = os.write(fd, line)
        finally:
            os.close(fd)
        if written != len(line):
            raise OSError("short write to the audit journal")

    def _line(self, entry: JournalEntry) -> bytes:
        if entry.confirmation not in CONFIRMATION_STATES:
            raise ValueError("unknown confirmation state")
        if entry.origin_policy not in ORIGIN_POLICIES:
            raise ValueError("unknown origin policy")
        if not isinstance(entry.mutating, bool) or not isinstance(entry.red_list, bool):
            raise ValueError("mutating and red_list are booleans")
        name, version = self._client_info()
        record = {
            "ts": _text(datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")),
            "operation_id": _text(entry.operation_id),
            "tool": _text(entry.tool),
            "origin": None if entry.origin is None else _origin_text(entry.origin),
            "result": _text(entry.result),
            "mutating": entry.mutating,
            "intent": _text(entry.intent)[:INTENT_LIMIT],
            "gates": {"red_list": entry.red_list, "confirmation": entry.confirmation, "origin_policy": entry.origin_policy},
            "client": {"name": _text(name), "version": _text(version)},
            "host": _text(socket.gethostname()),
            "user": _text(_user_name()),
        }
        if entry.touched:
            record["touched"] = _plain_text({key: value for key, value in entry.touched.items() if value is not None})
        return (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")

    def _client_info(self) -> tuple[str, str]:
        try:
            info = self._client_info_provider()
        except Exception:  # noqa: BLE001 - l'identité du client n'est qu'informative
            info = None
        name, version = info if info else (None, None)
        return _client_field(name), _client_field(version)


def _text(value: object) -> str:
    # Un identifiant résolu n'est jamais écrit que sous la forme de son marqueur.
    if isinstance(value, Secret):
        return repr(value)
    if not isinstance(value, str):
        raise ValueError("journal text fields are strings")
    return redact_text(value)


def _plain_text(value: Any) -> Any:
    """Les chaînes d'une structure de dictionnaires et de listes, chacune par la neutralisation du journal."""
    if isinstance(value, Mapping):
        return {_text(key): _plain_text(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_text(item) for item in value]
    return _text(value)


def _origin_text(value: str) -> str:
    # Nettoyer d'abord retire les informations d'utilisateur, si bien qu'une adresse qui n'avait échoué que
    # là-dessus se réduit tout de même à son origine.
    cleaned = sanitize_url(value, for_journal=True)
    try:
        return _text(origin_of_url(cleaned).canonical)
    except OriginError:
        return _text(cleaned)


def _client_field(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = _CLIENT_CHAR_RE.sub("", value)[:CLIENT_FIELD_LIMIT].strip()
    return cleaned or "unknown"


def _user_name() -> str:
    euid = os.geteuid()
    try:
        return pwd.getpwuid(euid).pw_name
    except KeyError:
        return str(euid)
