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

from mcp_server_diwall_browser.cdp import INTERNAL_SCRIPTS

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
        # When it returns True for (method, session), the call is never answered.
        self.block: Callable[[str, str | None], bool] | None = None
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
        if self.block is not None and self.block(method, session_id):
            return
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
        # Address -> builder of the simulated page a navigation to it shows.
        self.site: dict[str, Callable[[], Any]] = {}
        self.emit_load_events = True
        self.same_document = False
        self._target_ids = itertools.count(1)
        self._session_ids = itertools.count(1)
        self._loader_ids = itertools.count(1)
        cdp.on("Browser.getVersion", {"protocolVersion": "1.3", "product": "Chrome/154.0.0.0", "revision": "@test"})
        cdp.on("Target.setDiscoverTargets", {})
        cdp.on("Page.enable", {})
        cdp.on("Inspector.enable", {})
        self.dialogs: dict[str, str] = {}
        self.accepted: list[bool] = []
        cdp.on("Page.handleJavaScriptDialog", handler=self._handle_dialog)
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
        # Action calls, answered from the simulated page as a browser would.
        self.focus: dict[str, int] = {}
        # Where the focus really goes when an element is focused: a page
        # script that moves it elsewhere.
        self.focus_redirect: dict[int, int] = {}
        self.objects: dict[str, Any] = {}
        self.mouse: list[tuple[str, dict[str, Any]]] = []
        self.keys: list[tuple[str, dict[str, Any]]] = []
        self.typed: list[tuple[str, int | None, str]] = []
        self.clicks: list[tuple[str, int | None]] = []
        self.dom_events: list[tuple[str, int, str]] = []
        self.download_behaviors: list[dict[str, Any]] = []
        self.on_click: Callable[[str, int | None], Any] | None = None
        self.on_key: Callable[[str, int | None, dict[str, Any]], Any] | None = None
        self.on_focus: Callable[[str, int], Any] | None = None
        self._selected: set[str] = set()
        for method, answer in {
            "DOM.describeNode": self._describe_node,
            "DOM.resolveNode": self._resolve_node,
            "Accessibility.getPartialAXTree": self._partial_ax_tree,
            "DOM.scrollIntoViewIfNeeded": self._scroll_into_view,
            "DOM.getContentQuads": self._content_quads,
            "DOM.getNodeForLocation": self._node_for_location,
            "DOM.focus": self._focus,
            "Runtime.callFunctionOn": self._call_function_on,
            "Runtime.evaluate": self._evaluate,
            "Input.dispatchMouseEvent": self._mouse_event,
            "Input.dispatchKeyEvent": self._key_event,
            "Input.insertText": self._insert_text,
        }.items():
            cdp.on(method, handler=self._node_call(answer))
        cdp.on("Browser.setDownloadBehavior", handler=self._download_behavior)

    def new_target_id(self) -> str:
        return f"TARGET{next(self._target_ids):04d}"

    def session_of(self, target_id: str) -> str:
        for session_id, owner in self.sessions.items():
            if owner == target_id:
                return session_id
        raise KeyError(target_id)

    def set_url(self, target_id: str, url: str) -> None:
        self.targets[target_id]["url"] = url

    async def open_dialog(self, target_id: str, message: str, kind: str = "confirm") -> None:
        """Open a page dialog: as measured, every call of that page then waits, but the answer to the dialog."""
        session_id = self.session_of(target_id)
        self.dialogs[session_id] = message

        def blocked(method: str, session: str | None) -> bool:
            return session in self.dialogs and method != "Page.handleJavaScriptDialog"

        self.cdp.block = blocked
        url = self.targets[target_id]["url"]
        await self.cdp.emit("Page.javascriptDialogOpening", {"url": url, "message": message, "type": kind, "hasBrowserHandler": True, "defaultPrompt": ""}, session_id=session_id)

    def _handle_dialog(self, params: dict[str, Any], session_id: str | None) -> Any:
        self.accepted.append(bool(params.get("accept")))
        if session_id not in self.dialogs:
            return {}  # a dialog announced by a bare event, without the blocking
        del self.dialogs[session_id]
        return Reply({}, [("Page.javascriptDialogClosed", {"result": bool(params.get("accept")), "userInput": ""}, session_id)])

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

    # --- action calls ------------------------------------------------------------------

    def _node_call(self, answer: Callable[[str, Any, dict[str, Any]], Any]) -> Callable[[dict[str, Any], str | None], Any]:
        def handler(params: dict[str, Any], session_id: str | None) -> Any:
            target_id = self._target_of(session_id)
            page = self.targets[target_id].get("page")
            if page is None:
                raise FakeCdpError(-32000, "no simulated page in this target")
            return answer(target_id, page, params)

        return handler

    @staticmethod
    def _node(page: Any, backend_id: object) -> Any:
        element = page.find(backend_id) if isinstance(backend_id, int) else None
        if element is None:
            raise FakeCdpError(-32000, "No node with given id found")
        return element

    def _object(self, object_id: object) -> Any:
        element = self.objects.get(object_id) if isinstance(object_id, str) else None
        if element is None:
            raise FakeCdpError(-32000, "Could not find object with given id")
        return element

    def _object_of(self, element: Any) -> str:
        object_id = f"node-{element.backend_id}" if element.backend_id else f"node-detached-{id(element)}"
        self.objects[object_id] = element
        return object_id

    @staticmethod
    def hit(page: Any, x: float, y: float) -> Any:
        """The element drawn last at a point of the viewport: the one a click there reaches."""
        px, py = x + page.viewport[0], y + page.viewport[1]
        found = None
        for element in page.prepare()._elements():
            if element.tag == "#text" or element.box is None or element.visibility != "visible" or element.opacity == "0":
                continue
            bx, by, bw, bh = element.box
            if bx <= px < bx + bw and by <= py < by + bh:
                found = element
        return found

    def _describe_node(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        element = self._node(page, params.get("backendNodeId"))
        node_type = 3 if element.tag == "#text" else 1
        return {"node": {"nodeId": 0, "backendNodeId": element.backend_id, "nodeType": node_type, "nodeName": element.tag, "localName": element.tag.lower(), "nodeValue": ""}}

    def _resolve_node(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        element = self._node(page, params.get("backendNodeId"))
        return {"object": {"type": "object", "subtype": "node", "className": "HTMLElement", "objectId": self._object_of(element)}}

    def _partial_ax_tree(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        backend_id = params.get("backendNodeId")
        self._node(page, backend_id)
        return {"nodes": [node for node in page.ax_tree()["nodes"] if node.get("backendDOMNodeId") == backend_id]}

    def _scroll_into_view(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        element = self._node(page, params.get("backendNodeId"))
        if element.box is None:
            raise FakeCdpError(-32000, "Node does not have a layout object")
        x, y, width, height = page.viewport
        top = element.box[1]
        if top < y or top + element.box[3] > y + height:
            page.viewport = (x, max(0, min(top - 10, page.content_size[1] - height)), width, height)
        return {}

    def _content_quads(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        element = self._node(page, params.get("backendNodeId"))
        if element.box is None:
            raise FakeCdpError(-32000, "Could not compute content quads.")
        bx, by, bw, bh = element.box
        left, top = bx - page.viewport[0], by - page.viewport[1]
        return {"quads": [[left, top, left + bw, top, left + bw, top + bh, left, top + bh]]}

    def _node_for_location(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        found = self.hit(page, params["x"], params["y"])
        return {"backendNodeId": found.backend_id if found is not None else 6, "frameId": target_id}

    async def _focus(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        element = self._node(page, params.get("backendNodeId"))
        self.focus[target_id] = self.focus_redirect.get(element.backend_id, element.backend_id)
        self._selected.discard(target_id)
        if self.on_focus is not None:
            outcome = self.on_focus(target_id, element.backend_id)
            if inspect.isawaitable(outcome):
                await outcome
        return {}

    def _call_function_on(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        text = params.get("functionDeclaration")
        name = next((key for key, script in INTERNAL_SCRIPTS.items() if script.text == text), None)
        this = self._object(params.get("objectId"))
        arguments = params.get("arguments") or []
        if name == "is_connected":
            value = page.find(this.backend_id) is this
        elif name == "contains":
            other = self._object(arguments[0].get("objectId"))
            value = other is this or _descends(this, other)
        elif name == "select_text":
            value = this.tag in ("INPUT", "TEXTAREA")
            if value:
                self._selected.add(target_id)
        elif name == "set_select_option":
            wanted = arguments[0].get("value")
            options = [child for child in this.children if child.tag == "OPTION"]
            index = next(
                (i for i, option in enumerate(options) if _text_of(option).strip() == wanted or option.attrs.get("value") == wanted),
                -1,
            )
            value = index >= 0
            if value:
                this.input_value = options[index].attrs.get("value", _text_of(options[index]))
                self.dom_events += [(target_id, this.backend_id, "input"), (target_id, this.backend_id, "change")]
        else:
            raise FakeCdpError(-32000, "a script the fake does not know")
        return {"result": {"type": "boolean", "value": value}}

    def _evaluate(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        if params.get("expression") != INTERNAL_SCRIPTS["active_element"].text:
            raise FakeCdpError(-32000, "a script the fake does not know")
        focused = self.focus.get(target_id)
        element = page.find(focused) if focused is not None else None
        if element is None:
            self.objects["node-body"] = _BODY
            return {"result": {"type": "object", "subtype": "node", "objectId": "node-body"}}
        return {"result": {"type": "object", "subtype": "node", "objectId": self._object_of(element)}}

    async def _mouse_event(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        self.mouse.append((target_id, dict(params)))
        if params.get("type") == "mouseWheel":
            x, y, width, height = page.viewport
            top = max(0, min(y + params.get("deltaY", 0), page.content_size[1] - height))
            page.viewport = (x, top, width, height)
            return {}
        if params.get("type") == "mouseReleased" and params.get("button") == "left":
            found = self.hit(page, params["x"], params["y"])
            hit_id = found.backend_id if found is not None else None
            self.clicks.append((target_id, hit_id))
            if self.on_click is not None:
                outcome = self.on_click(target_id, hit_id)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
                if outcome is not None:
                    return outcome
        return {}

    async def _key_event(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        self.keys.append((target_id, dict(params)))
        if params.get("type") == "keyDown" and self.on_key is not None:
            outcome = self.on_key(target_id, self.focus.get(target_id), params)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            if outcome is not None:
                return outcome
        return {}

    def _insert_text(self, target_id: str, page: Any, params: dict[str, Any]) -> Any:
        focused = self.focus.get(target_id)
        value = params.get("text", "")
        self.typed.append((target_id, focused, value))
        element = page.find(focused) if focused is not None else None
        if element is not None:
            if target_id in self._selected or not element.input_value:
                element.input_value = value
            else:
                element.input_value += value
        self._selected.discard(target_id)
        return {}

    def _download_behavior(self, params: dict[str, Any], session_id: str | None) -> Any:
        self.download_behaviors.append(dict(params))
        return {}

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
        page = self.site.get(params["url"])
        if page is not None:
            self.targets[target_id]["page"] = page()
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


def _descends(container: Any, inner: Any) -> bool:
    return any(child is inner or _descends(child, inner) for child in container.children)


def _text_of(element: Any) -> str:
    if element.tag == "#text":
        return element.text
    return "".join(_text_of(child) for child in element.children)


class _Body:
    tag = "BODY"
    backend_id = 6
    children: list[Any] = []


_BODY = _Body()


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
