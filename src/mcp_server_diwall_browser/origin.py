"""Turn an address into an origin and compare origins.

Why this module exists:
    Every decision about where a credential may be typed or which site is
    allowed rests on comparing origins. Text comparison is wrong in both
    directions (default ports, case, userinfo tricks, numeric hosts), so the
    parsing lives here once and every other module asks this one.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

ORIGIN_ERROR_REASONS = frozenset(
    {
        "forbidden_scheme",
        "userinfo",
        "empty_host",
        "invalid_host",
        "invalid_port",
        "control_character",
        "wildcard",
        "trailing_data",
        "malformed",
    }
)

_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*):(.*)", re.DOTALL)
_PORT_RE = re.compile(r"[0-9]{1,5}")
_DOTTED_QUAD_RE = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}")
_NUMERIC_LABEL_RE = re.compile(r"[0-9]+|0x[0-9a-f]*")
_LABEL_RE = re.compile(r"[a-z0-9_\-]{1,63}")
_MAX_HOST_LENGTH = 253


class OriginError(ValueError):
    """An address was refused; ``reason`` is a stable English word."""

    def __init__(self, reason: str) -> None:
        if reason not in ORIGIN_ERROR_REASONS:
            raise ValueError(f"unknown origin error reason: {reason!r}")
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Origin:
    """A web origin with its effective port; equal when all three fields are."""

    scheme: str
    host: str
    port: int

    def __post_init__(self) -> None:
        # A hand-built Origin must not be able to smuggle a value that
        # parse_origin would have refused or written differently.
        if self.scheme not in DEFAULT_PORTS:
            raise OriginError("forbidden_scheme")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise OriginError("invalid_port")
        if not 1 <= self.port <= 65535:
            raise OriginError("invalid_port")
        if not isinstance(self.host, str) or not self.host:
            raise OriginError("empty_host")
        if self.host != _normalize_host(self.host, bracketed=":" in self.host):
            raise OriginError("invalid_host")

    @property
    def canonical(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        if self.port == DEFAULT_PORTS[self.scheme]:
            return f"{self.scheme}://{host}"
        return f"{self.scheme}://{host}:{self.port}"

    def __str__(self) -> str:
        return self.canonical


@dataclass(frozen=True)
class UrlParts:
    """The raw pieces of an address, without any normalization of the host."""

    scheme: str
    has_authority: bool
    userinfo: str | None
    hostport: str
    path: str
    query: str | None
    fragment: str | None


def split_url(url: str) -> UrlParts:
    """Cut an address into its pieces; refuse anything a browser could read
    differently from this code (control characters, spaces, backslashes)."""
    if not isinstance(url, str):
        raise OriginError("malformed")
    for char in url:
        code = ord(char)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            raise OriginError("control_character")
        if char.isspace() or char == "\\":
            raise OriginError("malformed")
    match = _SCHEME_RE.fullmatch(url)
    if match is None:
        raise OriginError("malformed")
    scheme = match.group(1).lower()
    rest = match.group(2)

    rest, fragment = _cut(rest, "#")
    rest, query = _cut(rest, "?")
    if rest.startswith("//"):
        rest = rest[2:]
        slash = rest.find("/")
        authority, path = (rest, "") if slash < 0 else (rest[:slash], rest[slash:])
        userinfo, at, hostport = authority.rpartition("@")
        return UrlParts(
            scheme=scheme,
            has_authority=True,
            userinfo=userinfo if at else None,
            hostport=hostport,
            path=path,
            query=query,
            fragment=fragment,
        )
    return UrlParts(
        scheme=scheme,
        has_authority=False,
        userinfo=None,
        hostport="",
        path=rest,
        query=query,
        fragment=fragment,
    )


def parse_origin(text: str) -> Origin:
    """Parse a list entry: scheme, host and optional port, nothing else."""
    parts, origin = _parse(text)
    if parts.path or parts.query is not None or parts.fragment is not None:
        raise OriginError("trailing_data")
    return origin


def origin_of_url(url: str) -> Origin:
    """Origin of a full address; path, query and fragment are ignored."""
    return _parse(url)[1]


def is_blank(url: str) -> bool:
    return url == "about:blank"


def origin_allowed(origin: Origin | None, allowed: Iterable[Origin]) -> bool:
    """True only for an origin equal to one of ``allowed``.

    No origin (about:blank, an address that cannot be read) and an empty
    list allow nothing: the default protects, it never permits.
    """
    return isinstance(origin, Origin) and any(origin == item for item in allowed)


def form_target_origin(page_url: str, action: str | None) -> Origin | None:
    """The origin a form sends to: its ``action`` read against the page address.

    ``None`` when it cannot be read or is not a web origin (a script address
    for one); an empty or relative action sends to the page's own origin.
    """
    try:
        page: Origin | None = origin_of_url(page_url)
    except OriginError:
        page = None
    target = (action or "").strip()
    if not target:
        return page
    try:
        if target.startswith("//"):
            return None if page is None else origin_of_url(f"{page.scheme}:{target}")
        if _SCHEME_RE.fullmatch(target):
            return origin_of_url(target)
    except OriginError:
        return None
    return page


def split_host_port(hostport: str) -> tuple[str, str | None]:
    """Host (an IPv6 address keeps its brackets) and port text of an authority."""
    return _split_hostport(hostport)


def _parse(text: str) -> tuple[UrlParts, Origin]:
    parts = split_url(text)
    if parts.scheme not in DEFAULT_PORTS:
        raise OriginError("forbidden_scheme")
    if not parts.has_authority:
        raise OriginError("malformed")
    if parts.userinfo is not None:
        raise OriginError("userinfo")
    if "*" in parts.hostport:
        raise OriginError("wildcard")
    host_text, port_text = _split_hostport(parts.hostport)
    if not host_text:
        raise OriginError("empty_host")
    bracketed = host_text.startswith("[")
    if bracketed:
        host_text = host_text[1:-1]
    host = _normalize_host(host_text, bracketed=bracketed)
    if port_text is None:
        port = DEFAULT_PORTS[parts.scheme]
    else:
        if not _PORT_RE.fullmatch(port_text):
            raise OriginError("invalid_port")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise OriginError("invalid_port")
    return parts, Origin(parts.scheme, host, port)


def _cut(text: str, sep: str) -> tuple[str, str | None]:
    head, found, tail = text.partition(sep)
    return (head, tail) if found else (head, None)


def _split_hostport(hostport: str) -> tuple[str, str | None]:
    if hostport.startswith("["):
        end = hostport.find("]")
        if end < 0:
            raise OriginError("invalid_host")
        host, after = hostport[: end + 1], hostport[end + 1 :]
        if not after:
            return host, None
        if not after.startswith(":"):
            raise OriginError("malformed")
        return host, after[1:]
    host, colon, port = hostport.partition(":")
    if "[" in host or "]" in host:
        raise OriginError("invalid_host")
    return host, (port if colon else None)


def _ascii_host(host: str) -> str:
    """The punycode form of a non-Latin host, only when it is unambiguous.

    Python converts with IDNA 2003, browsers with IDNA 2008. They disagree
    on a few characters (``ß``, ``ς``, the joiners) and Python silently maps
    others: ``faß.test`` becomes ``fass.test``, another domain than the one
    the browser opens. A name that does not come back unchanged from the
    conversion is refused, so a disagreement can only refuse, never allow.
    """
    try:
        converted = host.encode("idna").decode("ascii").lower()
        returned = converted.encode("ascii").decode("idna")
    except UnicodeError as exc:
        raise OriginError("invalid_host") from exc
    if returned != unicodedata.normalize("NFC", host):
        raise OriginError("invalid_host")
    return converted


def _normalize_host(host: str, *, bracketed: bool) -> str:
    if bracketed:
        # A zone identifier names a local interface, never a web origin.
        if "%" in host:
            raise OriginError("invalid_host")
        try:
            return ipaddress.IPv6Address(host).compressed
        except ValueError as exc:
            raise OriginError("invalid_host") from exc

    host = host.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        raise OriginError("empty_host")
    if not host.isascii():
        host = _ascii_host(host)

    labels = host.split(".")
    # A host whose last label is a number is an IPv4 address to a browser,
    # whatever its spelling; only the plain dotted quad is accepted.
    if _NUMERIC_LABEL_RE.fullmatch(labels[-1]):
        if not _DOTTED_QUAD_RE.fullmatch(host):
            raise OriginError("invalid_host")
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError as exc:
            raise OriginError("invalid_host") from exc

    if len(host) > _MAX_HOST_LENGTH:
        raise OriginError("invalid_host")
    for label in labels:
        if not _LABEL_RE.fullmatch(label):
            raise OriginError("invalid_host")
        if label.startswith("xn--"):
            try:
                label.encode("ascii").decode("idna")
            except UnicodeError as exc:
                raise OriginError("invalid_host") from exc
    return host
