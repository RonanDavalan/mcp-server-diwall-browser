"""The tabs a stopped process left open, listed and closed by the next one, against a real browser."""

from __future__ import annotations

import http.client
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import mounted_volume
from fixtures.browser_launcher import SKIP_REASON, LaunchedBrowser, launch_browser
from fixtures.page_server import PageServer

from mcp_server_diwall_browser.browser import BrowserAttachment, TabRegistry
from mcp_server_diwall_browser.cdp import CdpConnection
from mcp_server_diwall_browser.mount import Volume
from mcp_server_diwall_browser.output import ToolError
from mcp_server_diwall_browser.tab_state import TabState

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def launched() -> Iterator[LaunchedBrowser]:
    browser = launch_browser()
    if browser is None:
        pytest.skip(SKIP_REASON)
    yield browser
    browser.stop()


@pytest.fixture(scope="module")
def pages() -> Iterator[PageServer]:
    with PageServer() as server:
        yield server


@pytest.fixture
def volume(tmp_path: Path) -> Volume:
    _paths, _mount, built = mounted_volume(tmp_path)
    return built


def page_targets(launched: LaunchedBrowser) -> list[str]:
    connection = http.client.HTTPConnection("127.0.0.1", launched.port, timeout=5)
    try:
        connection.request("GET", "/json/list")
        listing = json.loads(connection.getresponse().read())
    finally:
        connection.close()
    return [entry["id"] for entry in listing if entry.get("type") == "page"]


class Process:
    """A server process: its own connection to the browser and its own registry, on the shared volume."""

    def __init__(self, launched: LaunchedBrowser, volume: Volume) -> None:
        self._launched = launched
        self._volume = volume
        self._attachment: BrowserAttachment | None = None
        self.registry: TabRegistry

    async def __aenter__(self) -> Process:
        profile = SimpleNamespace(
            browser=SimpleNamespace(family="chromium", profile_dir=self._launched.profile_dir, debug_port=self._launched.port)
        )
        self._attachment = await BrowserAttachment.attach(profile, cdp_factory=CdpConnection)
        self.registry = TabRegistry(self._attachment.connection, state=TabState(self._volume))
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._attachment is not None
        await self._attachment.close()


async def test_a_second_process_lists_and_closes_the_tabs_the_first_left_in_a_real_browser(
    launched: LaunchedBrowser, pages: PageServer, volume: Volume
) -> None:
    before = set(page_targets(launched))
    async with Process(launched, volume) as first:
        opened = [await first.registry.open_tab(pages.url(name)) for name in ("wait-frozen.html", "wait-busy.html", "wait-growing.html")]
    ours = set(page_targets(launched)) - before
    assert len(ours) == 3

    async with Process(launched, volume) as second:
        previous = await second.registry.list_previous_tabs()
        assert len(previous) == 3
        assert {info.origin for info in previous} == {pages.origin}
        assert not {info.tab_id for info in previous} & {tab.tab_id for tab in opened}
        assert await second.registry.list_tabs() == []

        with pytest.raises(ToolError) as refused:
            second.registry.get(previous[0].tab_id)
        assert refused.value.code == "tab_not_owned"

        await second.registry.close_tab(previous[0].tab_id)
        assert len(set(page_targets(launched)) - before) == 2
        with pytest.raises(ToolError):
            await second.registry.close_tab(previous[0].tab_id)

    async with Process(launched, volume) as third:
        rest = await third.registry.list_previous_tabs()
        assert len(rest) == 2
        for info in rest:
            await third.registry.close_tab(info.tab_id)
    assert set(page_targets(launched)) == before


async def test_closing_previous_tabs_leaves_the_tabs_that_were_already_there_in_a_real_browser(
    launched: LaunchedBrowser, pages: PageServer, volume: Volume
) -> None:
    before = set(page_targets(launched))
    async with Process(launched, volume) as first:
        await first.registry.open_tab(pages.url("wait-frozen.html"))
    async with Process(launched, volume) as second:
        previous = await second.registry.list_previous_tabs()
        assert len(previous) == 1
        await second.registry.close_tab(previous[0].tab_id)
        assert await second.registry.list_previous_tabs() == []
    assert set(page_targets(launched)) == before
