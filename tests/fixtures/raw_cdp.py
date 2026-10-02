"""A raw DevTools session for the browser tests, outside the production client.

Why this module exists:
    The browser tests must act on a page the way a page or the browser
    would (a script that fills a field, an autofill, a resize, a click that
    opens a dialog), with methods the production list rightly refuses. They
    use this separate connection, never cdp.py, so the list stays what the
    server needs and nothing more.
"""

from __future__ import annotations

import itertools
import json
from types import TracebackType
from typing import Any

from websockets.asyncio.client import ClientConnection, connect


class RawError(Exception):
    pass


class RawSession:
    def __init__(self, ws_url: str, target_id: str) -> None:
        self._ws_url = ws_url
        self._target_id = target_id
        self._ws: ClientConnection | None = None
        self._ids = itertools.count(1)
        self.session_id = ""

    async def __aenter__(self) -> RawSession:
        self._ws = await connect(self._ws_url, proxy=None, max_size=2**26)
        attached = await self.browser_call("Target.attachToTarget", {"targetId": self._target_id, "flatten": True})
        self.session_id = attached["sessionId"]
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def browser_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._call(method, params, None)

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._call(method, params, self.session_id)

    async def evaluate(self, expression: str, *, gesture: bool = False) -> Any:
        result = await self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "userGesture": gesture})
        if "exceptionDetails" in result:
            raise RawError("script failed")
        return result["result"].get("value")

    async def backend_id(self, selector: str) -> int:
        document = await self.call("DOM.getDocument", {"depth": 0})
        found = await self.call("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector})
        described = await self.call("DOM.describeNode", {"nodeId": found["nodeId"]})
        return described["node"]["backendNodeId"]

    async def _call(self, method: str, params: dict[str, Any] | None, session_id: str | None) -> dict[str, Any]:
        assert self._ws is not None
        message_id = next(self._ids)
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        await self._ws.send(json.dumps(message))
        while True:
            answer = json.loads(await self._ws.recv())
            if answer.get("id") == message_id:
                if "error" in answer:
                    raise RawError(answer["error"].get("message", ""))
                return answer.get("result", {})
