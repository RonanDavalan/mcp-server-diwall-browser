"""A DevTools endpoint on loopback that records what it receives and answers from handlers.

Why this module exists:
    The CDP client and the tab registry must be tested without a browser,
    including the cases a browser rarely produces on demand: a connection
    lost mid-call, an answer that never comes, an event between a request
    and its answer, foreign tabs appearing. The fake listens for real on
    127.0.0.1, so the loopback check of /proc/net/tcp passes against it.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

# The fake's own frame log would show the parameters it receives; it is
# kept quiet so that log checks only ever see the production code.
_quiet = logging.getLogger("tests.fake_cdp.frames")
_quiet.setLevel(logging.CRITICAL)
_quiet.propagate = False

NO_REPLY = object()


class FakeCdpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Reply:
    """A result, followed by events sent after the answer."""

    result: dict[str, Any] = field(default_factory=dict)
    events_after: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)


Handler = Callable[[dict[str, Any], str | None], Any]


class FakeCdp:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.handlers: dict[str, Handler] = {}
        self.version_payload: dict[str, Any] | None = None
        self.connections: set[ServerConnection] = set()
        self.port = 0
        self._server: Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await serve(
            self._session,
            "127.0.0.1",
            0,
            process_request=self._http,
            compression=None,
            max_size=64 * 1024 * 1024,
            ping_interval=None,
            logger=_quiet,
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/browser/test"

    def methods(self) -> list[str]:
        return [message.get("method", "") for message in self.received]

    def on(self, method: str, result: Any = None, *, handler: Handler | None = None) -> None:
        """Answer ``method`` with ``result`` (a dict, a Reply, NO_REPLY), or by ``handler``."""
        if handler is None:
            fixed = {} if result is None else result

            def handler(params: dict[str, Any], session_id: str | None) -> Any:
                return fixed

        self.handlers[method] = handler

    async def emit(self, method: str, params: dict[str, Any] | None = None, *, session_id: str | None = None) -> None:
        message: dict[str, Any] = {"method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        text = json.dumps(message)
        for connection in list(self.connections):
            try:
                await connection.send(text)
            except ConnectionClosed:
                pass

    async def drop(self) -> None:
        """Close every client connection, as a browser that goes away."""
        for connection in list(self.connections):
            await connection.close()

    # --- server side -------------------------------------------------------------

    def _http(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path == "/json/version":
            payload = self.version_payload or {
                "Browser": "Chrome/154.0.0.0",
                "webSocketDebuggerUrl": self.ws_url,
            }
            return connection.respond(HTTPStatus.OK, json.dumps(payload))
        return None

    async def _session(self, connection: ServerConnection) -> None:
        self.connections.add(connection)
        try:
            async for raw in connection:
                message = json.loads(raw)
                self.received.append(message)
                task = asyncio.create_task(self._answer(connection, message))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except ConnectionClosed:
            pass
        finally:
            self.connections.discard(connection)

    async def _answer(self, connection: ServerConnection, message: dict[str, Any]) -> None:
        method = message.get("method", "")
        session_id = message.get("sessionId")
        handler = self.handlers.get(method)
        response: dict[str, Any] = {"id": message["id"]}
        if session_id is not None:
            response["sessionId"] = session_id
        events: list[tuple[str, dict[str, Any], str | None]] = []
        if handler is None:
            response["error"] = {"code": -32601, "message": f"'{method}' wasn't found"}
        else:
            try:
                outcome = handler(message.get("params", {}), session_id)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            except FakeCdpError as exc:
                response["error"] = {"code": exc.code, "message": exc.message}
                outcome = None
            if outcome is NO_REPLY:
                return
            if isinstance(outcome, Reply):
                events = outcome.events_after
                outcome = outcome.result
            if "error" not in response:
                response["result"] = outcome if outcome is not None else {}
        try:
            await connection.send(json.dumps(response))
            for event_method, params, event_session in events:
                await self.emit(event_method, params, session_id=event_session)
        except ConnectionClosed:
            pass


class FakeBrowser:
    """Browser behaviour on top of FakeCdp: targets, sessions, frames, navigation."""

    def __init__(self, cdp: FakeCdp) -> None:
        self.cdp = cdp
        self.targets: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, str] = {}
        self.navigation_error: str | None = None
        self.emit_load_events = True
        self.same_document = False
        self._target_ids = itertools.count(1)
        self._session_ids = itertools.count(1)
        self._loader_ids = itertools.count(1)
        cdp.on("Browser.getVersion", {"protocolVersion": "1.3", "product": "Chrome/154.0.0.0", "revision": "@test"})
        cdp.on("Target.setDiscoverTargets", {})
        cdp.on("Page.enable", {})
        cdp.on("Inspector.enable", {})
        cdp.on("Page.handleJavaScriptDialog", {})
        cdp.on("Target.createTarget", handler=self._create_target)
        cdp.on("Target.attachToTarget", handler=self._attach)
        cdp.on("Target.closeTarget", handler=self._close_target)
        cdp.on("Page.getFrameTree", handler=self._frame_tree)
        cdp.on("Page.navigate", handler=self._navigate)
        # Perception reads, answered from the simulated page shown in the target.
        self.after: dict[str, Callable[[str], None]] = {}
        cdp.on("Accessibility.getFullAXTree", handler=self._page_read(lambda page, params: page.ax_tree()))
        cdp.on("DOMSnapshot.captureSnapshot", handler=self._page_read(lambda page, params: page.dom_snapshot()))
        cdp.on("Page.getLayoutMetrics", handler=self._page_read(lambda page, params: page.layout_metrics()))
        cdp.on("Page.captureScreenshot", handler=self._page_read(lambda page, params: page.screenshot(params.get("clip"))))

    def new_target_id(self) -> str:
        return f"TARGET{next(self._target_ids):04d}"

    def session_of(self, target_id: str) -> str:
        for session_id, owner in self.sessions.items():
            if owner == target_id:
                return session_id
        raise KeyError(target_id)

    def set_url(self, target_id: str, url: str) -> None:
        self.targets[target_id]["url"] = url

    def show(self, target_id: str, page: Any, url: str | None = None) -> None:
        """Put a simulated page (fixtures.page_model.Page) in the target."""
        self.targets[target_id]["page"] = page
        if url is not None:
            self.targets[target_id]["url"] = url

    def _page_read(self, answer: Callable[[Any, dict[str, Any]], Any]) -> Callable[[dict[str, Any], str | None], Any]:
        def handler(params: dict[str, Any], session_id: str | None) -> Any:
            target_id = self._target_of(session_id)
            page = self.targets[target_id].get("page")
            if page is None:
                raise FakeCdpError(-32000, "no simulated page in this target")
            result = answer(page, params)
            method = next(name for name, h in self.cdp.handlers.items() if h is handler)
            hook = self.after.get(method)
            if hook is not None:
                hook(target_id)
            return result

        return handler

    async def announce(self, title: str, url: str, *, opener_id: str | None = None) -> str:
        """Emit the creation of a target, as discovery does for every tab of the browser."""
        target_id = self.new_target_id()
        self.targets[target_id] = {"url": url, "title": title}
        info: dict[str, Any] = {
            "targetId": target_id,
            "type": "page",
            "title": title,
            "url": url,
            "attached": False,
            "canAccessOpener": False,
            "browserContextId": "CONTEXT",
        }
        if opener_id is not None:
            info["openerId"] = opener_id
            info["openerFrameId"] = opener_id
        await self.cdp.emit("Target.targetCreated", {"targetInfo": info})
        await self.cdp.emit("Target.targetInfoChanged", {"targetInfo": info})
        return target_id

    def _create_target(self, params: dict[str, Any], session_id: str | None) -> Any:
        target_id = self.new_target_id()
        self.targets[target_id] = {"url": params.get("url", "about:blank"), "title": ""}
        return {"targetId": target_id}

    def _attach(self, params: dict[str, Any], session_id: str | None) -> Any:
        target_id = params.get("targetId")
        if target_id not in self.targets:
            raise FakeCdpError(-32602, "No target with given id found")
        new_session = f"SESSION{next(self._session_ids):04d}"
        self.sessions[new_session] = target_id
        return {"sessionId": new_session}

    def _close_target(self, params: dict[str, Any], session_id: str | None) -> Any:
        target_id = params.get("targetId")
        if self.targets.pop(target_id, None) is None:
            raise FakeCdpError(-32602, "No target with given id found")
        for session in [s for s, t in self.sessions.items() if t == target_id]:
            del self.sessions[session]
        return Reply({"success": True}, [("Target.targetDestroyed", {"targetId": target_id}, None)])

    def _target_of(self, session_id: str | None) -> str:
        target_id = self.sessions.get(session_id or "")
        if target_id is None or target_id not in self.targets:
            raise FakeCdpError(-32001, "Session with given id not found.")
        return target_id

    def _frame_tree(self, params: dict[str, Any], session_id: str | None) -> Any:
        target_id = self._target_of(session_id)
        url = self.targets[target_id]["url"]
        return {
            "frameTree": {
                "frame": {
                    "id": target_id,
                    "loaderId": self.targets[target_id].get("loader", "LOADER0"),
                    "url": url,
                    "securityOrigin": "://",
                    "mimeType": "text/html",
                }
            }
        }

    def _navigate(self, params: dict[str, Any], session_id: str | None) -> Any:
        target_id = self._target_of(session_id)
        loader = f"LOADER{next(self._loader_ids)}"
        result: dict[str, Any] = {"frameId": target_id, "loaderId": loader}
        if self.navigation_error is not None:
            result["errorText"] = self.navigation_error
            return result
        self.targets[target_id]["url"] = params["url"]
        self.targets[target_id]["loader"] = loader
        if self.same_document:
            del result["loaderId"]
            return result
        events: list[tuple[str, dict[str, Any], str | None]] = []
        if self.emit_load_events:
            events = [
                ("Page.loadEventFired", {"timestamp": 1.0}, session_id),
                ("Page.frameStoppedLoading", {"frameId": target_id}, session_id),
            ]
        return Reply(result, events)


async def settle(rounds: int = 5) -> None:
    """Let messages in flight on loopback be delivered and handled."""
    for _ in range(rounds):
        await asyncio.sleep(0.01)


Waitable = Callable[[], bool]


async def until(condition: Waitable, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)
