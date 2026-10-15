"""What `wait` says about the page when its deadline passes, against a real browser."""

from __future__ import annotations

import asyncio
import json

import pytest
from fixtures.page_server import PageServer
from test_perception_integration import (  # noqa: F401 - fixtures
    attachment,
    launched,
    open_page,
    pages,
    percept,
    registry,
)

from mcp_server_diwall_browser.browser import TabRegistry
from mcp_server_diwall_browser.output import ToolError, finalize
from mcp_server_diwall_browser.perception import Perception

pytestmark = pytest.mark.browser

ABSENT = "Text that no page of this test shows"
WAIT_KEYS = {"tree_lines_start", "tree_lines_end", "changed", "busy"}


async def timed_out(percept: Perception, tab, timeout_s: float) -> ToolError:
    with pytest.raises(ToolError) as caught:
        await percept.wait_for(tab, kind="text", value=ABSENT, timeout_s=timeout_s)
    assert caught.value.code == "wait_timeout"
    return caught.value


def what_the_page_did(error: ToolError) -> dict:
    assert set(error.extra_server) == {"wait"}
    wait = error.extra_server["wait"]
    assert set(wait) == WAIT_KEYS
    return wait


async def test_a_page_that_adds_nodes_at_one_second_is_said_to_have_changed(
    registry: TabRegistry, pages: PageServer, percept: Perception
) -> None:
    tab = await open_page(registry, pages, "wait-growing.html")
    wait = what_the_page_did(await timed_out(percept, tab, 2.0))
    assert wait["changed"] is True
    assert wait["tree_lines_end"] > wait["tree_lines_start"]
    assert wait["busy"] == 0


async def test_a_page_that_adds_nodes_at_three_seconds_is_seen_by_a_wait_that_covers_it(
    registry: TabRegistry, pages: PageServer, percept: Perception
) -> None:
    tab = await open_page(registry, pages, "wait-growing.html")
    await asyncio.sleep(1.8)
    wait = what_the_page_did(await timed_out(percept, tab, 2.0))
    assert wait["changed"] is True
    assert wait["tree_lines_end"] > wait["tree_lines_start"]


async def test_a_frozen_page_is_said_not_to_have_changed(registry: TabRegistry, pages: PageServer, percept: Perception) -> None:
    tab = await open_page(registry, pages, "wait-frozen.html")
    wait = what_the_page_did(await timed_out(percept, tab, 1.0))
    assert wait["changed"] is False
    assert wait["tree_lines_end"] == wait["tree_lines_start"] > 0
    assert wait["busy"] == 0


async def test_a_node_marked_busy_is_counted_and_one_marked_not_busy_is_not(
    registry: TabRegistry, pages: PageServer, percept: Perception
) -> None:
    tab = await open_page(registry, pages, "wait-busy.html")
    wait = what_the_page_did(await timed_out(percept, tab, 1.0))
    assert wait["busy"] >= 1
    assert wait["changed"] is False


async def test_what_the_wait_says_holds_no_text_of_the_page(registry: TabRegistry, pages: PageServer, percept: Perception) -> None:
    for name, words in (("wait-growing.html", ("Report", "Row")), ("wait-busy.html", ("Report", "Results", "Computing", "Loading"))):
        tab = await open_page(registry, pages, name)
        error = await timed_out(percept, tab, 0.7)
        envelope = finalize({"ok": False, "server": {**error.extra_server, "error": {"code": error.code, "message": error.message}}, "page_content": None})
        serialized = json.dumps(envelope, ensure_ascii=False)
        assert all(word not in serialized for word in words)
        wait = envelope["server"]["wait"]
        assert all(isinstance(wait[key], int) and not isinstance(wait[key], bool) for key in ("tree_lines_start", "tree_lines_end", "busy"))
        assert isinstance(wait["changed"], bool)
