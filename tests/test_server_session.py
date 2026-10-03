from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from conftest import mounted_volume
from fixtures.action_support import FakeClock, make_profile
from fixtures.fake_cdp import FakeBrowser, FakeCdp

from mcp_server_diwall_browser.browser import default_http_get, read_proc_net_tcp
from mcp_server_diwall_browser.cdp import CdpConnection
from mcp_server_diwall_browser.evidence import DownloadManager
from mcp_server_diwall_browser.journal import Journal
from mcp_server_diwall_browser.operator_profile import BrowserSettings
from mcp_server_diwall_browser.perception import draw_mask
from mcp_server_diwall_browser.server import Dependencies, ServerState


@dataclass
class Wiring:
    fake: FakeCdp
    browser: FakeBrowser
    state: ServerState


@pytest.fixture
async def wiring(tmp_path: Path) -> AsyncIterator[Wiring]:
    fake = FakeCdp()
    await fake.start()
    browser = FakeBrowser(fake)
    paths, mount, _volume = mounted_volume(tmp_path)
    profile = replace(
        make_profile(tmp_path, allowed=("https://example.test",)),
        secrets_dir=paths.mount_point,
        browser=BrowserSettings(family="chromium", profile_dir=str(tmp_path / "dedicated-profile"), debug_port=fake.port),
    )
    clock = FakeClock()
    deps = Dependencies(
        load_profile=lambda: profile,
        mount_checker=mount.checker,
        cdp_factory=CdpConnection,
        http_get=default_http_get,
        proc_net=read_proc_net_tcp,
        which=lambda name: None,
        environ={},
        clock=clock,
        sleep=clock.sleep,
        mask_drawer=draw_mask,
        journal=Journal,
    )
    state = ServerState(deps)
    yield Wiring(fake=fake, browser=browser, state=state)
    await state.close()
    await fake.stop()


async def downloads_closed_from_the_attachment_on(wiring: Wiring) -> bool:
    """After the attachment and before any action, the last download setting is a refusal."""
    await wiring.state.browser()
    methods = wiring.fake.methods()
    if "Browser.getVersion" not in methods:
        return False
    return bool(wiring.browser.download_behaviors) and wiring.browser.download_behaviors[-1] == {"behavior": "deny"}


async def test_downloads_are_forbidden_from_the_attachment_on(wiring: Wiring) -> None:
    assert await downloads_closed_from_the_attachment_on(wiring)
    assert wiring.browser.download_behaviors == [{"behavior": "deny"}]
    gestures = [m for m in wiring.fake.methods() if m.startswith("Input.")]
    assert gestures == []


async def test_counter_proof_a_wiring_that_never_forbids_leaves_the_setting_open(
    wiring: Wiring, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def keep_open(self: DownloadManager) -> None:
        return None

    monkeypatch.setattr(DownloadManager, "forbid", keep_open)
    assert not await downloads_closed_from_the_attachment_on(wiring)


async def test_one_attachment_serves_every_call(wiring: Wiring) -> None:
    first = await wiring.state.browser()
    second = await wiring.state.browser()
    assert first is second
    assert wiring.fake.methods().count("Browser.getVersion") == 1


async def test_a_lost_connection_is_replaced_and_the_setting_closed_again(wiring: Wiring) -> None:
    first = await wiring.state.browser()
    await wiring.fake.drop()
    for _ in range(50):
        if not first.alive:
            break
        await asyncio.sleep(0.02)
    assert not first.alive
    second = await wiring.state.browser()
    assert second is not first and second.alive
    assert wiring.browser.download_behaviors == [{"behavior": "deny"}, {"behavior": "deny"}]


async def test_stopping_gives_downloads_back_and_leaves_the_tabs_open(wiring: Wiring) -> None:
    session = await wiring.state.browser()
    await session.registry.open_tab()
    await wiring.state.close()
    assert wiring.browser.download_behaviors[-1] == {"behavior": "default"}
    assert "Target.closeTarget" not in wiring.fake.methods()
    assert not session.alive and wiring.state.session is None
