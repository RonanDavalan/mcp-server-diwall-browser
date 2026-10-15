"""Point de sortie unique de tout ce que le serveur rend, journalise ou imprime.

Pourquoi ce module existe :
    Tout ce qui quitte le serveur atteint le fournisseur du modèle ou un fichier de journal. Une
    protection écrite par outil ou par chemin de sortie est oubliée au suivant : la rédaction, le
    nettoyage des URL, les bornes de longueur et l'enveloppe du résultat sont donc appliqués ici,
    à chaque valeur, par une seule fonction.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import re
import sys
import threading
import unicodedata
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import origin as _origin

REDACTED = "<redacted>"
INVALID_URL = "<invalid-url>"
TRUNCATION_MARK = "…"
SERVER_STRING_LIMIT = 2000
PAGE_STRING_LIMIT = 4000

# Marqueurs que ce module ou ses appelants écrivent à la place d'une valeur. Ils sont reconnus en entier
# pour que rédiger un texte deux fois ne les grignote pas quand un secret enregistré est un seul
# caractère qu'on trouve dans un marqueur.
_MARKERS = (
    REDACTED,
    INVALID_URL,
    "<secret:username>",
    "<secret:password>",
    "<typed>",
)

ERROR_CODES = frozenset(
    {
        "not_configured",
        "profile_invalid",
        "redlist_in_profile",
        "profile_dir_is_default",
        "debug_endpoint_not_loopback",
        "debug_endpoint_undetermined",
        "browser_unreachable",
        "encrypted_dir_closed",
        "encrypted_dir_undetermined",
        "mount_not_gocryptfs",
        "mount_unreadable",
        "project_root_missing",
        "volume_path_refused",
        "origin_not_allowed",
        "forbidden_scheme",
        "tab_not_owned",
        "navigation_failed",
        "snapshot_stale",
        "element_stale",
        "element_obscured",
        "unsupported_element",
        "option_not_found",
        "page_changed_during_capture",
        "capture_unavailable",
        "sensitive_value_refused",
        "credential_not_bound",
        "credential_ambiguous",
        "credential_invalid",
        "redlist_refused",
        "confirmation_denied",
        "confirmation_expired",
        "confirmation_unavailable",
        "limit_reached",
        "journal_unavailable",
        "wait_timeout",
        "invalid_arguments",
        "internal_error",
    }
)

SENSITIVE_QUERY_WORDS = (
    "token",
    "code",
    "state",
    "key",
    "secret",
    "session",
    "auth",
    "password",
    "passwd",
    "pwd",
    "otp",
    "jwt",
    "signature",
    "credential",
    "bearer",
    "apikey",
    "api_key",
)

_ENVELOPE_KEYS = frozenset({"ok", "server", "page_content"})
_INTERNAL_ERROR_MESSAGE = "erreur interne du serveur"

_log = logging.getLogger(__name__)


# --- Registre des secrets résolus -----------------------------------------------


class _SecretRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()
        self._pattern: re.Pattern[str] | None = None

    def add(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("a secret must be a string")
        if value == "":
            return
        forms = {value, urllib.parse.quote(value, safe=""), json.dumps(value)[1:-1]}
        with self._lock:
            if forms <= self._values:
                return
            self._values |= forms
            # Le plus long d'abord : à chaque position la valeur enregistrée la plus longue l'emporte, si bien
            # qu'un secret court ne laisse jamais la fin d'un long.
            alternatives = sorted(self._values | set(_MARKERS), key=len, reverse=True)
            self._pattern = re.compile("|".join(re.escape(a) for a in alternatives))

    def redact(self, text: str) -> str:
        with self._lock:
            pattern = self._pattern
        if pattern is None:
            return text
        return pattern.sub(_replace_match, text)


def _replace_match(match: re.Match[str]) -> str:
    found = match.group(0)
    return found if found in _MARKERS else REDACTED


_registry = _SecretRegistry()


def register_secret(value: str) -> None:
    """Retenir un secret résolu pour qu'il soit rédigé de chaque sortie."""
    _registry.add(value)


def redact_text(text: str) -> str:
    """Remplacer chaque secret enregistré, sous toutes ses formes enregistrées, par un marqueur."""
    return _registry.redact(text)


# --- Nettoyage des URL ----------------------------------------------------------


def sanitize_url(url: str, *, for_journal: bool = False) -> str:
    """Retirer d'une adresse ce qui peut porter un secret.

    Pour le modèle : retirer les informations d'utilisateur, rédiger la valeur des paramètres de requête
    dont le nom évoque un secret (le nom est gardé), rédiger un fragment non vide.
    Pour le journal : garder seulement schéma, hôte, port et chemin.
    """
    try:
        parts = _origin.split_url(url)
    except _origin.OriginError:
        return INVALID_URL
    if parts.scheme in _origin.DEFAULT_PORTS and not parts.has_authority:
        return INVALID_URL

    if parts.has_authority:
        head = f"{parts.scheme}://{parts.hostport}{parts.path}"
    elif for_journal:
        # Les adresses opaques (data:, javascript:) portent du contenu dans leur chemin.
        head = f"{parts.scheme}:{parts.path}" if parts.scheme == "about" else f"{parts.scheme}:"
    else:
        head = f"{parts.scheme}:{parts.path}"
    if for_journal:
        return head

    result = head
    if parts.query is not None:
        result += "?" + _sanitize_query(parts.query)
    if parts.fragment is not None:
        result += "#" + (REDACTED if parts.fragment else "")
    return result


def _sanitize_query(query: str) -> str:
    pieces = []
    for piece in query.split("&"):
        name, eq, _value = piece.partition("=")
        decoded = urllib.parse.unquote(name).lower()
        if eq and any(word in decoded for word in SENSITIVE_QUERY_WORDS):
            pieces.append(f"{name}={REDACTED}")
        else:
            pieces.append(piece)
    return "&".join(pieces)


# --- Erreurs et sorties d'outils ------------------------------------------------


class ToolError(Exception):
    """Un refus prévu, rapporté à l'opérateur avec un code stable."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        action_expected: str | None = None,
        extra_server: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code!r}")
        super().__init__(code)
        self.code = code
        self.message = message
        self.action_expected = action_expected
        self.extra_server = dict(extra_server or {})


@dataclass
class ToolOutput:
    server: dict[str, Any] = field(default_factory=dict)
    page_content: dict[str, Any] | None = None
    image_png: bytes | None = None


class EnvelopeError(Exception):
    """Un outil a construit un résultat que le point de sortie n'accepte pas (erreur de programmation)."""


def finalize(envelope: dict[str, Any]) -> dict[str, Any]:
    """Contrôler la forme de l'enveloppe, puis nettoyer chaque valeur qu'elle contient."""
    if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_KEYS:
        raise EnvelopeError("envelope keys must be exactly ok, server, page_content")
    if not isinstance(envelope["ok"], bool):
        raise EnvelopeError("ok must be a boolean")
    if not isinstance(envelope["server"], dict):
        raise EnvelopeError("server must be an object")
    page = envelope["page_content"]
    if page is not None and not isinstance(page, dict):
        raise EnvelopeError("page_content must be an object or null")
    return {
        "ok": envelope["ok"],
        "server": _clean(envelope["server"], page=False, url_key=False),
        "page_content": None if page is None else _clean(page, page=True, url_key=False),
    }


def _clean(value: Any, *, page: bool, url_key: bool) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_string(value, page=page, url_key=url_key)
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EnvelopeError("object keys must be strings")
            is_url = key == "url" or key.endswith("_url")
            cleaned[_clean_string(key, page=page, url_key=False)] = _clean(
                item, page=page, url_key=is_url
            )
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean(item, page=page, url_key=url_key) for item in value]
    # Tout autre serait sérialisé par str(), dont cette fonction ne peut pas répondre.
    raise EnvelopeError(f"unsupported value type in envelope: {type(value).__name__}")


def _clean_string(text: str, *, page: bool, url_key: bool) -> str:
    if url_key:
        text = sanitize_url(text)
    # Rédiger avant et après le retrait des caractères de contrôle : un secret peut en être coupé, ou en
    # contenir un.
    text = redact_text(text)
    text = _strip_controls(text, keep_newlines=page)
    text = redact_text(text)
    limit = PAGE_STRING_LIMIT if page else SERVER_STRING_LIMIT
    if len(text) > limit:
        text = text[: limit - 1] + TRUNCATION_MARK
    return text


def bounded_text(text: object, limit: int) -> str:
    """Texte de page rendu montrable : caractères de contrôle et de format retirés, espaces réduites,
    longueur bornée. Tout sauf une chaîne donne ``""``.

    Les caractères de format partent aussi : des surcharges bidirectionnelles peuvent faire lire un
    texte autrement que ce qu'il contient.
    """
    if not isinstance(text, str):
        return ""
    kept = []
    for char in text:
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char) not in ("Cc", "Cf"):
            kept.append(char)
    collapsed = " ".join("".join(kept).split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1] + TRUNCATION_MARK
    return collapsed


def _strip_controls(text: str, *, keep_newlines: bool) -> str:
    kept = []
    for char in text:
        if unicodedata.category(char) == "Cc" and not (keep_newlines and char in "\n\t"):
            continue
        kept.append(char)
    return "".join(kept)


def new_operation_id() -> str:
    return os.urandom(16).hex()


_current_operation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "operation_id", default=None
)


def current_operation_id() -> str | None:
    """Identifiant de l'appel d'outil en cours d'exécution, pour les entrées de journal."""
    return _current_operation_id.get()


ToolResult = tuple[dict[str, Any], bytes | None]


EXIT_POINT_ATTRIBUTE = "output_tool_name"


def tool(name: str) -> Callable[[Callable[..., Awaitable[ToolOutput]]], Callable[..., Awaitable[ToolResult]]]:
    """Envelopper une coroutine d'outil pour que son résultat quitte toujours par finalize.

    L'enveloppe porte le nom de l'outil, pour que le serveur puisse vérifier que chaque outil qu'il
    enregistre passe par ici.
    """

    def decorate(func: Callable[..., Awaitable[ToolOutput]]) -> Callable[..., Awaitable[ToolResult]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> ToolResult:
            operation_id = new_operation_id()
            token = _current_operation_id.set(operation_id)
            try:
                return await _run(func, name, operation_id, args, kwargs)
            finally:
                _current_operation_id.reset(token)

        setattr(wrapper, EXIT_POINT_ATTRIBUTE, name)
        return wrapper

    return decorate


async def _run(
    func: Callable[..., Awaitable[ToolOutput]],
    name: str,
    operation_id: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> ToolResult:
    image: bytes | None = None
    try:
        result = await func(*args, **kwargs)
        if not isinstance(result, ToolOutput):
            raise EnvelopeError("a tool must return a ToolOutput")
        envelope = {
            "ok": True,
            "server": {"notices": [], **result.server, "operation_id": operation_id, "tool": name},
            "page_content": result.page_content,
        }
        image = result.image_png
    except ToolError as exc:
        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.action_expected:
            error["action_expected"] = exc.action_expected
        envelope = {
            "ok": False,
            "server": {
                "notices": [],
                **exc.extra_server,
                "operation_id": operation_id,
                "tool": name,
                "error": error,
            },
            "page_content": None,
        }
    except Exception as exc:  # noqa: BLE001 - tout échec doit sortir sous forme d'enveloppe
        # Seuls le nom du type et, pour une erreur du protocole, son code sont journalisés : le texte de
        # l'exception et la pile peuvent contenir des données de page ou un secret jamais enregistré.
        _log.error("tool %s failed with %s", name, _failure_label(exc))
        envelope = _internal_error(name, operation_id)
    try:
        return finalize(envelope), image
    except Exception as exc:  # noqa: BLE001
        _log.error("tool %s produced an invalid result: %s", name, type(exc).__name__)
        return finalize(_internal_error(name, operation_id)), None


def _failure_label(exc: Exception) -> str:
    """Le nom du type de l'exception, suivi du code numérique d'une erreur du protocole."""
    # Importé ici : le client du protocole importe ce module.
    from .cdp import CdpError

    if isinstance(exc, CdpError):
        return f"{type(exc).__name__} (code {exc.code})"
    return type(exc).__name__


def _internal_error(name: str, operation_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "server": {
            "notices": [],
            "operation_id": operation_id,
            "tool": name,
            "error": {"code": "internal_error", "message": _INTERNAL_ERROR_MESSAGE},
        },
        "page_content": None,
    }


# --- Journal d'exploitation -----------------------------------------------------


class RedactingFilter(logging.Filter):
    """Faire passer chaque message de journal formaté, et tout texte d'exception, par la rédaction."""

    _exception_formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info:
            text = self._exception_formatter.formatException(record.exc_info)
            record.exc_text = redact_text(text)
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


_HANDLER_NAME = "mcp_server_diwall_browser.stderr"


def configure_logging(level: int = logging.INFO) -> logging.Handler:
    """Envoyer le journal d'exploitation sur la sortie d'erreur seulement, par la rédaction.

    La sortie standard porte le protocole ; tout autre gestionnaire du journal racine est retiré pour
    que rien ne puisse y écrire.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.get_name() == _HANDLER_NAME:
            return handler
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    return handler
