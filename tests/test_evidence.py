from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import ROOT_LINE, mounted_volume
from fixtures.fake_cdp import FakeCdp, settle

from mcp_server_diwall_browser.cdp import CdpConnection
from mcp_server_diwall_browser.evidence import DownloadManager, EvidenceWriter
from mcp_server_diwall_browser.mount import Volume, safe_name
from mcp_server_diwall_browser.output import ToolError

PNG = b"\x89PNG\r\n\x1a\nfake image"
DAY = "2000-01-02"


def files_under(root: Path) -> set[str]:
    return {os.path.relpath(os.path.join(base, name), root) for base, _dirs, names in os.walk(root) for name in names}


def close_volume(fake: Any) -> None:
    fake.rewrite(ROOT_LINE + "\n")


# --- evidence ------------------------------------------------------------------------------------


def test_evidence_is_private_and_under_the_volume(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    writer = EvidenceWriter(volume, today=lambda: DAY)
    before = writer.before("op1", PNG)
    after = writer.after("op1", PNG)
    manual = writer.manual("op1", "../../x", PNG)
    directory = paths.project / "evidence" / DAY / "op1"
    assert {before, after, manual} == {directory / "before.png", directory / "after.png", directory / "manual-_.._x.png"}
    for path in (before, after, manual):
        assert path.stat().st_mode & 0o777 == 0o600 and path.read_bytes() == PNG
    for level in (paths.project / "evidence", directory.parent, directory):
        assert level.stat().st_mode & 0o777 == 0o700


def test_evidence_is_never_overwritten(tmp_path: Path) -> None:
    _paths, _fake, volume = mounted_volume(tmp_path)
    writer = EvidenceWriter(volume, today=lambda: DAY)
    writer.before("op1", PNG)
    with pytest.raises(FileExistsError):
        writer.before("op1", b"other")


Writer = Callable[[Volume, Path], Any]


def nothing_written_anywhere_when_closed(tmp_path: Path, make_writer: Writer) -> bool:
    paths, fake, volume = mounted_volume(tmp_path)
    close_volume(fake)
    before = files_under(tmp_path)
    try:
        make_writer(volume, tmp_path).before("op1", PNG)
    except ToolError:
        pass
    return files_under(tmp_path) == before


class FallbackWriter(EvidenceWriter):
    """Defective: when the volume is closed, keeps the capture in a temporary directory."""

    def __init__(self, volume: Volume, fallback: Path) -> None:
        super().__init__(volume, today=lambda: DAY)
        self._fallback = fallback

    def write(self, operation_id: str, name: str, png: bytes) -> Path:
        try:
            return super().write(operation_id, name, png)
        except ToolError:
            target = self._fallback / f"{name}.png"
            target.write_bytes(png)
            return target


def test_a_closed_volume_writes_nothing_anywhere(tmp_path: Path) -> None:
    assert nothing_written_anywhere_when_closed(tmp_path, lambda volume, _tmp: EvidenceWriter(volume, today=lambda: DAY))


def test_closed_volume_check_catches_a_writer_that_falls_back_to_a_temporary_directory(tmp_path: Path) -> None:
    assert not nothing_written_anywhere_when_closed(tmp_path, lambda volume, tmp: FallbackWriter(volume, tmp))


def test_a_closed_volume_is_refused_with_its_code(tmp_path: Path) -> None:
    paths, fake, volume = mounted_volume(tmp_path)
    close_volume(fake)
    with pytest.raises(ToolError) as caught:
        EvidenceWriter(volume, today=lambda: DAY).before("op1", PNG)
    assert caught.value.code in ("encrypted_dir_closed", "encrypted_dir_undetermined")


def test_a_link_in_the_evidence_directory_is_refused(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (paths.project / "evidence").symlink_to(outside)
    with pytest.raises(ToolError) as caught:
        EvidenceWriter(volume, today=lambda: DAY).before("op1", PNG)
    assert caught.value.code == "volume_path_refused"
    assert files_under(outside) == set()


# --- downloads ------------------------------------------------------------------------------------


@pytest.fixture
async def fake() -> AsyncIterator[FakeCdp]:
    server = FakeCdp()
    await server.start()
    server.on("Browser.setDownloadBehavior", {})
    yield server
    await server.stop()


@pytest.fixture
async def connection(fake: FakeCdp) -> AsyncIterator[CdpConnection]:
    client = CdpConnection()
    await client.connect(fake.ws_url)
    yield client
    await client.close()


async def test_a_closed_volume_denies_downloads_and_creates_nothing(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    paths, mount, volume = mounted_volume(tmp_path)
    close_volume(mount)
    before = files_under(tmp_path)
    manager = DownloadManager(volume, connection)
    assert await manager.prepare("op1") == "deny"
    assert fake.received[-1]["params"] == {"behavior": "deny"}
    assert not (paths.project / "downloads").exists()
    assert files_under(tmp_path) == before


async def test_an_open_volume_names_downloads_by_identifier_under_it(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    manager = DownloadManager(volume, connection)
    assert await manager.prepare("op1") == "allowAndName"
    params = fake.received[-1]["params"]
    directory = paths.project / "downloads" / "op1"
    assert params == {"behavior": "allowAndName", "downloadPath": str(directory), "eventsEnabled": True}
    assert os.path.isabs(params["downloadPath"])
    assert directory.stat().st_mode & 0o777 == 0o700
    await manager.restore_default()
    assert fake.received[-1]["params"] == {"behavior": "default"}


async def download(fake: FakeCdp, directory: Path, guid: str, suggested: str, state: str = "completed") -> None:
    await fake.emit("Browser.downloadWillBegin", {"frameId": "F", "guid": guid, "url": "https://example.test/f", "suggestedFilename": suggested})
    path = directory / guid
    path.write_bytes(b"downloaded content")
    os.chmod(path, 0o644)
    await fake.emit("Browser.downloadProgress", {"guid": guid, "totalBytes": 18, "receivedBytes": 18, "state": state})
    await settle()


SUGGESTED = [
    ("report.pdf", "report.pdf"),
    ("../../x.txt", "_.._x.txt"),
    ("con.txt", "con.txt"),
    ("a" * 300 + ".pdf", "a" * 64),
    ("rapport final (2).pdf", "rapport_final__2_.pdf"),
    ("", "unnamed"),
]


@pytest.mark.parametrize(("suggested", "expected"), SUGGESTED)
async def test_a_finished_download_gets_a_safe_private_name(
    tmp_path: Path, fake: FakeCdp, connection: CdpConnection, suggested: str, expected: str
) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    directory = paths.project / "downloads" / "op1"
    await download(fake, directory, "guid-0001", suggested)
    assert sorted(os.listdir(directory)) == [expected]
    assert (directory / expected).stat().st_mode & 0o777 == 0o600
    assert manager.drain_notices() == [f"fichier téléchargé dans le répertoire chiffré : {expected}"]


async def test_a_name_already_taken_gets_a_suffix(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    directory = paths.project / "downloads" / "op1"
    for number in range(3):
        await download(fake, directory, f"guid-{number}", "report.pdf")
    assert sorted(os.listdir(directory)) == ["report-1.pdf", "report-2.pdf", "report.pdf"]


async def test_a_cancelled_download_is_said_and_nothing_renamed(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    directory = paths.project / "downloads" / "op1"
    await download(fake, directory, "guid-9", "x.pdf", state="canceled")
    assert os.listdir(directory) == ["guid-9"]
    assert manager.drain_notices() == ["un téléchargement a été annulé"]


async def test_a_link_in_the_downloads_directory_is_refused(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (paths.project / "downloads").symlink_to(outside)
    manager = DownloadManager(volume, connection)
    assert await manager.prepare("op1") == "deny"
    assert files_under(outside) == set()


async def a_downloaded_link_is_not_followed(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> bool:
    paths, _mount, volume = mounted_volume(tmp_path)
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    directory = paths.project / "downloads" / "op1"
    target = tmp_path / "victim"
    target.write_bytes(b"x")
    os.chmod(target, 0o644)
    await fake.emit("Browser.downloadWillBegin", {"guid": "guid-l", "suggestedFilename": "a.txt"})
    (directory / "guid-l").symlink_to(target)
    await fake.emit("Browser.downloadProgress", {"guid": "guid-l", "state": "completed"})
    await settle()
    return target.stat().st_mode & 0o777 == 0o644


async def test_a_download_that_is_a_link_is_left_alone(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    assert await a_downloaded_link_is_not_followed(tmp_path, fake, connection)


class RawNameVolume(Volume):
    """Defective: renames the file to the page's suggestion as it is."""

    def settle_file(self, *parts: str, source: str, wanted: str) -> Path:
        root = self.require_mounted()
        directory = root.joinpath(*parts)
        target = directory / self.raw_wanted
        os.rename(directory / source, target)
        return target


async def downloads_stay_in_their_directory(tmp_path: Path, fake: FakeCdp, connection: CdpConnection, raw: bool) -> bool:
    paths, mount, volume = mounted_volume(tmp_path)
    if raw:
        volume = RawNameVolume(mount.checker, paths.mount_point)
        volume.raw_wanted = "../../x.txt"  # type: ignore[attr-defined]
    manager = DownloadManager(volume, connection)
    await manager.prepare("op1")
    directory = paths.project / "downloads" / "op1"
    await download(fake, directory, "guid-r", "../../x.txt")
    outside = {name for name in files_under(tmp_path) if not name.startswith(os.path.relpath(directory, tmp_path))}
    return not any(name.endswith("x.txt") for name in outside)


async def test_downloads_never_leave_their_directory(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    assert await downloads_stay_in_their_directory(tmp_path, fake, connection, raw=False)


async def test_directory_check_catches_a_version_that_keeps_the_suggested_name(tmp_path: Path, fake: FakeCdp, connection: CdpConnection) -> None:
    assert not await downloads_stay_in_their_directory(tmp_path, fake, connection, raw=True)


def test_the_settled_name_must_already_be_safe(tmp_path: Path) -> None:
    paths, _mount, volume = mounted_volume(tmp_path)
    with pytest.raises(ValueError):
        volume.settle_file("downloads", source="g", wanted="../x")
    assert safe_name("../../x.txt") == "_.._x.txt"


def test_events_of_a_page_session_are_not_downloads(tmp_path: Path) -> None:
    _paths, _mount, volume = mounted_volume(tmp_path)

    class Silent:
        def add_event_handler(self, handler: Any) -> None:
            self.handler = handler

    silent = Silent()
    manager = DownloadManager(volume, silent)  # type: ignore[arg-type]
    silent.handler("Browser.downloadProgress", {"guid": "x", "state": "completed"}, "SESSION")
    assert manager.drain_notices() == []
