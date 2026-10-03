"""Read credential files and resolve one credential for one origin.

Why this module exists:
    A credential typed on the wrong site is a credential handed over. The
    binding between a credential and the origins it may be typed on must be
    checked on every resolution, by one function, whichever way the
    credential was designated; naming a file must never skip that check.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from . import hardening
from .mount import Volume, VolumeFileError
from .origin import Origin, OriginError, parse_origin
from .output import ToolError, register_secret

CREDENTIALS_DIR = "credentials"
MAX_FILE_BYTES = 16 * 1024
KEYS = ("username", "password")

_ALLOWED_FILE_KEYS = frozenset({"allowed_origins", "username", "password"})
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_FILE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\.json")

_FIX_COMMANDS = {
    "bad_mode": "corriger avec : chmod 600 {name}",
    "bad_owner": "le fichier doit appartenir à l'utilisateur du serveur",
    "not_regular_file": "le fichier doit être un fichier ordinaire, pas un lien ni un répertoire",
}


class Secret:
    """A resolved credential value that refuses to be printed, copied or pickled."""

    __slots__ = ("_value", "_kind")

    def __init__(self, value: str, kind: str) -> None:
        if kind not in KEYS:
            raise ValueError("unknown credential kind")
        register_secret(value)
        self._value = value
        self._kind = kind

    @property
    def kind(self) -> str:
        return self._kind

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"<secret:{self._kind}>"

    __str__ = __repr__

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError("a Secret cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("a Secret cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("a Secret cannot be serialized")

    def __copy__(self) -> NoReturn:
        raise TypeError("a Secret cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("a Secret cannot be copied")


@dataclass(frozen=True)
class _CredentialFile:
    basename: str
    allowed_origins: tuple[Origin, ...]
    values: dict[str, str]


def valid_name(name: object) -> bool:
    """Whether ``name`` can label a credential file: the file is ``<name>.json``."""
    return isinstance(name, str) and _NAME_RE.fullmatch(name) is not None


def loads_strict(raw: bytes) -> Any:
    """Parse a JSON document that repeats no key; ``ValueError`` when it is not one."""
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)


def validate(basename: str, raw: bytes) -> None:
    """Refuse, as ``credential_invalid``, the content of a credential file that ``resolve`` would refuse.

    The one schema: a file is checked here before it is written as it is
    when it is read.
    """
    _parse(basename, raw)


def resolve(volume: Volume, page_origin: Origin, key: str, name: str | None = None) -> Secret:
    """Return the ``key`` of the single credential bound to ``page_origin``.

    ``page_origin`` is the origin the browser reports at the moment of
    typing. ``name`` narrows the search to one file but never replaces the
    origin binding.
    """
    hardening.disable_core_dumps()
    root = volume.require_mounted()
    if not isinstance(page_origin, Origin):
        raise TypeError("page_origin must be an Origin")
    if key not in KEYS:
        raise ToolError("credential_invalid", "la clé demandée doit être username ou password")
    if name is not None and not valid_name(name):
        raise ToolError("credential_invalid", "le nom d'identifiant demandé n'est pas un nom valide")

    loaded = [_load(volume, basename) for basename in _candidates(volume, root, name)]
    bound = [item for item in loaded if page_origin in item.allowed_origins]
    if not bound:
        expected = f"« {name} »" if name is not None else "un fichier d'identifiants"
        raise ToolError(
            "credential_not_bound",
            f"aucun identifiant lié à l'origine {page_origin.canonical} : "
            f"{expected} doit lister cette origine dans allowed_origins",
        )
    if len(bound) > 1:
        names = ", ".join(item.basename for item in bound)
        raise ToolError(
            "credential_ambiguous",
            f"plusieurs fichiers d'identifiants sont liés à l'origine {page_origin.canonical} : {names}",
        )
    chosen = bound[0]
    if key not in chosen.values:
        _refuse(chosen.basename, "key_missing")
    return Secret(chosen.values[key], key)


def credentials_directory(volume: Volume) -> Path | None:
    """The directory of the credential files, checked; ``None`` when the operator has not made it yet."""
    root = volume.require_mounted()
    directory = volume.resolve_inside(CREDENTIALS_DIR)
    if directory != root / CREDENTIALS_DIR:
        _refuse(CREDENTIALS_DIR, "not_regular_file")
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        _refuse(CREDENTIALS_DIR, "not_regular_file")
    if info.st_uid != volume.euid:
        _refuse(CREDENTIALS_DIR, "bad_owner")
    if info.st_mode & 0o077:
        raise ToolError(
            "credential_invalid",
            "répertoire d'identifiants « credentials » refusé (bad_mode) : "
            "corriger avec : chmod 700 credentials",
            extra_server={"reason": "bad_mode"},
        )
    return directory


def _candidates(volume: Volume, root: Path, name: str | None) -> list[str]:
    directory = credentials_directory(volume)
    if directory is None:
        return []
    if name is not None:
        filename = f"{name}.json"
        return [filename] if os.path.lexists(os.path.join(directory, filename)) else []
    with os.scandir(directory) as entries:
        return sorted(
            entry.name
            for entry in entries
            if _FILE_NAME_RE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False)
        )


def _load(volume: Volume, basename: str) -> _CredentialFile:
    try:
        content = volume.read_file(CREDENTIALS_DIR, basename, max_bytes=MAX_FILE_BYTES)
    except VolumeFileError as exc:
        _refuse(basename, "not_regular_file" if exc.reason == "missing" else exc.reason)
    return _parse(basename, content.data)


def _parse(basename: str, raw: bytes) -> _CredentialFile:
    try:
        data = loads_strict(raw)
    except (UnicodeDecodeError, ValueError):
        _refuse(basename, "not_json")
    if not isinstance(data, dict):
        _refuse(basename, "not_json")

    # Register first: whatever happens next, these values are now in memory.
    for key in KEYS:
        if isinstance(data.get(key), str):
            register_secret(data[key])

    if "allowed_origins" not in data:
        _refuse(basename, "missing_allowed_origins")
    if set(data) - _ALLOWED_FILE_KEYS:
        _refuse(basename, "unknown_keys")
    origins_raw = data["allowed_origins"]
    if not isinstance(origins_raw, list):
        _refuse(basename, "bad_origin")
    if not origins_raw:
        _refuse(basename, "missing_allowed_origins")
    origins = []
    for entry in origins_raw:
        if not isinstance(entry, str):
            _refuse(basename, "bad_origin")
        try:
            origins.append(parse_origin(entry))
        except OriginError:
            _refuse(basename, "bad_origin")
    values = {}
    for key in KEYS:
        if key in data:
            value = data[key]
            if not isinstance(value, str) or value == "":
                _refuse(basename, "no_username_or_password")
            values[key] = value
    if not values:
        _refuse(basename, "no_username_or_password")
    return _CredentialFile(basename=basename, allowed_origins=tuple(origins), values=values)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            # Two values for one key: which one wins is a parser detail the
            # operator cannot see; refuse instead.
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _refuse(basename: str, reason: str) -> NoReturn:
    message = f"fichier d'identifiants « {basename} » refusé ({reason})"
    fix = _FIX_COMMANDS.get(reason)
    if fix:
        message += " : " + fix.format(name=basename)
    raise ToolError("credential_invalid", message, extra_server={"reason": reason})
