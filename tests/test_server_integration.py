"""The server started by its entry point, as a process, against a real Chromium-family browser."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import write_profile
from fixtures.browser_launcher import SKIP_REASON, LaunchedBrowser, launch_browser
from fixtures.page_server import PageServer
from fixtures.raw_cdp import RawSession
from mcp import ClientSession
from test_server_process import MODULE, child_running, listening_sockets_of, stdio_session

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("browser-home")


@pytest.fixture(scope="module")
def launched(browser_home: Path) -> Iterator[LaunchedBrowser]:
    browser = launch_browser(home=str(browser_home))
    if browser is None:
        pytest.skip(SKIP_REASON)
    yield browser
    browser.stop()


@pytest.fixture(scope="module")
def pages() -> Iterator[PageServer]:
    with PageServer() as server:
        yield server


def environment(tmp_path: Path, launched: LaunchedBrowser, pages: PageServer) -> dict[str, str]:
    volume = tmp_path / "vol"
    volume.mkdir(mode=0o700)
    profile = write_profile(
        tmp_path,
        secrets_dir=str(volume),
        allowed_origins=[pages.origin],
        browser={"family": "chromium", "profile_dir": launched.profile_dir, "debug_port": launched.port},
    )
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    return {"MCP_DIWALL_BROWSER_PROFILE": str(profile), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}


async def call(session: ClientSession, name: str, **arguments: Any) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    envelope = json.loads(result.content[0].text)
    assert result.isError is (envelope["ok"] is not True)
    return envelope


def mark_of(observed: dict[str, Any], name: str) -> int:
    return next(element["mark"] for element in observed["page_content"]["elements"] if element["name"] == name)


async def test_the_nominal_path_through_the_entry_point(tmp_path: Path, launched: LaunchedBrowser, pages: PageServer) -> None:
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, "-m", MODULE], environment(tmp_path, launched, pages), errlog) as (session, unreadable):
            status = await call(session, "status")
            opened = await call(session, "open_tab", url=pages.url("neutral.html"), intent="leave a comment")
            tab = opened["server"]["tab_id"]
            observed = await call(session, "observe", tab_id=tab)
            image = (await session.call_tool("observe", {"tab_id": tab, "include": ["image"]})).content
            snapshot = observed["server"]["snapshot_id"]
            filled = await call(session, "fill", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Pseudonyme"), text="alice")
            clicked = await call(session, "click", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Envoyer"))
            after = await call(session, "observe", tab_id=tab, include=["tree"])
            sockets = listening_sockets_of(child_running(MODULE))
    assert status["server"]["browser"]["attached"] is True
    assert opened["ok"] and opened["server"]["origin"] == pages.origin and opened["server"]["loaded"] is True
    assert observed["ok"] and observed["server"]["capture"] == "ok"
    assert [part.type for part in image] == ["text", "image"]
    assert filled["ok"] and clicked["ok"] and clicked["server"]["gate"]["red_list"] is False
    assert '  StaticText "Sent by alice"' in after["page_content"]["tree"]
    assert unreadable == [] and sockets == set()


async def test_drift_through_the_entry_point_never_clicks_another_element(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer
) -> None:
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, "-m", MODULE], environment(tmp_path, launched, pages), errlog) as (session, _unreadable):
            opened = await call(session, "open_tab", url=pages.url("drift.html"))
            tab = opened["server"]["tab_id"]
            observed = await call(session, "observe", tab_id=tab)
            snapshot = observed["server"]["snapshot_id"]
            # The first button inserts a new button before the second one.
            inserted = await call(session, "click", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Insert a button"))
            second = await call(session, "click", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Second button"))
            after = await call(session, "observe", tab_id=tab, include=["tree", "elements"])
    assert inserted["ok"] and second["ok"]
    assert "Inserted button" in [element["name"] for element in after["page_content"]["elements"]]
    assert '  StaticText "second clicked"' in after["page_content"]["tree"]


async def operator_tab(launched: LaunchedBrowser, url: str) -> str:
    """A tab the operator opens, outside the server, through a connection of its own."""
    from websockets.asyncio.client import connect

    async with connect(launched.version_info["webSocketDebuggerUrl"], proxy=None) as raw:
        await raw.send(json.dumps({"id": 1, "method": "Target.createTarget", "params": {"url": url}}))
        while True:
            answer = json.loads(await raw.recv())
            if answer.get("id") == 1:
                return answer["result"]["targetId"]


async def operator_downloads(launched: LaunchedBrowser, target_id: str, directory: Path) -> int:
    """Click the download link of the operator's tab; how many files arrive in the browser's own directory."""
    before = set(directory.iterdir()) if directory.exists() else set()
    async with RawSession(launched.version_info["webSocketDebuggerUrl"], target_id) as tab:
        await tab.evaluate("document.getElementById('invoice').click()", gesture=True)
    await asyncio.sleep(1.5)
    return len((set(directory.iterdir()) if directory.exists() else set()) - before)


async def test_while_the_server_is_attached_the_operators_own_downloads_are_refused_too(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer, browser_home: Path
) -> None:
    # Measured fact frozen here: the download setting is the browser's, not a
    # tab's. While the server is attached, it holds deny between its actions,
    # and a download the operator starts in a tab of their own is refused as
    # well; once the server's connection ends, the browser's setting is back.
    target = await operator_tab(launched, pages.url("download.html"))
    await asyncio.sleep(1.0)
    default_dir = browser_home / "Downloads"
    assert await operator_downloads(launched, target, default_dir) == 1
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, "-m", MODULE], environment(tmp_path, launched, pages), errlog) as (session, _unreadable):
            assert (await call(session, "status"))["server"]["browser"]["attached"] is True
            during = await operator_downloads(launched, target, default_dir)
    await asyncio.sleep(0.5)
    after = await operator_downloads(launched, target, default_dir)
    assert during == 0 and after == 1
