"""Single exit point for everything the server returns, logs or prints.

Why this module exists:
    Whatever leaves the server reaches the model provider or a log file. A
    protection written per tool or per output path is forgotten on the next
    one, so redaction, URL cleaning, length bounds and the result envelope
    are applied here, to every value, by one function.
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

# Markers this module or its callers write in place of a value. They are
# matched as a whole so that redacting a text twice does not chew them up
# when a registered secret is a single character found inside a marker.
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


# --- Registry of resolved secrets -------------------------------------------


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
            # Longest first: at any position the longest registered value
            # wins, so a short secret never leaves the tail of a long one.
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
    """Remember a resolved secret so that it is redacted from every output."""
    _registry.add(value)


def redact_text(text: str) -> str:
    """Replace every registered secret, in all its recorded forms, by a marker."""
    return _registry.redact(text)


# --- URL cleaning -------------------------------------------------------------


def sanitize_url(url: str, *, for_journal: bool = False) -> str:
    """Remove from an address what may carry a secret.

    For the model: drop userinfo, redact the value of query parameters whose
    name suggests a secret (the name is kept), redact a non-empty fragment.
    For the journal: keep scheme, host, port and path only.
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
        # Opaque addresses (data:, javascript:) carry content in their path.
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


# --- Tool errors and outputs --------------------------------------------------


class ToolError(Exception):
    """A foreseen refusal, reported to the operator with a stable code."""

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
    """A tool built a result the exit point does not accept (programming error)."""


def finalize(envelope: dict[str, Any]) -> dict[str, Any]:
    """Check the envelope shape, then clean every value in it."""
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
    # Anything else would be serialized through str(), which this function
    # cannot vouch for.
    raise EnvelopeError(f"unsupported value type in envelope: {type(value).__name__}")


def _clean_string(text: str, *, page: bool, url_key: bool) -> str:
    if url_key:
        text = sanitize_url(text)
    # Redact before and after removing control characters: a secret may be
    # split by one, or may itself contain one.
    text = redact_text(text)
    text = _strip_controls(text, keep_newlines=page)
    text = redact_text(text)
    limit = PAGE_STRING_LIMIT if page else SERVER_STRING_LIMIT
    if len(text) > limit:
        text = text[: limit - 1] + TRUNCATION_MARK
    return text


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
    """Identifier of the tool call being executed, for journal entries."""
    return _current_operation_id.get()


ToolResult = tuple[dict[str, Any], bytes | None]


def tool(name: str) -> Callable[[Callable[..., Awaitable[ToolOutput]]], Callable[..., Awaitable[ToolResult]]]:
    """Wrap a tool coroutine so that its result always leaves through finalize."""

    def decorate(func: Callable[..., Awaitable[ToolOutput]]) -> Callable[..., Awaitable[ToolResult]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> ToolResult:
            operation_id = new_operation_id()
            token = _current_operation_id.set(operation_id)
            try:
                return await _run(func, name, operation_id, args, kwargs)
            finally:
                _current_operation_id.reset(token)

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
    except Exception as exc:  # noqa: BLE001 - every failure must leave as an envelope
        # Only the type name is logged: the exception text and the stack may
        # hold page data or a secret that was never registered.
        _log.error("tool %s failed with %s", name, type(exc).__name__)
        envelope = _internal_error(name, operation_id)
    try:
        return finalize(envelope), image
    except Exception as exc:  # noqa: BLE001
        _log.error("tool %s produced an invalid result: %s", name, type(exc).__name__)
        return finalize(_internal_error(name, operation_id)), None


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


# --- Operating log --------------------------------------------------------------


class RedactingFilter(logging.Filter):
    """Pass every formatted log message, and any exception text, through redaction."""

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
    """Send the operating log to standard error only, through redaction.

    Standard output carries the protocol; any other handler on the root
    logger is removed so nothing can write there.
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
