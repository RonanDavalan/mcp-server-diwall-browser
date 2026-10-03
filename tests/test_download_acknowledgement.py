"""A download announced by the browser is not lost when the setting is closed right after.

Why this module exists:
    The browser announces a download before it has chosen the behaviour that applies to it; a
    setting changed between the announcement and that choice refuses the download without any
    event. A real browser does this about one time in ten, so the race is rebuilt here with a
    browser that takes a set time to choose: the manager must keep the setting open until the
    browser has taken the download up, and a manager that closes it at the announcement must lose it.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from conftest import mounted_volume
from fixtures.fake_cdp import FakeCdp, settle

from mcp_server_diwall_browser.cdp import CdpConnection
from mcp_server_diwall_browser.evidence import DownloadManager

TAKE_UP_S = 0.05
BODY = b"downloaded content"


class ChoosingBrowser:
    """A browser that announces a download, then applies the setting in force when it chooses."""

    def __init__(self, fake: FakeCdp) -> None:
        self._fake = fake
        self.setting: dict[str, Any] = {"behavior": "default"}
        fake.on("Browser.setDownloadBehavior", handler=self._set)

    def _set(self, params: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        self.setting = dict(params)
        return {}

    async def download(self, guid: str, suggested: str, *, after_s: float) -> bool:
        """Announce at once; choose ``after_s`` later. True if the download was taken up, False if refused."""
        await self._fake.emit(
            "Browser.downloadWillBegin", {"frameId": "F", "guid": guid, "url": "https://example.test/f", "suggestedFilename": suggested}
        )
        await asyncio.sleep(after_s)
        if self.setting.get("behavior") != "allowAndName":
            return False  # refused: the browser says nothing
        path = Path(self.setting["downloadPath"]) / guid
        path.write_bytes(BODY)
        os.chmod(path, 0o644)
        await self._fake.emit("Browser.downloadProgress", {"guid": guid, "totalBytes": len(BODY), "receivedBytes": 0, "state": "inProgress"})
        await self._fake.emit("Browser.downloadProgress", {"guid": guid, "totalBytes": len(BODY), "receivedBytes": len(BODY), "state": "completed"})
        return True


class AnnouncementOnly(DownloadManager):
    """The manager as it was: a download has begun as soon as it is announced."""

    async def wait_begin(self, timeout_s: float) -> bool:
        try:
            await asyncio.wait_for(self._begun.wait(), timeout_s)
        except TimeoutError:
            return False
        return True


@pytest.fixture
async def fake() -> AsyncIterator[FakeCdp]:
    server = FakeCdp()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def connection(fake: FakeCdp) -> AsyncIterator[CdpConnection]:
    client = CdpConnection()
    await client.connect(fake.ws_url)
    yield client
    await client.close()


def saved(directory: Path) -> list[str]:
    return sorted(os.listdir(directory)) if directory.exists() else []


async def gesture(manager: DownloadManager, browser: ChoosingBrowser, guid: str, *, after_s: float = TAKE_UP_S) -> bool:
    """What an action does around a click that starts a download: open the setting, wait, close it."""
    await manager.prepare("op1")
    choosing = asyncio.create_task(browser.download(guid, "report.txt", after_s=after_s))
    await asyncio.sleep(0)  # the announcement goes out
    await manager.wait_begin(1.0)
    await manager.forbid()
    taken_up = await choosing
    await settle()
    return taken_up


async def test_the_setting_stays_open_until_the_browser_has_taken_the_download_up(
    tmp_path: Path, fake: FakeCdp, connection: CdpConnection
) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    browser = ChoosingBrowser(fake)
    manager = DownloadManager(volume, connection)
    assert await gesture(manager, browser, "guid-1") is True
    assert saved(paths.project / "downloads" / "op1") == ["report.txt"]
    assert fake.received[-1]["params"] == {"behavior": "deny"}, "the setting is closed once the download is taken up"


async def test_counter_proof_a_manager_that_closes_the_setting_at_the_announcement_loses_the_download(
    tmp_path: Path, fake: FakeCdp, connection: CdpConnection
) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    browser = ChoosingBrowser(fake)
    manager = AnnouncementOnly(volume, connection)
    assert await gesture(manager, browser, "guid-1") is False
    assert saved(paths.project / "downloads" / "op1") == []


async def test_every_download_announced_is_waited_for(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    browser = ChoosingBrowser(fake)
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    first = asyncio.create_task(browser.download("guid-1", "a.txt", after_s=0.02))
    second = asyncio.create_task(browser.download("guid-2", "b.txt", after_s=0.12))
    await asyncio.sleep(0)
    await manager.wait_begin(1.0)
    await manager.forbid()
    assert await first is True and await second is True
    await settle()
    assert saved(paths.project / "downloads" / "op1") == ["a.txt", "b.txt"]


async def test_a_browser_that_never_takes_the_download_up_does_not_hold_the_setting_open(
    tmp_path: Path, fake: FakeCdp, connection: CdpConnection
) -> None:
    _paths, _mount, volume = mounted_volume(tmp_path)
    ChoosingBrowser(fake)
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    await fake.emit("Browser.downloadWillBegin", {"frameId": "F", "guid": "guid-1", "url": "https://example.test/f", "suggestedFilename": "a.txt"})
    started = time.monotonic()
    assert await manager.wait_begin(0.1) is True
    assert time.monotonic() - started < 0.5
    await manager.forbid()
    assert fake.received[-1]["params"] == {"behavior": "deny"}


async def test_a_download_left_unanswered_by_an_earlier_gesture_is_not_waited_for_again(
    tmp_path: Path, fake: FakeCdp, connection: CdpConnection
) -> None:
    _paths, _mount, volume = mounted_volume(tmp_path)
    browser = ChoosingBrowser(fake)
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    await fake.emit("Browser.downloadWillBegin", {"frameId": "F", "guid": "stale", "url": "https://example.test/f", "suggestedFilename": "a.txt"})
    await manager.forbid()
    started = time.monotonic()
    assert await gesture(manager, browser, "guid-2", after_s=0.02) is True
    assert time.monotonic() - started < 0.5, "the stale announcement was waited for"

