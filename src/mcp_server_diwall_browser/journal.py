"""Audit journal: one JSON line per operation, inside the encrypted volume.

Why this module exists:
    The operator needs to know afterwards what the model did, on which site
    and with which confirmation. That record must never hold a secret, must
    not make an ordinary operation fail when it cannot be written, and must
    make an irreversible one fail when it cannot.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pwd
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass

from .credentials import Secret
from .mount import Volume
from .origin import OriginError, origin_of_url
from .output import ToolError, redact_text, sanitize_url

JOURNAL_DIR = "journal"
JOURNAL_FILE = "operations.jsonl"
INTENT_LIMIT = 500
CLIENT_FIELD_LIMIT = 64
TYPED_MARKER = "<typed>"
CONFIRMATION_STATES = frozenset({"none", "requested", "granted", "denied", "expired"})
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
    """What a tool reports about one operation; the journal adds the rest."""

    operation_id: str
    tool: str
    result: str
    mutating: bool
    origin: str | None = None
    intent: str | Secret = ""
    red_list: bool = False
    confirmation: str = "none"


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
        """Append the entry; on failure count it and warn once, never raise."""
        line = self._line(entry)
        try:
            self._append(line)
        except Exception as exc:  # noqa: BLE001 - an ordinary operation must not fail here
            self._record_failure(exc)
            return False
        return True

    def write_required(self, entry: JournalEntry) -> None:
        """Append the entry or refuse: used before an irreversible action."""
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
            "gates": {"red_list": entry.red_list, "confirmation": entry.confirmation},
            "client": {"name": _text(name), "version": _text(version)},
            "host": _text(socket.gethostname()),
            "user": _text(_user_name()),
        }
        return (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")

    def _client_info(self) -> tuple[str, str]:
        try:
            info = self._client_info_provider()
        except Exception:  # noqa: BLE001 - client identity is informative only
            info = None
        name, version = info if info else (None, None)
        return _client_field(name), _client_field(version)


def _text(value: object) -> str:
    # A resolved credential is only ever written as its marker.
    if isinstance(value, Secret):
        return repr(value)
    if not isinstance(value, str):
        raise ValueError("journal text fields are strings")
    return redact_text(value)


def _origin_text(value: str) -> str:
    # Cleaning first drops userinfo, so an address that only failed on it
    # still reduces to its origin.
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
