"""Factory of the MCP server: what it is built from, and its connection to the browser.

Why this module exists:
    The libraries of the server each guard one thing; something has to put
    them together, once, the same way in production and in the tests. Every
    dependency the tests replace is a field of ``Dependencies``, given to the
    one factory: there is no switch, variable or file that turns a check off.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .browser import BrowserAttachment, HttpGet, ProcNetReader, TabRegistry, default_http_get, read_proc_net_tcp
from .cdp import CdpConnection, CdpError
from .evidence import DownloadManager, EvidenceWriter
from .journal import ClientInfo, Journal
from .mount import MountChecker, Volume
from .operator_profile import Profile, load_profile
from .output import ToolError
from .perception import MaskDrawer, Perception, draw_mask

_log = logging.getLogger(__name__)

JournalFactory = Callable[[Volume, Callable[[], ClientInfo | None]], Journal]


@dataclass
class Dependencies:
    """What the server is built from; the tests replace these, and only these."""

    load_profile: Callable[[], Profile]
    mount_checker: MountChecker
    cdp_factory: Callable[[], CdpConnection]
    http_get: HttpGet
    proc_net: ProcNetReader
    # The confirmation window: where its tools are found, and the environment
    # it is shown in (``None``: the server's own).
    which: Callable[[str], str | None]
    environ: Mapping[str, str] | None
    clock: Callable[[], float]
    sleep: Callable[[float], Awaitable[Any]]
    mask_drawer: MaskDrawer
    journal: JournalFactory


def production_dependencies() -> Dependencies:
    """The real ones: the operator's profile, the kernel's tables, the browser, the clock."""
    return Dependencies(
        load_profile=load_profile,
        mount_checker=MountChecker(),
        cdp_factory=CdpConnection,
        http_get=default_http_get,
        proc_net=read_proc_net_tcp,
        which=shutil.which,
        environ=None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        mask_drawer=draw_mask,
        journal=Journal,
    )


@dataclass
class BrowserSession:
    """One connection to the browser and what lives on it; a new connection gets new ones."""

    attachment: BrowserAttachment
    registry: TabRegistry
    perception: Perception
    downloads: DownloadManager
    evidence: EvidenceWriter
    volume: Volume

    @property
    def alive(self) -> bool:
        return not self.attachment.connection.closed


class ServerState:
    """What the server keeps between two calls: the browser session and the journals.

    The profile is read again on every call, as the operator may change it
    without restarting the server.
    """

    def __init__(self, deps: Dependencies) -> None:
        self._deps = deps
        self._session: BrowserSession | None = None
        self._volumes: dict[tuple[str, str | None], Volume] = {}
        self._attach_lock = asyncio.Lock()

    @property
    def deps(self) -> Dependencies:
        return self._deps

    @property
    def session(self) -> BrowserSession | None:
        return self._session

    def profile(self) -> Profile:
        return self._deps.load_profile()

    def volume(self, profile: Profile) -> Volume:
        key = (str(profile.secrets_dir), None if profile.secrets_crypt_dir is None else str(profile.secrets_crypt_dir))
        volume = self._volumes.get(key)
        if volume is None:
            volume = Volume(self._deps.mount_checker, *key)
            self._volumes[key] = volume
        return volume

    async def browser(self) -> BrowserSession:
        """The live session, attaching first if there is none or the last one was lost."""
        async with self._attach_lock:
            if self._session is not None and self._session.alive:
                return self._session
            if self._session is not None:
                await self._close_session()
            profile = self.profile()
            attachment = await BrowserAttachment.attach(
                profile,
                cdp_factory=self._deps.cdp_factory,
                http_get=self._deps.http_get,
                proc_net=self._deps.proc_net,
            )
            volume = self.volume(profile)
            downloads = DownloadManager(volume, attachment.connection)
            # Until a gesture opens it, the download setting is closed: a page
            # download between the attachment and the first action would go
            # wherever the browser's own settings say, outside the volume.
            await downloads.forbid()
            registry = TabRegistry(attachment.connection)
            perception = Perception(
                registry,
                allowed_origins=lambda: self.profile().allowed_origins,
                mask_drawer=self._deps.mask_drawer,
            )
            self._session = BrowserSession(
                attachment=attachment,
                registry=registry,
                perception=perception,
                downloads=downloads,
                evidence=EvidenceWriter(volume),
                volume=volume,
            )
            return self._session

    async def close(self) -> None:
        """When the server stops: downloads back to the browser's own setting, connection closed.

        The tabs the server opened stay open, and the browser keeps running.
        """
        async with self._attach_lock:
            await self._close_session()

    async def _close_session(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        if session.alive:
            try:
                await session.downloads.restore_default()
            except (ToolError, CdpError) as exc:
                _log.warning("the download setting could not be restored (%s)", type(exc).__name__)
        await session.attachment.close()
