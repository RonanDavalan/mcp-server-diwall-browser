"""Attach to the operator's running browser and act only in the tabs the server opened.

Why this module exists:
    The debugging port gives anyone who reaches it the whole browser, with
    no password, so it must listen on loopback only; and the operator's
    default profile must never be the one attached. Once attached, a
    browser-level session sees every tab of the operator, their mail and
    their bank included: which tabs belong to the server is decided here,
    once, and nothing about any other tab is ever kept, returned or logged.
"""

from __future__ import annotations

import asyncio
import errno
import http.client
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .cdp import DEFAULT_TIMEOUT_S, CdpConnection, CdpError
from .operator_profile import environment_dirs
from .origin import Origin, OriginError, is_blank, origin_of_url, split_host_port, split_url
from .output import ToolError

LOOPBACK_HOST = "127.0.0.1"
VERSION_PATH = "/json/version"
HTTP_TIMEOUT_S = 3.0
MAX_VERSION_BYTES = 64 * 1024
MAX_PROC_NET_BYTES = 32 * 1024 * 1024
DEFAULT_NAVIGATION_TIMEOUT_S = 15.0
MAX_NAVIGATION_TIMEOUT_S = 60.0
BLANK = "about:blank"

# Default user data directory of each family, under the configuration
# directory. Since Chrome 136 the debugging port is ignored on it.
DEFAULT_DATA_DIRS: dict[str, tuple[str, ...]] = {
    "chrome": ("google-chrome",),
    "chromium": ("chromium",),
    "brave": ("BraveSoftware", "Brave-Browser"),
}

# Answer of the browser when a call names a session that no longer exists.
SESSION_NOT_FOUND = -32001

TCP_STATES = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
    "0C": "NEW_SYN_RECV",
}

_SLOT_RE = re.compile(r"[0-9]+:")
_IPV4_ENDPOINT_RE = re.compile(r"([0-9A-Fa-f]{8}):([0-9A-Fa-f]{4})")
_IPV6_ENDPOINT_RE = re.compile(r"([0-9A-Fa-f]{32}):([0-9A-Fa-f]{4})")
_STATE_RE = re.compile(r"[0-9A-Fa-f]{2}")
_NET_ERROR_RE = re.compile(r"[A-Za-z0-9_.:\-]{1,100}")
_LOOPBACK_V4 = ipaddress.IPv4Network("127.0.0.0/8")
_LOOPBACK_V6 = ipaddress.IPv6Address("::1")
_LOOPBACK_WS_HOSTS = frozenset({"127.0.0.1", "[::1]"})

_UNREACHABLE_MESSAGE = (
    "le navigateur ne répond pas sur le port de débogage du profil : l'opérateur doit le lancer "
    "avec --remote-debugging-port=<port> et --user-data-dir=<profil dédié>"
)
_NOT_LOOPBACK_MESSAGE = (
    "le port de débogage du navigateur est joignable hors de la boucle locale : quiconque l'atteint "
    "prend le contrôle du navigateur ; l'opérateur doit relancer le navigateur sans "
    "--remote-debugging-address"
)
_UNDETERMINED_MESSAGE = (
    "le serveur ne peut pas vérifier que le port de débogage n'écoute qu'en boucle locale : refus par précaution"
)
_DEFAULT_PROFILE_MESSAGE = (
    "le profil déclaré (browser.profile_dir) est le répertoire de données par défaut du navigateur "
    "ou se trouve dedans : le serveur ne s'attache jamais au profil par défaut de l'opérateur ; "
    "créer un répertoire dédié et lancer le navigateur avec --user-data-dir=<ce répertoire>"
)
_NOT_OWNED_MESSAGE = (
    "cet onglet n'existe pas, est fermé, ou n'a pas été ouvert par le serveur : "
    "utiliser un onglet de la liste du serveur ou en ouvrir un"
)

_log = logging.getLogger(__name__)


# --- Loopback check of the debugging port --------------------------------------------


class ProcNetError(ValueError):
    """A socket table of /proc/net could not be understood."""


@dataclass(frozen=True)
class Listener:
    """One socket of a /proc/net table: its local address, port and state."""

    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    port: int
    state: str


def parse_proc_net_tcp(text: str, *, ipv6: bool) -> list[Listener]:
    """Parse ``/proc/net/tcp`` (or ``tcp6`` when ``ipv6``).

    A malformed line raises instead of being skipped: the skipped line could
    be the very socket that listens on every interface. An empty text is a
    table with no socket; the kernel never writes one (the header is always
    there), it stands for the table of a kernel without IPv6.
    """
    lines = text.splitlines()
    if not lines:
        return []
    header = lines[0].split()
    if len(header) < 4 or header[0] != "sl" or header[1] != "local_address" or header[3] != "st":
        raise ProcNetError("line 1: not a socket table header")
    endpoint_re = _IPV6_ENDPOINT_RE if ipv6 else _IPV4_ENDPOINT_RE
    listeners = []
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split()
        if len(fields) < 4 or not _SLOT_RE.fullmatch(fields[0]):
            raise ProcNetError(f"line {number}: wrong fields")
        local = endpoint_re.fullmatch(fields[1])
        if local is None or endpoint_re.fullmatch(fields[2]) is None or not _STATE_RE.fullmatch(fields[3]):
            raise ProcNetError(f"line {number}: wrong fields")
        state = fields[3].upper()
        listeners.append(
            Listener(
                address=_decode_address(local.group(1), ipv6=ipv6),
                port=int(local.group(2), 16),
                state=TCP_STATES.get(state, state),
            )
        )
    return listeners


def _decode_address(hex_text: str, *, ipv6: bool) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    # The kernel prints each 32-bit word of the address as an integer in the
    # machine's byte order (little-endian on the usual hardware).
    raw = b"".join(
        int(hex_text[i : i + 8], 16).to_bytes(4, sys.byteorder) for i in range(0, len(hex_text), 8)
    )
    return ipaddress.IPv6Address(raw) if ipv6 else ipaddress.IPv4Address(raw)


def is_loopback(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """127.0.0.0/8, ::1 and ::ffff:127.0.0.0/8, nothing else."""
    if isinstance(address, ipaddress.IPv4Address):
        return address in _LOOPBACK_V4
    if address == _LOOPBACK_V6:
        return True
    mapped = address.ipv4_mapped
    return mapped is not None and mapped in _LOOPBACK_V4


def verify_loopback_listening(port: int, *, tcp_text: str | None, tcp6_text: str | None) -> None:
    """Refuse unless every socket listening on ``port`` is bound to loopback.

    ``None`` stands for a table that could not be read.
    """
    try:
        if tcp_text is None or tcp6_text is None:
            raise ProcNetError("table unreadable")
        listeners = parse_proc_net_tcp(tcp_text, ipv6=False) + parse_proc_net_tcp(tcp6_text, ipv6=True)
    except ProcNetError:
        raise ToolError("debug_endpoint_undetermined", _UNDETERMINED_MESSAGE) from None
    bound = [entry for entry in listeners if entry.port == port and entry.state == "LISTEN"]
    if not bound:
        raise ToolError("browser_unreachable", _UNREACHABLE_MESSAGE)
    if not all(is_loopback(entry.address) for entry in bound):
        raise ToolError("debug_endpoint_not_loopback", _NOT_LOOPBACK_MESSAGE)


def kernel_supports_ipv6() -> bool:
    """False only when the kernel refuses to create any IPv6 socket."""
    family = getattr(socket, "AF_INET6", None)
    if family is None:
        return True
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
    except OSError as exc:
        return exc.errno != errno.EAFNOSUPPORT
    probe.close()
    return True


def read_proc_net_tcp(
    *, directory: str = "/proc/net", ipv6_supported: Callable[[], bool] = kernel_supports_ipv6
) -> tuple[str | None, str | None]:
    """Read the IPv4 and IPv6 socket tables; ``None`` for a table that cannot be read.

    A kernel without IPv6 has no ``tcp6`` table and can hold no IPv6 socket;
    only in that case is the missing table read as an empty one.
    """
    tcp = _read_table(os.path.join(directory, "tcp"))
    tcp6_path = os.path.join(directory, "tcp6")
    tcp6 = _read_table(tcp6_path)
    if tcp6 is None and not os.path.lexists(tcp6_path) and not ipv6_supported():
        tcp6 = ""
    return tcp, tcp6


def _read_table(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_PROC_NET_BYTES + 1)
    except OSError:
        return None
    if not raw or len(raw) > MAX_PROC_NET_BYTES:
        return None
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return None


# --- Dedicated profile -------------------------------------------------------------------


def default_data_dirs(family: str, home: str, *, config_home: str | None = None) -> list[str]:
    """Where ``family`` keeps its default profile: under ~/.config, and under
    $XDG_CONFIG_HOME when the operator set it."""
    if family not in DEFAULT_DATA_DIRS:
        raise ValueError(f"unknown browser family: {family!r}")
    bases = [os.path.join(home, ".config")]
    if config_home and os.path.isabs(config_home):
        bases.append(config_home)
    return [os.path.join(base, *DEFAULT_DATA_DIRS[family]) for base in bases]


def check_profile_dir(family: str, profile_dir: str, home: str, *, config_home: str | None = None) -> None:
    """Refuse a profile directory that is, or lies inside, the family's default one."""
    real_profile = os.path.realpath(profile_dir)
    for default in default_data_dirs(family, home, config_home=config_home):
        real_default = os.path.realpath(default)
        inside = real_profile.startswith(real_default.rstrip(os.sep) + os.sep)
        if real_profile == real_default or inside:
            raise ToolError("profile_dir_is_default", _DEFAULT_PROFILE_MESSAGE)


# --- Attachment ------------------------------------------------------------------------


HttpGet = Callable[[int], tuple[int, bytes]]
ProcNetReader = Callable[[], tuple[str | None, str | None]]


def default_http_get(port: int, path: str = VERSION_PATH, *, timeout: float = HTTP_TIMEOUT_S) -> tuple[int, bytes]:
    """GET ``path`` on 127.0.0.1:``port``; the host is fixed, never taken from data."""
    connection = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read(MAX_VERSION_BYTES + 1)
    finally:
        connection.close()


def check_debugger_url(url: object, port: int) -> str:
    """Accept only ws://127.0.0.1:<port>/… or ws://[::1]:<port>/…"""
    try:
        if not isinstance(url, str):
            raise OriginError("malformed")
        parts = split_url(url)
    except OriginError:
        raise ToolError("debug_endpoint_undetermined", _UNDETERMINED_MESSAGE) from None
    if parts.scheme != "ws" or not parts.has_authority or parts.userinfo is not None:
        raise ToolError("debug_endpoint_undetermined", _UNDETERMINED_MESSAGE)
    try:
        host, port_text = split_host_port(parts.hostport)
    except OriginError:
        raise ToolError("debug_endpoint_undetermined", _UNDETERMINED_MESSAGE) from None
    if host not in _LOOPBACK_WS_HOSTS:
        raise ToolError("debug_endpoint_not_loopback", _NOT_LOOPBACK_MESSAGE)
    # The loopback check covered this port only; another one is unchecked.
    if port_text != str(port):
        raise ToolError("debug_endpoint_undetermined", _UNDETERMINED_MESSAGE)
    return url


class _BrowserProfile(Protocol):
    @property
    def browser(self) -> Any: ...


class BrowserAttachment:
    """A connection to the operator's browser, made only after every check passed."""

    def __init__(self, connection: CdpConnection, *, product: str, version: str, profile_dir_declared: str) -> None:
        self._connection = connection
        self._product = product
        self._version = version
        self._profile_dir_declared = profile_dir_declared

    @property
    def connection(self) -> CdpConnection:
        return self._connection

    @classmethod
    async def attach(
        cls,
        profile: _BrowserProfile,
        *,
        cdp_factory: Callable[[], CdpConnection] = CdpConnection,
        http_get: HttpGet = default_http_get,
        proc_net: ProcNetReader = read_proc_net_tcp,
        home: str | None = None,
        config_home: str | None = None,
    ) -> BrowserAttachment:
        """Check the port, the profile and the announced endpoint, then connect.

        The server never launches nor closes the operator's browser, and
        never relays its port.
        """
        settings = profile.browser
        port = settings.debug_port
        tcp_text, tcp6_text = proc_net()
        verify_loopback_listening(port, tcp_text=tcp_text, tcp6_text=tcp6_text)
        dirs = environment_dirs()
        check_profile_dir(
            settings.family,
            settings.profile_dir,
            dirs.home if home is None else home,
            config_home=dirs.config_home if config_home is None else config_home,
        )
        ws_url = check_debugger_url(await _announced_url(http_get, port), port)
        connection = cdp_factory()
        await connection.connect(ws_url)
        try:
            version = await connection.send("Browser.getVersion")
            await connection.send("Target.setDiscoverTargets", {"discover": True})
        except CdpError:
            await connection.close()
            raise ToolError("browser_unreachable", _UNREACHABLE_MESSAGE) from None
        except BaseException:
            await connection.close()
            raise
        product_text = version.get("product")
        product, _, number = (product_text if isinstance(product_text, str) else "").partition("/")
        return cls(connection, product=product, version=number, profile_dir_declared=settings.profile_dir)

    def describe(self) -> dict[str, str]:
        return {
            "product": self._product,
            "version": self._version,
            "profile_dir_declared": self._profile_dir_declared,
        }

    async def close(self) -> None:
        """Close the connection; the browser stays open."""
        await self._connection.close()


async def _announced_url(http_get: HttpGet, port: int) -> object:
    try:
        # A blocking request, kept off the event loop for its whole delay.
        status, body = await asyncio.to_thread(http_get, port)
    except (OSError, http.client.HTTPException) as exc:
        _log.debug("debugging endpoint unreachable (%s)", type(exc).__name__)
        raise ToolError("browser_unreachable", _UNREACHABLE_MESSAGE) from None
    if status != 200 or len(body) > MAX_VERSION_BYTES:
        raise ToolError("browser_unreachable", _UNREACHABLE_MESSAGE)
    try:
        info = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ToolError("browser_unreachable", _UNREACHABLE_MESSAGE) from None
    if not isinstance(info, dict) or "webSocketDebuggerUrl" not in info:
        raise ToolError("browser_unreachable", _UNREACHABLE_MESSAGE)
    return info["webSocketDebuggerUrl"]


# --- Tabs ------------------------------------------------------------------------------


@dataclass(eq=False)
class Tab:
    """A tab the server owns. ``target_id`` and ``session_id`` never leave the server."""

    tab_id: str
    adopted: bool
    target_id: str = field(repr=False)
    session_id: str = field(repr=False)
    main_frame_id: str | None = field(default=None, repr=False)
    # {"type", "message"} of an open dialog; the message is untrusted page data.
    dialog: dict[str, str] | None = field(default=None, repr=False)
    # Address of the frame that opened the dialog, as the browser reports it.
    dialog_url: str | None = field(default=None, repr=False)
    # Whether the main frame is loading, from the browser's loading events.
    loading: bool = field(default=False, repr=False)
    closed: bool = False


@dataclass(frozen=True)
class TabInfo:
    tab_id: str
    # Canonical origin, "about:blank", or None for an address without a web
    # origin (an error page, for instance).
    origin: str | None
    adopted: bool


@dataclass(frozen=True)
class FrameInfo:
    url: str
    origin: Origin | None
    loader_id: str
    frame_id: str


@dataclass(frozen=True)
class NavigationResult:
    """``loaded`` is False when the deadline passed first; a notice says so."""

    loaded: bool


def check_navigable(url: object) -> Origin:
    """The origin of an address the server may open: http or https only.

    Navigation is free on every site of the web; the list of allowed sites
    governs actions, not where a tab may go.
    """
    try:
        return origin_of_url(url)  # type: ignore[arg-type]
    except OriginError as exc:
        if exc.reason == "forbidden_scheme":
            raise ToolError("forbidden_scheme", "seules les adresses http et https peuvent être ouvertes") from None
        raise ToolError(
            "navigation_failed", f"adresse refusée ({exc.reason})", extra_server={"reason": exc.reason}
        ) from None


class DialogBlocking(Exception):
    """A page read gave up because a dialog blocks the page.

    While a dialog is open the page answers no call at all, not even the
    frame tree; a read waiting for it would only time out.
    """


_DIALOG_FIRST_MESSAGE = (
    "une boîte de dialogue est ouverte dans cet onglet : la traiter (handle_dialog) avant toute autre action"
)
HANDLE_DIALOG = "handle_dialog"


def dialog_refusal() -> ToolError:
    """The refusal of any action but handling the dialog while one is open.

    The dialog covers the whole page as long as it is open: the code is the
    one of an element covered by something else.
    """
    return ToolError("element_obscured", _DIALOG_FIRST_MESSAGE, action_expected=HANDLE_DIALOG)


def _navigation_timeout(timeout_s: object) -> float:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s >= 0:
        raise ValueError("timeout_s is a non-negative number of seconds")
    return min(float(timeout_s), MAX_NAVIGATION_TIMEOUT_S)


class TabRegistry:
    """The tabs the server opened, and the windows their pages opened.

    Every other target the browser announces is ignored on arrival: nothing
    of it is kept, so nothing of it can be returned or logged.
    """

    def __init__(self, connection: CdpConnection) -> None:
        self._connection = connection
        self._tabs: dict[str, Tab] = {}
        self._adopting: set[str] = set()
        self._notices: list[str] = []
        connection.add_event_handler(self._on_event)

    # --- public operations ---------------------------------------------------------

    async def open_tab(self, url: str | None = None) -> Tab:
        if url is not None:
            check_navigable(url)
        created = await self._connection.send("Target.createTarget", {"url": BLANK})
        target_id = created.get("targetId")
        if not isinstance(target_id, str) or not target_id:
            raise CdpError(0, "no target identifier in the answer")
        try:
            session_id = await self._connection.attach(target_id)
        except BaseException:
            await self._close_target_quietly(target_id)
            raise
        tab = self._register(target_id, session_id, adopted=False)
        try:
            await self._prepare(tab)
        except BaseException:
            self._forget(tab)
            await self._close_target_quietly(target_id)
            raise
        if url is not None:
            await self.navigate(tab, url)
        return tab

    def get(self, tab_id: str) -> Tab:
        tab = self._tabs.get(tab_id) if isinstance(tab_id, str) else None
        if tab is None or tab.closed:
            raise ToolError("tab_not_owned", _NOT_OWNED_MESSAGE)
        return tab

    async def list_tabs(self) -> list[TabInfo]:
        infos = []
        for tab in list(self._tabs.values()):
            try:
                frame = await self.main_frame_info(tab, stop_on_dialog=True)
            except ToolError as exc:
                if exc.code == "tab_not_owned":
                    continue
                raise
            except DialogBlocking:
                # The page answers nothing while a dialog is open; the dialog's
                # own address says where the tab is.
                frame = FrameInfo(url=tab.dialog_url or "", origin=_origin_or_none(tab.dialog_url), loader_id="", frame_id="")
            infos.append(TabInfo(tab_id=tab.tab_id, origin=origin_label(frame), adopted=tab.adopted))
        return infos

    async def close_tab(self, tab_id: str) -> None:
        tab = self.get(tab_id)
        try:
            await self._connection.send("Target.closeTarget", {"targetId": tab.target_id})
        except CdpError:
            pass  # already gone: forgetting it is all that is left to do
        finally:
            self._forget(tab)

    def drain_notices(self) -> list[str]:
        notices, self._notices = self._notices, []
        return notices

    async def navigate(
        self, tab: Tab, url: str, *, timeout_s: float = DEFAULT_NAVIGATION_TIMEOUT_S
    ) -> NavigationResult:
        """Load ``url`` and wait for the load event or for the main frame to stop loading.

        Never waits for a quiet network: a page that polls forever never
        reaches it. At the deadline the result is ``loaded=False`` and a
        notice is queued for ``drain_notices``; it is not an error.
        """
        self._require_owned(tab)
        check_navigable(url)
        limit = _navigation_timeout(timeout_s)
        session_id = tab.session_id
        frame_id = tab.main_frame_id

        def finished(method: str, params: dict[str, Any], event_session: str | None) -> bool:
            if event_session != session_id:
                return False
            if method == "Page.loadEventFired":
                return True
            return method == "Page.frameStoppedLoading" and params.get("frameId") == frame_id

        waiter = self._connection.wait_event(finished, timeout=limit)
        try:
            try:
                result = await self._tab_call(tab, "Page.navigate", {"url": url}, timeout=limit)
            except ToolError as exc:
                # A navigation that has not committed by the deadline is slow,
                # not a lost browser, as long as the connection is alive.
                if exc.code == "browser_unreachable" and not self._connection.closed:
                    return self._not_loaded(tab, limit)
                raise
            error_text = result.get("errorText")
            if error_text:
                raise ToolError(
                    "navigation_failed",
                    f"la navigation a échoué ({_net_error_label(error_text)})",
                    extra_server={"tab_id": tab.tab_id},
                )
            if not result.get("loaderId"):
                # Same-document navigation: there is no load to wait for.
                return NavigationResult(loaded=True)
            try:
                await waiter
            except TimeoutError:
                return self._not_loaded(tab, limit)
            return NavigationResult(loaded=True)
        finally:
            waiter.cancel()

    async def main_frame_info(self, tab: Tab, *, stop_on_dialog: bool = False) -> FrameInfo:
        """Address and origin of the main frame as the browser reports them now.

        Read on every call: a redirect or a navigation started by the page
        moves the origin without the server asking. With ``stop_on_dialog``,
        an open dialog raises ``DialogBlocking`` instead of a long wait.
        """
        self._require_owned(tab)
        if stop_on_dialog:
            tree = await self.read(tab, "Page.getFrameTree")
        else:
            tree = await self._tab_call(tab, "Page.getFrameTree")
        frame_tree = tree.get("frameTree")
        frame = frame_tree.get("frame") if isinstance(frame_tree, dict) else None
        if not isinstance(frame, dict):
            frame = {}
        url = frame.get("url") if isinstance(frame.get("url"), str) else ""
        fragment = frame.get("urlFragment")
        if isinstance(fragment, str):
            url += fragment
        try:
            origin: Origin | None = origin_of_url(url)
        except OriginError:
            origin = None
        loader_id = frame.get("loaderId")
        frame_id = frame.get("id")
        return FrameInfo(
            url=url,
            origin=origin,
            loader_id=loader_id if isinstance(loader_id, str) else "",
            frame_id=frame_id if isinstance(frame_id, str) else "",
        )

    async def read(
        self, tab: Tab, method: str, params: dict[str, Any] | None = None, *, timeout: float = DEFAULT_TIMEOUT_S
    ) -> dict[str, Any]:
        """One call on an owned tab that gives up as soon as a dialog blocks the page."""
        return await self._until_dialog(tab, lambda: self._tab_call(tab, method, params, timeout=timeout), timeout)

    async def run_script(
        self,
        tab: Tab,
        name: str,
        *,
        object_id: str | None = None,
        arguments: list[dict[str, Any]] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """An internal script on an owned tab, given up as soon as a dialog blocks the page."""

        async def call() -> dict[str, Any]:
            try:
                return await self._connection.call_internal(
                    name, session_id=tab.session_id, object_id=object_id, arguments=arguments, timeout=timeout
                )
            except CdpError as exc:
                if exc.code == SESSION_NOT_FOUND:
                    self._forget(tab)
                    raise ToolError("tab_not_owned", _NOT_OWNED_MESSAGE) from None
                raise

        return await self._until_dialog(tab, call, timeout)

    async def _until_dialog(
        self, tab: Tab, start: Callable[[], Awaitable[dict[str, Any]]], timeout: float
    ) -> dict[str, Any]:
        self._require_owned(tab)
        if tab.dialog is not None:
            raise DialogBlocking()
        session_id = tab.session_id
        dialog = self._connection.wait_event(
            lambda method_, params_, session: session == session_id and method_ == "Page.javascriptDialogOpening",
            timeout=timeout,
        )
        call = asyncio.ensure_future(start())
        dialog_task = asyncio.ensure_future(_awaited(dialog))
        try:
            done, _ = await asyncio.wait({call, dialog_task}, return_when=asyncio.FIRST_COMPLETED)
            if call in done:
                return call.result()
            if not dialog_task.cancelled() and dialog_task.exception() is None:
                raise DialogBlocking()
            return await call
        finally:
            dialog.cancel()
            for task in (call, dialog_task):
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - only the cancellation matters here
                        pass

    async def wait_loaded(self, tab: Tab, *, timeout_s: float = DEFAULT_NAVIGATION_TIMEOUT_S) -> bool:
        """True once the main frame is not loading, False at the deadline."""
        self._require_owned(tab)
        limit = _navigation_timeout(timeout_s)
        session_id = tab.session_id
        frame_id = tab.main_frame_id

        def stopped(method: str, params: dict[str, Any], event_session: str | None) -> bool:
            return (
                event_session == session_id
                and method == "Page.frameStoppedLoading"
                and params.get("frameId") == frame_id
            )

        waiter = self._connection.wait_event(stopped, timeout=limit)
        try:
            if not tab.loading:
                return True
            await waiter
            return True
        except TimeoutError:
            return False
        finally:
            waiter.cancel()

    def ensure_no_dialog(self, tab: Tab) -> None:
        """Refuse at once when a dialog blocks the page, instead of waiting for a call."""
        self._require_owned(tab)
        if tab.dialog is not None:
            raise dialog_refusal()

    async def handle_dialog(self, tab: Tab, accept: bool) -> None:
        """Accept or dismiss the open dialog. Whether to do so is decided by the caller."""
        self._require_owned(tab)
        if not isinstance(accept, bool):
            raise TypeError("accept is a boolean")
        await self._tab_call(tab, "Page.handleJavaScriptDialog", {"accept": accept})
        tab.dialog = None
        tab.dialog_url = None

    # --- internals ------------------------------------------------------------------

    def _register(self, target_id: str, session_id: str, *, adopted: bool) -> Tab:
        tab_id = secrets.token_hex(8)
        while tab_id in self._tabs:
            tab_id = secrets.token_hex(8)
        tab = Tab(tab_id=tab_id, adopted=adopted, target_id=target_id, session_id=session_id)
        self._tabs[tab_id] = tab
        return tab

    def _forget(self, tab: Tab) -> None:
        tab.closed = True
        tab.dialog = None
        tab.dialog_url = None
        if self._tabs.get(tab.tab_id) is tab:
            del self._tabs[tab.tab_id]

    def _require_owned(self, tab: Tab) -> None:
        if not isinstance(tab, Tab) or tab.closed or self._tabs.get(tab.tab_id) is not tab:
            raise ToolError("tab_not_owned", _NOT_OWNED_MESSAGE)

    def _tab_by_target(self, target_id: object) -> Tab | None:
        for tab in self._tabs.values():
            if tab.target_id == target_id:
                return tab
        return None

    def _tab_by_session(self, session_id: object) -> Tab | None:
        for tab in self._tabs.values():
            if tab.session_id == session_id:
                return tab
        return None

    async def _prepare(self, tab: Tab) -> None:
        await self._tab_call(tab, "Page.enable")
        await self._tab_call(tab, "Inspector.enable")
        info = await self.main_frame_info(tab)
        tab.main_frame_id = info.frame_id

    async def _tab_call(
        self, tab: Tab, method: str, params: dict[str, Any] | None = None, *, timeout: float = DEFAULT_TIMEOUT_S
    ) -> dict[str, Any]:
        try:
            return await self._connection.send(method, params, session_id=tab.session_id, timeout=timeout)
        except CdpError as exc:
            if exc.code == SESSION_NOT_FOUND:
                self._forget(tab)
                raise ToolError("tab_not_owned", _NOT_OWNED_MESSAGE) from None
            raise

    async def _close_target_quietly(self, target_id: str) -> None:
        try:
            await self._connection.send("Target.closeTarget", {"targetId": target_id})
        except (CdpError, ToolError):
            pass

    def _not_loaded(self, tab: Tab, limit: float) -> NavigationResult:
        self._notices.append(
            f"la page de l'onglet {tab.tab_id} n'a pas fini de charger en {limit:g} s : "
            "son contenu peut être incomplet"
        )
        return NavigationResult(loaded=False)

    def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> Any:
        if session_id is None:
            if method == "Target.targetCreated":
                return self._on_target_created(params)
            if method in ("Target.targetDestroyed", "Target.targetCrashed"):
                self._on_target_gone(params.get("targetId"))
            elif method == "Target.detachedFromTarget":
                tab = self._tab_by_session(params.get("sessionId"))
                if tab is not None:
                    self._forget(tab)
            return None
        tab = self._tab_by_session(session_id)
        if tab is None:
            return None
        if method == "Inspector.targetCrashed":
            self._forget(tab)
        elif method == "Page.javascriptDialogOpening":
            kind = params.get("type")
            message = params.get("message")
            url = params.get("url")
            tab.dialog = {
                "type": kind if isinstance(kind, str) else "",
                "message": message if isinstance(message, str) else "",
            }
            tab.dialog_url = url if isinstance(url, str) else None
        elif method == "Page.javascriptDialogClosed":
            tab.dialog = None
            tab.dialog_url = None
        elif method in ("Page.frameStartedLoading", "Page.frameStoppedLoading"):
            if params.get("frameId") == tab.main_frame_id:
                tab.loading = method == "Page.frameStartedLoading"
        return None

    def _on_target_created(self, params: dict[str, Any]) -> Any:
        # Only a page opened by the page of an owned tab is looked at further;
        # any other target is dropped here without keeping anything of it.
        info = params.get("targetInfo")
        if not isinstance(info, dict) or info.get("type") != "page":
            return None
        opener = self._tab_by_target(info.get("openerId"))
        target_id = info.get("targetId")
        if opener is None or not isinstance(target_id, str) or not target_id:
            return None
        if target_id in self._adopting or self._tab_by_target(target_id) is not None:
            return None
        self._adopting.add(target_id)
        return self._adopt(target_id, opener.tab_id)

    def _on_target_gone(self, target_id: object) -> None:
        tab = self._tab_by_target(target_id)
        if tab is not None:
            self._forget(tab)
        elif isinstance(target_id, str):
            self._adopting.discard(target_id)

    async def _adopt(self, target_id: str, opener_tab_id: str) -> None:
        try:
            session_id = await self._connection.attach(target_id)
        except (CdpError, ToolError) as exc:
            self._adopting.discard(target_id)
            _log.debug("a window opened by an owned tab could not be attached (%s)", type(exc).__name__)
            return
        if target_id not in self._adopting:
            return  # destroyed while attaching
        self._adopting.discard(target_id)
        tab = self._register(target_id, session_id, adopted=True)
        try:
            await self._prepare(tab)
        except (CdpError, ToolError) as exc:
            self._forget(tab)
            _log.debug("a window opened by an owned tab could not be prepared (%s)", type(exc).__name__)
            return
        self._notices.append(
            f"une fenêtre a été ouverte par la page de l'onglet {opener_tab_id} : "
            f"le serveur l'a adoptée comme onglet {tab.tab_id}"
        )


async def _awaited(waiter: Any) -> Any:
    return await waiter


def _origin_or_none(url: str | None) -> Origin | None:
    try:
        return origin_of_url(url or "")
    except OriginError:
        return None


def origin_label(frame: FrameInfo) -> str | None:
    if frame.origin is not None:
        return frame.origin.canonical
    if frame.url == "" or is_blank(frame.url):
        return BLANK
    return None


def _net_error_label(error_text: object) -> str:
    if isinstance(error_text, str) and _NET_ERROR_RE.fullmatch(error_text):
        return error_text
    return "erreur inconnue"

