"""What each tool returns, checked through the SDK's client, each check with a defective tool it must catch."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from test_server import URL, Client, Rig, mark_of, open_and_observe, rig  # noqa: F401 - rig is a fixture

from mcp_server_diwall_browser import __version__
from mcp_server_diwall_browser.output import ToolError, ToolOutput, tool
from mcp_server_diwall_browser.server import TOOLS, create_server, registered_tool
from mcp_server_diwall_browser.tools import close_tab, observe, press_key, select_option, wait

ToolRun = Callable[..., Awaitable[ToolOutput]]


def replaced(server: FastMCP, name: str, run: ToolRun, module: Any) -> FastMCP:
    """The same server with one tool swapped for a version written by the test, through the exit point."""
    state = server._tool_manager.get_tool("status").state  # type: ignore[union-attr]
    mutating = dict(TOOLS)[name]
    swapped = SimpleNamespace(run=tool(name)(run), Arguments=module.Arguments)
    server._tool_manager._tools[name] = registered_tool(state, name, mutating, swapped)
    return server


@asynccontextmanager
async def client_of(server: FastMCP) -> AsyncIterator[Client]:
    info = types.Implementation(name="test-client", version="1.0")
    async with create_connected_server_and_client_session(server, client_info=info) as session:
        yield Client(session)


Check = Callable[[Rig, FastMCP], Awaitable[None]]


def code_of(envelope: dict[str, Any]) -> str | None:
    """The refusal's code, or None for a result that is not a refusal."""
    return (envelope["server"].get("error") or {}).get("code")


async def holds(check: Check, rig: Rig, server: FastMCP) -> bool:
    try:
        await check(rig, server)
    except AssertionError:
        return False
    return True


# --- wait ---------------------------------------------------------------------------------------------


async def check_wait(rig: Rig, server: FastMCP) -> None:
    async with client_of(server) as client:
        tab, _observed = await open_and_observe(client, URL)
        text = await client.call("wait", tab_id=tab, value="Envoyer", timeout_s=2.0, **{"for": "text"})
        missing = await client.call("wait", tab_id=tab, value="Texte absent de la page", timeout_s=0.5, **{"for": "text"})
        element = await client.call("wait", tab_id=tab, value={"role": "button", "name": "Envoyer"}, timeout_s=2.0, **{"for": "element"})
        no_element = await client.call(
            "wait", tab_id=tab, value={"role": "button", "name": "Payer maintenant"}, timeout_s=0.5, **{"for": "element"}
        )
        load = await client.call("wait", tab_id=tab, timeout_s=2.0, **{"for": "load"})
    assert text["ok"] and text["server"]["met"] is True and text["server"]["tab_id"] == tab
    assert missing["ok"] is False and code_of(missing) == "wait_timeout"
    assert element["ok"] and element["server"]["met"] is True
    assert no_element["ok"] is False and code_of(no_element) == "wait_timeout"
    assert load["ok"] and load["server"]["met"] is True


async def wait_that_ignores_the_deadline(ctx: Any, *, tab_id: str, for_: str, value: Any, timeout_s: float) -> ToolOutput:
    # Defective: gives up without saying that the deadline passed.
    return ToolOutput(server={"tab_id": tab_id, "met": False, "dialog_open": False})


async def wait_that_never_looks(ctx: Any, *, tab_id: str, for_: str, value: Any, timeout_s: float) -> ToolOutput:
    # Defective: says what was waited for has come, without reading the page.
    return ToolOutput(server={"tab_id": tab_id, "met": True, "dialog_open": False})


async def test_wait_says_what_came_and_what_did_not_come_in_time(rig: Rig) -> None:
    assert await holds(check_wait, rig, create_server(rig.deps()))


@pytest.mark.parametrize("defective", [wait_that_ignores_the_deadline, wait_that_never_looks])
async def test_wait_check_catches_a_wait_that_does_not_read_or_ignores_the_deadline(rig: Rig, defective: ToolRun) -> None:
    assert not await holds(check_wait, rig, replaced(create_server(rig.deps()), "wait", defective, wait))


# --- select_option ---------------------------------------------------------------------------------------


async def check_select_option(rig: Rig, server: FastMCP) -> None:
    async with client_of(server) as client:
        tab, observed = await open_and_observe(client, URL)
        snapshot = observed["server"]["snapshot_id"]
        country = mark_of(observed, "Pays")
        chosen = await client.call("select_option", tab_id=tab, snapshot_id=snapshot, mark=country, option="Belgique", intent="choose")
        missing = await client.call("select_option", tab_id=tab, snapshot_id=snapshot, mark=country, option="Atlantide")
    assert chosen["ok"] and chosen["server"]["mark"] == country and chosen["server"]["action"] == "select_option"
    assert chosen["server"]["gate"] == {"red_list": False, "confirmation": "none"}
    assert [event for _, _, event in rig.browser.dom_events] == ["input", "change"]
    assert missing["ok"] is False and code_of(missing) == "option_not_found"


async def select_that_does_nothing(ctx: Any, *, tab_id: str, snapshot_id: str, mark: int, option: str, intent: str) -> ToolOutput:
    # Defective: reports a choice it never made, whatever the option.
    return ToolOutput(server={"tab_id": tab_id, "mark": mark, "action": "select_option", "gate": {"red_list": False, "confirmation": "none"}})


async def test_select_option_chooses_an_existing_option_and_refuses_a_missing_one(rig: Rig) -> None:
    assert await holds(check_select_option, rig, create_server(rig.deps()))


async def test_select_check_catches_a_tool_that_reports_a_choice_it_never_made(rig: Rig) -> None:
    assert not await holds(check_select_option, rig, replaced(create_server(rig.deps()), "select_option", select_that_does_nothing, select_option))


# --- press_key ------------------------------------------------------------------------------------------


async def check_press_key(rig: Rig, server: FastMCP) -> None:
    async with client_of(server) as client:
        tab, observed = await open_and_observe(client, URL)
        nickname = mark_of(observed, "Pseudonyme")
        moved = await client.call("press_key", tab_id=tab, key="Tab")
        entered = await client.call("press_key", tab_id=tab, key="Enter", snapshot_id=observed["server"]["snapshot_id"], mark=nickname)
    assert moved["ok"] and moved["server"]["action"] == "press_key" and "mark" not in moved["server"]
    assert entered["ok"] and entered["server"]["mark"] == nickname and entered["server"]["origin"] == "https://shop.example.test"
    downs = [params for _, params in rig.browser.keys if params["type"] == "keyDown"]
    assert [params["key"] for params in downs] == ["Tab", "Enter"]
    assert downs[1].get("text") == "\r"
    # Enter went to the marked field: it holds the focus.
    field = next(element for element in rig.page_of(URL)._elements() if element.name == "Pseudonyme")
    assert list(rig.browser.focus.values()) == [field.backend_id]


async def press_that_sends_nothing(
    ctx: Any, *, tab_id: str, key: str, snapshot_id: str | None, mark: int | None, intent: str
) -> ToolOutput:
    # Defective: answers like the action without sending any key.
    server: dict[str, Any] = {"tab_id": tab_id, "action": "press_key", "origin": "https://shop.example.test"}
    if mark is not None:
        server["mark"] = mark
    return ToolOutput(server=server)


async def test_press_key_sends_a_key_without_a_mark_and_enter_on_a_mark(rig: Rig) -> None:
    assert await holds(check_press_key, rig, create_server(rig.deps()))


async def test_press_key_check_catches_a_tool_that_sends_nothing(rig: Rig) -> None:
    assert not await holds(check_press_key, rig, replaced(create_server(rig.deps()), "press_key", press_that_sends_nothing, press_key))


# --- close_tab -----------------------------------------------------------------------------------------


async def check_close_tab(rig: Rig, server: FastMCP) -> None:
    async with client_of(server) as client:
        tab, _observed = await open_and_observe(client, URL)
        closed = await client.call("close_tab", tab_id=tab)
        again = await client.call("close_tab", tab_id=tab)
        observed = await client.call("observe", tab_id=tab)
        listed = await client.call("list_tabs")
    assert closed["ok"] and closed["server"]["tab_id"] == tab and closed["server"]["closed"] is True
    assert code_of(again) == "tab_not_owned" and code_of(observed) == "tab_not_owned"
    assert listed["server"]["tabs"] == []
    assert "Target.closeTarget" in rig.fake.methods()


async def close_that_closes_nothing(ctx: Any, *, tab_id: str) -> ToolOutput:
    # Defective: says the tab is closed and leaves it open.
    return ToolOutput(server={"tab_id": tab_id, "closed": True})


async def test_a_closed_tab_is_no_longer_the_servers(rig: Rig) -> None:
    assert await holds(check_close_tab, rig, create_server(rig.deps()))


async def test_close_check_catches_a_tool_that_closes_nothing(rig: Rig) -> None:
    assert not await holds(check_close_tab, rig, replaced(create_server(rig.deps()), "close_tab", close_that_closes_nothing, close_tab))


# --- observe ------------------------------------------------------------------------------------------

# What the perception states about an observation, and what the exit point adds to every result.
OBSERVE_SERVER_KEYS = {"tab_id", "snapshot_id", "origin", "loader_id", "viewport", "counters", "dialog_open"}
ENVELOPE_SERVER_KEYS = {"operation_id", "tool", "notices", "capture"}
VIEWPORT_KEYS = {"x", "y", "width", "height", "scale"}
COUNTER_KEYS = {"marks", "marks_truncated", "tree_lines", "tree_truncated", "omitted_not_visible", "redacted_fields", "frames_masked"}


async def check_observe(rig: Rig, server: FastMCP) -> None:
    async with client_of(server) as client:
        opened = await client.call("open_tab", url=URL)
        tab = opened["server"]["tab_id"]
        with_image = await client.call("observe", tab_id=tab)
        tree_only = await client.call("observe", tab_id=tab, include=["tree"])
    server_part = with_image["server"]
    assert set(server_part) == OBSERVE_SERVER_KEYS | ENVELOPE_SERVER_KEYS | {"image"}, sorted(server_part)
    assert set(tree_only["server"]) == OBSERVE_SERVER_KEYS | ENVELOPE_SERVER_KEYS, sorted(tree_only["server"])
    assert set(server_part["viewport"]) == VIEWPORT_KEYS
    assert server_part["viewport"] == {"x": 0, "y": 0, "width": 800, "height": 600, "scale": 1.0}
    assert set(server_part["counters"]) == COUNTER_KEYS
    assert server_part["counters"]["marks"] == len(with_image["page_content"]["elements"])
    assert server_part["counters"]["tree_lines"] == len(with_image["page_content"]["tree"])
    assert server_part["tab_id"] == tab and server_part["origin"] == "https://shop.example.test"
    assert server_part["loader_id"] and isinstance(server_part["loader_id"], str)
    assert server_part["dialog_open"] is False and server_part["capture"] == "ok"
    assert tree_only["server"]["capture"] == "not_requested"


async def observe_without_counters(ctx: Any, *, tab_id: str, include: list[str] | None) -> ToolOutput:
    # Defective: the real observation, with the counters and the document identity left out.
    output = await observe.run.__wrapped__(ctx, tab_id=tab_id, include=include)
    server = {key: value for key, value in output.server.items() if key not in ("counters", "loader_id")}
    return ToolOutput(server=server, page_content=output.page_content, image_png=output.image_png)


async def test_observe_states_exactly_what_the_perception_specifies(rig: Rig) -> None:
    assert await holds(check_observe, rig, create_server(rig.deps()))


async def test_observe_check_catches_a_result_without_counters(rig: Rig) -> None:
    assert not await holds(check_observe, rig, replaced(create_server(rig.deps()), "observe", observe_without_counters, observe))


async def test_a_refusal_from_the_defective_versions_still_leaves_through_the_exit_point(rig: Rig) -> None:
    async def refusing(ctx: Any, *, tab_id: str) -> ToolOutput:
        raise ToolError("tab_not_owned", "test")

    server = replaced(create_server(rig.deps()), "close_tab", refusing, close_tab)
    async with client_of(server) as client:
        refused = await client.call("close_tab", tab_id="t")
    assert code_of(refused) == "tab_not_owned"


# --- the version a client can read --------------------------------------------------------------------


async def test_the_server_version_comes_from_status_not_from_the_announcement(tmp_path: Path) -> None:
    # The operator guide sends the operator to status for the version; this keeps it true.
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "config")}
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server_diwall_browser"], env=env)
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_client(parameters, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                announced = await session.initialize()
                status = await session.call_tool("status", {})
    server = json.loads(status.content[0].text)["server"]
    assert announced.serverInfo.name == "mcp-server-diwall-browser"
    assert announced.serverInfo.version == version("mcp")
    assert server["server_version"] == __version__ != version("mcp")
