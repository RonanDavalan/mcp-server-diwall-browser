"""The only module that talks to the browser, over the DevTools protocol.

Why this module exists:
    The debugging connection gives full control of the operator's browser,
    every tab included. Every call the project makes passes through one
    method that checks it against a list fixed in code before a byte is
    sent, never logs parameters (typed text may be a secret), and runs page
    scripts only from a fixed table, so that no text from the model or from
    a page ever becomes code.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .origin import OriginError, is_blank, origin_of_url
from .output import ToolError

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_S = 30.0
OPEN_TIMEOUT_S = 10.0

# Every method the server may send. A method missing here cannot be called,
# whatever the caller; adding one is a reviewed change of this file.
ALLOWED_METHODS = frozenset(
    {
        # Attachment and tab ownership (browser.py).
        "Browser.getVersion",
        "Target.setDiscoverTargets",
        "Target.createTarget",
        "Target.attachToTarget",
        "Target.closeTarget",
        "Page.enable",
        "Inspector.enable",
        "Page.navigate",
        "Page.getFrameTree",
        "Page.handleJavaScriptDialog",
        # Perception: the reads whose results the browser tests pin down.
        "Accessibility.getFullAXTree",
        "DOMSnapshot.captureSnapshot",
        "Page.getLayoutMetrics",
        "Page.captureScreenshot",
        "DOM.getNodeForLocation",
        "DOM.describeNode",
        # Typing into a field.
        "DOM.focus",
        "Input.insertText",
        "Input.dispatchKeyEvent",
        # Page scripts: only through call_internal, from INTERNAL_SCRIPTS.
        "Runtime.evaluate",
        "Runtime.callFunctionOn",
    }
)

ScriptKind = Literal["evaluate", "call_function_on"]

# Method that runs a script, the kind of script it takes, the parameter that
# carries the script text, and every parameter call_internal may set.
_SCRIPT_METHODS: dict[str, tuple[str, str, frozenset[str]]] = {
    "Runtime.evaluate": ("evaluate", "expression", frozenset({"expression", "returnByValue"})),
    "Runtime.callFunctionOn": (
        "call_function_on",
        "functionDeclaration",
        frozenset({"functionDeclaration", "objectId", "arguments", "returnByValue"}),
    ),
}

# Methods that load an address: a javascript: address is a script too.
_ADDRESS_METHODS = frozenset({"Page.navigate", "Target.createTarget"})

_UNREACHABLE_MESSAGE = "le navigateur ne répond pas : vérifier qu'il est lancé avec le port de débogage du profil"
_TIMEOUT_MESSAGE = "le navigateur n'a pas répondu dans le délai"

_log = logging.getLogger(__name__)

# The websockets library logs every frame, parameters included, at DEBUG
# level. It writes to this logger, held above DEBUG whatever the root level.
_frame_log = logging.getLogger(__name__ + ".frames")
_frame_log.setLevel(logging.WARNING)


@dataclass(frozen=True)
class InternalScript:
    """A page script written in this code base; never built from data."""

    kind: ScriptKind
    text: str

    def __post_init__(self) -> None:
        if self.kind not in ("evaluate", "call_function_on"):
            raise ValueError("an internal script is evaluate or call_function_on")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("an internal script has a non-empty text")


# Name -> script. Read-only at run time; scripts are added by editing this
# literal, and every text is a plain string with no value formatted in.
INTERNAL_SCRIPTS: Mapping[str, InternalScript] = MappingProxyType({})


class CdpError(Exception):
    """The browser answered a call with a protocol error."""

    def __init__(self, code: int, message: str) -> None:
        # Only the code goes into the exception text; the message stays an
        # attribute so that printing the exception repeats nothing.
        super().__init__(code)
        self.code = code
        self.message = message


class DisallowedMethodError(Exception):
    """A call this module refuses to send: a programming error, never a tool error."""


class UnknownScriptError(DisallowedMethodError):
    """call_internal was given a name that is not in the script table."""


Event = tuple[str, dict[str, Any], str | None]
EventHandler = Callable[[str, dict[str, Any], str | None], Awaitable[None] | None]
EventPredicate = Callable[[str, dict[str, Any], str | None], bool]


def _unreachable(message: str = _UNREACHABLE_MESSAGE) -> ToolError:
    return ToolError("browser_unreachable", message)


class EventWaiter:
    """Waits for the first event that satisfies a predicate.

    It listens from the moment it is created, so a caller can start waiting
    before sending the call that triggers the event. Awaiting it returns
    ``(method, params, session_id)``; it raises ``TimeoutError`` at the
    deadline and ``browser_unreachable`` if the connection closes.
    """

    def __init__(self, connection: CdpConnection, predicate: EventPredicate, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        self._connection = connection
        self._predicate = predicate
        self._deadline = loop.time() + timeout
        self._future: asyncio.Future[Event] = loop.create_future()
        # A failure nobody awaits must not be reported as never retrieved.
        self._future.add_done_callback(lambda f: f.cancelled() or f.exception())

    def _offer(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if self._future.done():
            return
        try:
            matched = self._predicate(method, params, session_id)
        except Exception as exc:  # noqa: BLE001 - a bad predicate must not stop the reader
            _log.debug("event predicate failed with %s", type(exc).__name__)
            return
        if matched:
            self._future.set_result((method, params, session_id))

    def _fail(self) -> None:
        if not self._future.done():
            self._future.set_exception(_unreachable())

    def cancel(self) -> None:
        self._connection._waiters.discard(self)
        self._future.cancel()

    def __await__(self) -> Generator[Any, None, Event]:
        return self._wait().__await__()

    async def _wait(self) -> Event:
        try:
            async with asyncio.timeout_at(self._deadline):
                return await self._future
        finally:
            self._connection._waiters.discard(self)


class CdpConnection:
    """One WebSocket connection to the browser; every call goes through ``send``."""

    def __init__(self, *, internal_scripts: Mapping[str, InternalScript] | None = None) -> None:
        # The table can be replaced here, and only here, so that tests run
        # their own scripts; nothing in the configuration reaches it.
        table = INTERNAL_SCRIPTS if internal_scripts is None else internal_scripts
        for name, script in table.items():
            if not isinstance(name, str) or not isinstance(script, InternalScript):
                raise TypeError("the script table maps names to InternalScript values")
        self._scripts: Mapping[str, InternalScript] = MappingProxyType(dict(table))
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._handlers: list[EventHandler] = []
        self._waiters: set[EventWaiter] = set()
        self._tasks: set[asyncio.Future[Any]] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._ws is None or self._closed

    async def connect(self, ws_url: str) -> None:
        if self._ws is not None:
            raise RuntimeError("the connection is already open")
        try:
            # proxy=None: the library would otherwise route through the
            # proxy named in the environment, and so hand the browser over.
            self._ws = await connect(
                ws_url,
                proxy=None,
                max_size=MAX_MESSAGE_BYTES,
                compression=None,
                ping_interval=None,
                open_timeout=OPEN_TIMEOUT_S,
                logger=_frame_log,
            )
        except (OSError, TimeoutError, WebSocketException) as exc:
            _log.debug("cdp connection failed with %s", type(exc).__name__)
            raise _unreachable() from None
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        """Close this connection; the browser itself is left running."""
        self._closed = True
        ws = self._ws
        if ws is not None:
            await ws.close()
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
        for task in list(self._tasks):
            task.cancel()
        self._fail_all()

    def add_event_handler(self, callback: EventHandler) -> None:
        """Call ``callback(method, params, session_id)`` for every event.

        A callback runs inside the reader and must return quickly; if it
        returns an awaitable, that awaitable runs as a separate task, so it
        may itself call ``send``.
        """
        self._handlers.append(callback)

    def remove_event_handler(self, callback: EventHandler) -> None:
        self._handlers.remove(callback)

    def wait_event(self, predicate: EventPredicate, *, timeout: float) -> EventWaiter:
        waiter = EventWaiter(self, predicate, timeout)
        if self.closed:
            waiter._fail()
        else:
            self._waiters.add(waiter)
        return waiter

    async def send(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        _script: InternalScript | None = None,
    ) -> dict[str, Any]:
        """Send one call and return its ``result``.

        ``_script`` is private to ``call_internal``: a script method is
        refused without it, and its text must be the table's text.
        """
        payload = dict(params) if params is not None else {}
        self._check_call(method, payload, _script)
        return await self._exchange(method, payload, session_id, timeout)

    async def attach(self, target_id: str) -> str:
        """Open a flat session on a target and return its identifier."""
        result = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise CdpError(0, "no session identifier in the answer")
        return session_id

    async def call_internal(
        self,
        name: str,
        *,
        session_id: str,
        object_id: str | None = None,
        arguments: Sequence[Mapping[str, Any]] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Run the table script ``name``; values travel as protocol arguments, never in the text."""
        script = self._scripts.get(name) if isinstance(name, str) else None
        if script is None:
            raise UnknownScriptError(f"unknown internal script: {name!r}")
        if script.kind == "evaluate":
            if object_id is not None or arguments:
                raise DisallowedMethodError("an evaluated script takes no object and no arguments")
            params: dict[str, Any] = {"expression": script.text, "returnByValue": True}
            method = "Runtime.evaluate"
        else:
            if not isinstance(object_id, str) or not object_id:
                raise DisallowedMethodError("a function script runs on an object")
            params = {
                "functionDeclaration": script.text,
                "objectId": object_id,
                "arguments": _protocol_arguments(arguments),
                "returnByValue": True,
            }
            method = "Runtime.callFunctionOn"
        return await self.send(method, params, session_id=session_id, timeout=timeout, _script=script)

    # --- checks -----------------------------------------------------------------

    def _check_call(self, method: str, params: dict[str, Any], script: InternalScript | None) -> None:
        if not isinstance(method, str) or method not in ALLOWED_METHODS:
            raise DisallowedMethodError(f"method not allowed: {method!r}")
        script_method = _SCRIPT_METHODS.get(method)
        if script_method is None:
            if script is not None:
                raise DisallowedMethodError("an internal script goes with a script method only")
            if method in _ADDRESS_METHODS:
                _check_address(method, params.get("url"))
            return
        kind, text_field, allowed_params = script_method
        if script is None:
            raise DisallowedMethodError(f"{method} is only sent through call_internal")
        if not any(script is known for known in self._scripts.values()):
            raise DisallowedMethodError("the script is not in the table")
        if script.kind != kind:
            raise DisallowedMethodError(f"a {script.kind} script cannot be sent with {method}")
        text = params.get(text_field)
        if not isinstance(text, str) or text != script.text:
            raise DisallowedMethodError("the script text is not exactly the table's text")
        if not set(params) <= allowed_params:
            raise DisallowedMethodError(f"unexpected parameter for {method}")

    # --- transport --------------------------------------------------------------

    async def _exchange(
        self, method: str, params: dict[str, Any], session_id: str | None, timeout: float
    ) -> dict[str, Any]:
        ws = self._ws
        if ws is None or self._closed:
            raise _unreachable()
        message_id = next(self._ids)
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params}
        if session_id is not None:
            message["sessionId"] = session_id
        text = json.dumps(message, allow_nan=False)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        _log.debug("cdp call %s #%d", method, message_id)
        try:
            await ws.send(text)
            response = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            _log.debug("cdp call %s #%d timed out", method, message_id)
            raise _unreachable(_TIMEOUT_MESSAGE) from None
        except (ConnectionClosed, OSError) as exc:
            _log.debug("cdp call %s #%d lost the connection (%s)", method, message_id, type(exc).__name__)
            raise _unreachable() from None
        finally:
            self._pending.pop(message_id, None)
        error = response.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            text_message = error.get("message") if isinstance(error, dict) else None
            code = code if isinstance(code, int) and not isinstance(code, bool) else 0
            _log.debug("cdp call %s #%d failed with code %d", method, message_id, code)
            raise CdpError(code, text_message if isinstance(text_message, str) else "")
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    async def _read_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for raw in ws:
                self._dispatch(raw)
        except ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001 - whatever stops the reader closes the connection
            _log.debug("cdp reader stopped with %s", type(exc).__name__)
        finally:
            self._closed = True
            self._fail_all()

    def _dispatch(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            _log.debug("cdp message is not JSON")
            return
        if not isinstance(message, dict):
            return
        if "id" in message:
            message_id = message["id"]
            future = self._pending.get(message_id) if isinstance(message_id, int) else None
            if future is not None and not future.done():
                future.set_result(message)
            return
        method = message.get("method")
        params = message.get("params", {})
        session_id = message.get("sessionId")
        if not isinstance(method, str) or (session_id is not None and not isinstance(session_id, str)):
            return
        if not isinstance(params, dict):
            params = {}
        _log.debug("cdp event %s", method)
        for waiter in list(self._waiters):
            waiter._offer(method, params, session_id)
        for handler in list(self._handlers):
            try:
                outcome = handler(method, params, session_id)
            except Exception as exc:  # noqa: BLE001 - one handler must not starve the others
                _log.warning("cdp event handler failed with %s", type(exc).__name__)
                continue
            if inspect.isawaitable(outcome):
                task = asyncio.ensure_future(outcome)
                self._tasks.add(task)
                task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Future[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _log.warning("cdp event task failed with %s", type(task.exception()).__name__)

    def _fail_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(_unreachable())
        for waiter in list(self._waiters):
            waiter._fail()
        self._waiters.clear()


def _check_address(method: str, url: object) -> None:
    # The tab module refuses these first, with a tool error; this is the
    # last line, for a caller that forgot.
    if isinstance(url, str) and is_blank(url):
        return
    try:
        origin_of_url(url)  # type: ignore[arg-type]
    except OriginError:
        raise DisallowedMethodError(f"{method} only loads http, https or about:blank addresses") from None


def _protocol_arguments(arguments: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for argument in arguments or ():
        if not isinstance(argument, Mapping) or len(argument) != 1:
            raise DisallowedMethodError("each argument is {'value': ...} or {'objectId': ...}")
        if "value" in argument:
            result.append({"value": argument["value"]})
        elif isinstance(argument.get("objectId"), str):
            result.append({"objectId": argument["objectId"]})
        else:
            raise DisallowedMethodError("each argument is {'value': ...} or {'objectId': ...}")
    return result
