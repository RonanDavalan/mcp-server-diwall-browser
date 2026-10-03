"""Evidence of irreversible actions and downloaded files, inside the encrypted volume only.

Why this module exists:
    A capture of an authenticated page or a downloaded document written
    while the volume is closed lands in clear text on the disk. Evidence and
    downloads therefore go through the volume facade, which checks the mount
    on every write and has no fallback; a download is named by the browser
    with an identifier, then renamed from the page's suggestion reduced to
    safe characters, so no text of the page ever becomes a path.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cdp import CdpConnection, CdpError
from .mount import Volume, safe_name
from .output import ToolError

EVIDENCE_DIR = "evidence"
DOWNLOADS_DIR = "downloads"
BEFORE = "before"
AFTER = "after"
MANUAL_PREFIX = "manual-"

_log = logging.getLogger(__name__)


def _today() -> str:
    return datetime.date.today().isoformat()


class EvidenceWriter:
    """Writes captures under ``evidence/<YYYY-MM-DD>/<operation_id>/``; never purges any."""

    def __init__(self, volume: Volume, *, today: Callable[[], str] = _today) -> None:
        self._volume = volume
        self._today = today

    def write(self, operation_id: str, name: str, png: bytes) -> Path:
        parts = (EVIDENCE_DIR, self._today(), safe_name(operation_id))
        self._volume.ensure_dir(*parts)
        return self._volume.write_new(*parts, f"{safe_name(name)}.png", data=png)

    def before(self, operation_id: str, png: bytes) -> Path:
        return self.write(operation_id, BEFORE, png)

    def after(self, operation_id: str, png: bytes) -> Path:
        return self.write(operation_id, AFTER, png)

    def manual(self, operation_id: str, label: str, png: bytes) -> Path:
        return self.write(operation_id, MANUAL_PREFIX + safe_name(label), png)


class DownloadManager:
    """Sends downloads into the volume, or forbids them when it is closed."""

    def __init__(self, volume: Volume, connection: CdpConnection) -> None:
        self._volume = volume
        self._connection = connection
        self._directory: tuple[str, ...] | None = None
        self._pending: dict[str, tuple[tuple[str, ...], str]] = {}
        self._notices: list[str] = []
        self._begun = asyncio.Event()
        # Downloads the browser announced and has not yet reported a first progress for.
        self._unacknowledged: set[str] = set()
        self._acknowledged = asyncio.Event()
        self._acknowledged.set()
        connection.add_event_handler(self._on_event)

    async def prepare(self, operation_id: str) -> str:
        """Before a gesture that may start a download: where it would go, or ``deny``."""
        parts = (DOWNLOADS_DIR, safe_name(operation_id))
        self._begun.clear()
        self._unacknowledged.clear()
        self._acknowledged.set()
        try:
            directory = self._volume.ensure_dir(*parts)
        except ToolError:
            self._directory = None
            await self._connection.send("Browser.setDownloadBehavior", {"behavior": "deny"})
            return "deny"
        self._directory = parts
        await self._connection.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allowAndName", "downloadPath": str(directory), "eventsEnabled": True},
        )
        return "allowAndName"

    async def wait_begin(self, timeout_s: float) -> bool:
        """Wait for a download to begin after the last ``prepare``; a download starts a moment after the gesture.

        The browser announces a download before it has chosen the behaviour
        that applies to it. A setting changed in between refuses the download
        without any event (measured: about one download in ten when the
        setting was closed at the announcement). So once a download is
        announced this also waits, for at most ``timeout_s`` again, until the
        browser has reported a first progress for each one announced, and
        closes the setting after that: no download was lost that way in the
        runs measured, though a first progress is not proof of the choice.
        """
        try:
            await asyncio.wait_for(self._begun.wait(), timeout_s)
        except TimeoutError:
            return False
        try:
            await asyncio.wait_for(self._acknowledged.wait(), timeout_s)
        except TimeoutError:
            _log.warning("a download was announced and not taken up by the browser in time")
        return True

    async def forbid(self) -> None:
        """Close the window a gesture opened: no download starts between two actions.

        A page can start a download whenever it likes. The setting given
        before a gesture names a directory of the volume; left open, it would
        keep sending files there after the volume is closed, and the browser
        creates a missing directory itself, parents included, which is outside
        the volume and in clear. A download already begun is not cancelled
        (measured): it is tracked by its identifier and settled when it ends.
        If the call fails the connection is gone, and the setting ends with
        the connection.
        """
        self._directory = None
        try:
            await self._connection.send("Browser.setDownloadBehavior", {"behavior": "deny"})
        except (ToolError, CdpError) as exc:
            _log.warning("the download setting could not be closed (%s)", type(exc).__name__)

    async def restore_default(self) -> None:
        """Give the browser back its own download behaviour, when the server stops."""
        await self._connection.send("Browser.setDownloadBehavior", {"behavior": "default"})

    def drain_notices(self) -> list[str]:
        notices, self._notices = self._notices, []
        return notices

    def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if session_id is not None:
            return
        if method == "Browser.downloadWillBegin":
            guid = params.get("guid")
            if isinstance(guid, str) and self._directory is not None:
                suggested = params.get("suggestedFilename")
                self._pending[guid] = (self._directory, suggested if isinstance(suggested, str) else "")
                self._unacknowledged.add(guid)
                self._acknowledged.clear()
                self._begun.set()
        elif method == "Browser.downloadProgress":
            guid = params.get("guid")
            state = params.get("state")
            if not isinstance(guid, str):
                return
            if guid in self._unacknowledged:
                self._unacknowledged.discard(guid)
                if not self._unacknowledged:
                    self._acknowledged.set()
            if guid not in self._pending or state not in ("completed", "canceled"):
                return
            directory, suggested = self._pending.pop(guid)
            if state == "canceled":
                self._notices.append("un téléchargement a été annulé")
                return
            self._settle(directory, guid, suggested)

    def _settle(self, directory: tuple[str, ...], guid: str, suggested: str) -> None:
        wanted = safe_name(suggested)
        try:
            final = self._volume.settle_file(*directory, source=safe_name(guid), wanted=wanted)
        except (ToolError, OSError, ValueError) as exc:
            _log.warning("a downloaded file could not be settled (%s)", type(exc).__name__)
            self._notices.append("un fichier téléchargé n'a pas pu être rangé dans le répertoire chiffré")
            return
        # Only the reduced name is said; the file itself is page data, never opened.
        self._notices.append(f"fichier téléchargé dans le répertoire chiffré : {final.name}")
