from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import websockets.frames
from conftest import FAKE_PASSWORD
from fixtures.fake_cdp import NO_REPLY, FakeCdp, FakeCdpError, Reply, settle, until

from mcp_server_diwall_browser import cdp
from mcp_server_diwall_browser.cdp import (
    ALLOWED_METHODS,
    INTERNAL_SCRIPTS,
    CdpConnection,
    CdpError,
    DisallowedMethodError,
    InternalScript,
    UnknownScriptError,
)
from mcp_server_diwall_browser.output import ToolError

FOCUS = InternalScript("call_function_on", "function () { this.focus(); return true; }")
TITLE = InternalScript("evaluate", "document.title")
TEST_SCRIPTS = {"focus": FOCUS, "title": TITLE}

ConnectionFactory = Callable[[], CdpConnection]


@pytest.fixture
async def fake() -> AsyncIterator[FakeCdp]:
    server = FakeCdp()
    await server.start()
    yield server
    await server.stop()


async def connected(fake: FakeCdp, factory: ConnectionFactory = CdpConnection) -> CdpConnection:
    connection = factory()
    await connection.connect(fake.ws_url)
    return connection


@pytest.fixture
async def conn(fake: FakeCdp) -> AsyncIterator[CdpConnection]:
    connection = await connected(fake, lambda: CdpConnection(internal_scripts=TEST_SCRIPTS))
    yield connection
    await connection.close()


# --- requests, answers and events ---------------------------------------------------


async def test_answers_are_matched_to_requests_by_identifier(fake: FakeCdp, conn: CdpConnection) -> None:
    release_first = asyncio.Event()

    async def slow(params: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        await release_first.wait()
        return {"which": "first"}

    fake.on("Page.getFrameTree", handler=slow)
    fake.on("Page.getLayoutMetrics", {"which": "second"})
    first = asyncio.create_task(conn.send("Page.getFrameTree", session_id="S1"))
    second = await conn.send("Page.getLayoutMetrics", session_id="S1")
    release_first.set()
    assert second == {"which": "second"}
    assert await first == {"which": "first"}
    ids = [message["id"] for message in fake.received]
    assert ids == sorted(ids) and len(set(ids)) == 2 and all(isinstance(i, int) for i in ids)
    assert all(message["sessionId"] == "S1" for message in fake.received)


async def test_an_event_during_a_call_is_dispatched_and_the_answer_still_arrives(
    fake: FakeCdp, conn: CdpConnection
) -> None:
    seen: list[tuple[str, dict[str, Any], str | None]] = []

    async def emits_first(params: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        await fake.emit("Page.frameStartedLoading", {"frameId": "F"}, session_id="S1")
        await asyncio.sleep(0.05)
        return {"done": True}

    fake.on("Page.navigate", handler=emits_first)
    conn.add_event_handler(lambda method, params, session_id: seen.append((method, params, session_id)))
    result = await conn.send("Page.navigate", {"url": "https://example.test/"}, session_id="S1")
    assert result == {"done": True}
    assert seen == [("Page.frameStartedLoading", {"frameId": "F"}, "S1")]


async def test_a_slow_event_handler_never_holds_an_answer_back(fake: FakeCdp, conn: CdpConnection) -> None:
    never = asyncio.Event()

    async def blocking_handler(method: str, params: dict[str, Any], session_id: str | None) -> None:
        await never.wait()

    async def emits_first(params: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        await fake.emit("Page.loadEventFired", {})
        return {"ok": 1}

    conn.add_event_handler(blocking_handler)
    fake.on("Page.getLayoutMetrics", handler=emits_first)
    assert await asyncio.wait_for(conn.send("Page.getLayoutMetrics"), 2) == {"ok": 1}


async def test_an_event_handler_can_itself_send_a_call(fake: FakeCdp, conn: CdpConnection) -> None:
    results: list[dict[str, Any]] = []

    async def on_event(method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method == "Target.targetCreated":
            results.append(await conn.send("Target.attachToTarget", {"targetId": "T", "flatten": True}))

    fake.on("Target.attachToTarget", {"sessionId": "S9"})
    conn.add_event_handler(on_event)
    await fake.emit("Target.targetCreated", {"targetInfo": {"targetId": "T"}})
    await until(lambda: bool(results))
    assert results == [{"sessionId": "S9"}]


async def test_a_failing_event_handler_does_not_stop_the_reader(fake: FakeCdp, conn: CdpConnection) -> None:
    def broken(method: str, params: dict[str, Any], session_id: str | None) -> None:
        raise KeyError("boom")

    conn.add_event_handler(broken)
    fake.on("Page.getLayoutMetrics", handler=lambda p, s: Reply({"ok": 1}, [("Page.loadEventFired", {}, None)]))
    assert await conn.send("Page.getLayoutMetrics") == {"ok": 1}
    await settle()
    assert await conn.send("Page.getLayoutMetrics") == {"ok": 1}


async def test_protocol_error_raises_cdp_error(fake: FakeCdp, conn: CdpConnection) -> None:
    def refuse(params: dict[str, Any], session_id: str | None) -> Any:
        raise FakeCdpError(-32000, "Cannot navigate to invalid URL")

    fake.on("Page.navigate", handler=refuse)
    with pytest.raises(CdpError) as caught:
        await conn.send("Page.navigate", {"url": "https://example.test/"}, session_id="S1")
    assert caught.value.code == -32000
    assert caught.value.message == "Cannot navigate to invalid URL"


async def test_unknown_method_on_the_browser_side_is_a_cdp_error(conn: CdpConnection) -> None:
    with pytest.raises(CdpError) as caught:
        await conn.send("Page.getLayoutMetrics")
    assert caught.value.code == -32601


async def test_attach_asks_for_a_flat_session(fake: FakeCdp, conn: CdpConnection) -> None:
    fake.on("Target.attachToTarget", {"sessionId": "S42"})
    assert await conn.attach("T1") == "S42"
    assert fake.received[-1]["method"] == "Target.attachToTarget"
    assert fake.received[-1]["params"] == {"targetId": "T1", "flatten": True}


# --- the list of allowed methods ------------------------------------------------------

DISALLOWED = [
    "Target.getTargets",
    "Target.attachToBrowserTarget",
    "Browser.close",
    "Runtime.compileScript",
    "Runtime.runScript",
    "Page.addScriptToEvaluateOnNewDocument",
    "Network.getAllCookies",
    "Storage.getCookies",
    "DOM.setOuterHTML",
    "Debugger.enable",
    "page.navigate",
    "",
]


async def disallowed_methods_never_reach_the_browser(fake: FakeCdp, factory: ConnectionFactory) -> bool:
    """True when every disallowed method, in every form of call, is refused before a byte is sent."""
    connection = await connected(fake, factory)
    try:
        for method in DISALLOWED:
            for kwargs in ({}, {"session_id": "S1"}, {"session_id": "S1", "timeout": 1.0}):
                try:
                    await connection.send(method, {"x": 1}, **kwargs)
                except DisallowedMethodError:
                    pass
                except (CdpError, ToolError):
                    return False
                else:
                    return False
        await settle()
        return fake.received == []
    finally:
        await connection.close()


class PartialListConnection(CdpConnection):
    """Defective: applies the list to browser-level calls only."""

    async def send(self, method: str, params: Any = None, *, session_id: str | None = None, **kwargs: Any) -> Any:
        if session_id is not None:
            return await self._exchange(method, dict(params or {}), session_id, kwargs.get("timeout", 30.0))
        return await super().send(method, params, session_id=session_id, **kwargs)


async def test_a_method_outside_the_list_is_refused_before_any_byte_is_sent(fake: FakeCdp) -> None:
    assert await disallowed_methods_never_reach_the_browser(fake, CdpConnection)


async def test_list_check_catches_a_send_that_checks_only_some_calls(fake: FakeCdp) -> None:
    assert not await disallowed_methods_never_reach_the_browser(fake, PartialListConnection)
    assert "Target.getTargets" in fake.methods()


# The exact list: a method is added only with the code that sends it.
EXPECTED_METHODS = frozenset(
    {
        "Browser.getVersion",
        "Browser.setDownloadBehavior",
        "Target.setDiscoverTargets",
        "Target.createTarget",
        "Target.attachToTarget",
        "Target.closeTarget",
        "Page.enable",
        "Inspector.enable",
        "Page.navigate",
        "Page.getFrameTree",
        "Page.handleJavaScriptDialog",
        "Page.getLayoutMetrics",
        "Page.captureScreenshot",
        "Accessibility.getFullAXTree",
        "Accessibility.getPartialAXTree",
        "DOMSnapshot.captureSnapshot",
        "DOM.describeNode",
        "DOM.getNodeForLocation",
        "DOM.getContentQuads",
        "DOM.scrollIntoViewIfNeeded",
        "DOM.focus",
        "Input.dispatchMouseEvent",
        "Input.dispatchKeyEvent",
        "Input.insertText",
        "Runtime.evaluate",
        "Runtime.callFunctionOn",
    }
)


def list_is_exactly_the_expected_one(allowed: frozenset[str]) -> bool:
    return allowed == EXPECTED_METHODS


def test_the_list_of_allowed_methods_is_exactly_the_one_the_code_needs() -> None:
    assert list_is_exactly_the_expected_one(ALLOWED_METHODS)
    assert len(ALLOWED_METHODS) == 26


@pytest.mark.parametrize("extra", ["Target.activateTarget", "DOM.getDocument", "DOM.resolveNode", "DOM.getBoxModel", "Target.getTargets"])
def test_list_check_catches_one_more_method(extra: str) -> None:
    assert not list_is_exactly_the_expected_one(ALLOWED_METHODS | {extra})


def test_list_check_catches_one_method_less() -> None:
    assert not list_is_exactly_the_expected_one(ALLOWED_METHODS - {"Inspector.enable"})


def test_the_list_is_frozen_and_does_not_hold_the_dangerous_methods() -> None:
    assert isinstance(ALLOWED_METHODS, frozenset)
    for method in DISALLOWED:
        assert method not in ALLOWED_METHODS


# --- page scripts ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("Runtime.evaluate", {"expression": "document.title"}),
        ("Runtime.evaluate", {"expression": "1 + 1", "returnByValue": True}),
        ("Runtime.callFunctionOn", {"functionDeclaration": FOCUS.text, "objectId": "O1"}),
    ],
)
async def test_script_methods_cannot_be_sent_directly(
    fake: FakeCdp, conn: CdpConnection, method: str, params: dict[str, Any]
) -> None:
    with pytest.raises(DisallowedMethodError):
        await conn.send(method, params, session_id="S1")
    await settle()
    assert fake.received == []


async def test_call_internal_with_an_unknown_name_is_refused(fake: FakeCdp, conn: CdpConnection) -> None:
    with pytest.raises(UnknownScriptError):
        await conn.call_internal("document.cookie", session_id="S1")
    await settle()
    assert fake.received == []


@pytest.mark.parametrize(
    ("method", "params", "script"),
    [
        # The script object is in the table, the text sent is not its text.
        ("Runtime.evaluate", {"expression": "document.cookie"}, TITLE),
        ("Runtime.evaluate", {"expression": TITLE.text + " "}, TITLE),
        # A script built at run time with a table text is not the table's object.
        ("Runtime.evaluate", {"expression": "document.title"}, InternalScript("evaluate", "document.title")),
        ("Runtime.evaluate", {"expression": "location.href"}, InternalScript("evaluate", "location.href")),
        # Right text, wrong method or extra parameter.
        ("Runtime.evaluate", {"expression": FOCUS.text}, FOCUS),
        ("Runtime.evaluate", {"expression": TITLE.text, "contextId": 3}, TITLE),
        ("Page.navigate", {"url": "https://example.test/"}, TITLE),
    ],
)
async def test_a_text_that_is_not_exactly_a_table_text_is_refused(
    fake: FakeCdp, conn: CdpConnection, method: str, params: dict[str, Any], script: InternalScript
) -> None:
    with pytest.raises(DisallowedMethodError):
        await conn.send(method, params, session_id="S1", _script=script)
    await settle()
    assert fake.received == []


async def test_call_internal_sends_the_table_text_and_values_as_arguments(
    fake: FakeCdp, conn: CdpConnection
) -> None:
    fake.on("Runtime.callFunctionOn", {"result": {"type": "boolean", "value": True}})
    fake.on("Runtime.evaluate", {"result": {"type": "string", "value": "Title"}})
    hostile = "'); alert(1); ('"
    await conn.call_internal("focus", session_id="S1", object_id="O1", arguments=[{"value": hostile}, {"objectId": "O2"}])
    result = await conn.call_internal("title", session_id="S1")
    assert result == {"result": {"type": "string", "value": "Title"}}
    function_call, evaluation = fake.received
    assert function_call["params"] == {
        "functionDeclaration": FOCUS.text,
        "objectId": "O1",
        "arguments": [{"value": hostile}, {"objectId": "O2"}],
        "returnByValue": True,
    }
    assert function_call["sessionId"] == "S1"
    assert evaluation["params"] == {"expression": TITLE.text, "returnByValue": True}


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("focus", {"arguments": [{"value": 1, "objectId": "O2"}]}),
        ("focus", {"arguments": [{"unserializableValue": "NaN"}]}),
        ("focus", {"arguments": ["raw text"]}),
        ("focus", {"arguments": [{"objectId": 5}]}),
        ("focus", {}),
        ("title", {"object_id": "O1"}),
        ("title", {"arguments": [{"value": 1}]}),
    ],
)
async def test_call_internal_refuses_malformed_arguments(
    fake: FakeCdp, conn: CdpConnection, name: str, kwargs: dict[str, Any]
) -> None:
    if name == "focus":
        kwargs = {"object_id": "O1", **kwargs} if kwargs else {}
    with pytest.raises(DisallowedMethodError):
        await conn.call_internal(name, session_id="S1", **kwargs)
    await settle()
    assert fake.received == []


def test_production_script_table_is_empty_and_read_only() -> None:
    assert dict(INTERNAL_SCRIPTS) == {}
    with pytest.raises(TypeError):
        INTERNAL_SCRIPTS["x"] = TITLE  # type: ignore[index]
    connection = CdpConnection()
    with pytest.raises(UnknownScriptError):
        asyncio.run(connection.call_internal("title", session_id="S1"))


def test_the_script_table_takes_only_internal_scripts() -> None:
    with pytest.raises(TypeError):
        CdpConnection(internal_scripts={"x": "document.title"})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        InternalScript("eval", "1")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("Page.navigate", "javascript:alert(1)"),
        ("Page.navigate", "file:///etc/hostname"),
        ("Page.navigate", "data:text/html,x"),
        ("Target.createTarget", "chrome://settings"),
        ("Target.createTarget", None),
    ],
)
async def test_an_address_that_is_a_script_or_a_local_file_is_refused_by_send(
    fake: FakeCdp, conn: CdpConnection, method: str, url: str | None
) -> None:
    with pytest.raises(DisallowedMethodError):
        await conn.send(method, {} if url is None else {"url": url}, session_id="S1")
    await settle()
    assert fake.received == []


# --- connection failures -----------------------------------------------------------------


async def test_connection_closed_during_a_call_is_browser_unreachable(fake: FakeCdp, conn: CdpConnection) -> None:
    async def vanish(params: dict[str, Any], session_id: str | None) -> Any:
        await fake.drop()
        return NO_REPLY

    fake.on("Page.getLayoutMetrics", handler=vanish)
    with pytest.raises(ToolError) as caught:
        await conn.send("Page.getLayoutMetrics", session_id="S1")
    assert caught.value.code == "browser_unreachable"
    assert conn.closed
    with pytest.raises(ToolError) as again:
        await conn.send("Page.getLayoutMetrics", session_id="S1")
    assert again.value.code == "browser_unreachable"


async def test_no_answer_in_time_is_browser_unreachable(fake: FakeCdp, conn: CdpConnection) -> None:
    fake.on("Page.getLayoutMetrics", NO_REPLY)
    with pytest.raises(ToolError) as caught:
        await conn.send("Page.getLayoutMetrics", session_id="S1", timeout=0.2)
    assert caught.value.code == "browser_unreachable"
    fake.on("Page.getLayoutMetrics", {"ok": 1})
    assert await conn.send("Page.getLayoutMetrics", session_id="S1") == {"ok": 1}


async def test_nothing_listening_is_browser_unreachable(fake: FakeCdp) -> None:
    url = fake.ws_url
    await fake.stop()
    with pytest.raises(ToolError) as caught:
        await CdpConnection().connect(url)
    assert caught.value.code == "browser_unreachable"


async def test_the_connection_ignores_the_proxy_named_in_the_environment(
    fake: FakeCdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    fake.on("Browser.getVersion", {"product": "Chrome/154.0.0.0"})
    connection = await connected(fake)
    try:
        assert await connection.send("Browser.getVersion") == {"product": "Chrome/154.0.0.0"}
    finally:
        await connection.close()


# --- waiting for an event ---------------------------------------------------------------


async def test_wait_event_returns_the_first_matching_event(fake: FakeCdp, conn: CdpConnection) -> None:
    waiter = conn.wait_event(lambda m, p, s: m == "Page.loadEventFired" and s == "S1", timeout=2)
    await fake.emit("Page.loadEventFired", {"n": 0}, session_id="S2")
    await fake.emit("Page.loadEventFired", {"n": 1}, session_id="S1")
    assert await waiter == ("Page.loadEventFired", {"n": 1}, "S1")


async def test_wait_event_listens_from_its_creation(fake: FakeCdp, conn: CdpConnection) -> None:
    waiter = conn.wait_event(lambda m, p, s: m == "Page.loadEventFired", timeout=2)
    fake.on("Page.navigate", handler=lambda p, s: Reply({"loaderId": "L"}, [("Page.loadEventFired", {}, s)]))
    await conn.send("Page.navigate", {"url": "https://example.test/"}, session_id="S1")
    await settle()
    assert (await waiter)[0] == "Page.loadEventFired"


async def test_wait_event_times_out(conn: CdpConnection) -> None:
    with pytest.raises(TimeoutError):
        await conn.wait_event(lambda m, p, s: True, timeout=0.1)


async def test_wait_event_fails_when_the_connection_closes(fake: FakeCdp, conn: CdpConnection) -> None:
    waiter = conn.wait_event(lambda m, p, s: False, timeout=5)
    await fake.drop()
    with pytest.raises(ToolError) as caught:
        await waiter
    assert caught.value.code == "browser_unreachable"


# --- the operating log never carries parameters ------------------------------------------


def logged_text(records: list[logging.LogRecord]) -> str:
    parts = []
    for record in records:
        parts.append(record.getMessage())
        parts.append(repr(record.args))
        parts.append(record.exc_text or "")
    return "\n".join(parts)


async def typed_text_stays_out_of_the_log(
    fake: FakeCdp, factory: ConnectionFactory, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> bool:
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    # Frames are cut to 75 characters in the library's log by default; a
    # longer limit makes any frame logging show the whole parameter.
    monkeypatch.setattr(websockets.frames.Frame, "MAX_LOG_SIZE", 1_000_000)
    fake.on("Input.insertText", {})

    def refuse(params: dict[str, Any], session_id: str | None) -> Any:
        raise FakeCdpError(-32602, "Invalid parameters")

    fake.on("Input.dispatchKeyEvent", handler=refuse)
    connection = await connected(fake, factory)
    try:
        await connection.send("Input.insertText", {"text": FAKE_PASSWORD}, session_id="S1")
        with pytest.raises(CdpError):
            await connection.send("Input.dispatchKeyEvent", {"type": "char", "text": FAKE_PASSWORD}, session_id="S1")
        fake.on("Input.insertText", NO_REPLY)
        with pytest.raises(ToolError):
            await connection.send("Input.insertText", {"text": FAKE_PASSWORD}, session_id="S1", timeout=0.1)
    finally:
        await connection.close()
    assert any("Input.insertText" in record.getMessage() for record in caplog.records), "the call must be logged"
    return FAKE_PASSWORD not in logged_text(caplog.records)


class ParamsLoggingConnection(CdpConnection):
    """Defective: logs the parameters of every call."""

    async def send(self, method: str, params: Any = None, **kwargs: Any) -> Any:
        logging.getLogger(cdp.__name__).debug("cdp call %s %s", method, json.dumps(params))
        return await super().send(method, params, **kwargs)


class FrameLoggingConnection(CdpConnection):
    """Defective: lets the WebSocket library log its frames at DEBUG level."""

    async def connect(self, ws_url: str) -> None:
        cdp._frame_log.setLevel(logging.NOTSET)
        await super().connect(ws_url)

    async def close(self) -> None:
        await super().close()
        cdp._frame_log.setLevel(logging.WARNING)


async def test_typed_text_never_reaches_the_operating_log(
    fake: FakeCdp, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await typed_text_stays_out_of_the_log(fake, CdpConnection, caplog, monkeypatch)


async def test_log_check_catches_a_client_that_logs_parameters(
    fake: FakeCdp, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not await typed_text_stays_out_of_the_log(fake, ParamsLoggingConnection, caplog, monkeypatch)


async def test_log_check_catches_websocket_frame_logging(
    fake: FakeCdp, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not await typed_text_stays_out_of_the_log(fake, FrameLoggingConnection, caplog, monkeypatch)


async def test_cdp_error_text_does_not_repeat_the_browser_message() -> None:
    error = CdpError(-32000, "message from the browser")
    assert "message from the browser" not in str(error)
    assert "message from the browser" not in repr(error)


async def test_a_reply_after_close_is_ignored(fake: FakeCdp) -> None:
    connection = await connected(fake)
    await connection.close()
    assert connection.closed
    with pytest.raises(ToolError) as caught:
        await connection.send("Browser.getVersion")
    assert caught.value.code == "browser_unreachable"
    await settle()
    assert fake.received == []


async def test_messages_that_are_not_protocol_messages_are_ignored(fake: FakeCdp, conn: CdpConnection) -> None:
    for connection in fake.connections:
        await connection.send("not json")
        await connection.send("[1, 2]")
        await connection.send(json.dumps({"id": "x", "result": {}}))
        await connection.send(json.dumps({"method": 5}))
    fake.on("Browser.getVersion", {"product": "Chrome/1"})
    assert await conn.send("Browser.getVersion") == {"product": "Chrome/1"}
