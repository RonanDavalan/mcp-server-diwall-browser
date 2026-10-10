"""Preuves des actions irréversibles et fichiers téléchargés, à l'intérieur du volume chiffré seulement.

Pourquoi ce module existe :
    Une capture d'une page authentifiée ou un document téléchargé, écrits pendant que le volume est
    fermé, atterrissent en clair sur le disque. Les preuves et les téléchargements passent donc par la
    façade du volume, qui contrôle le montage à chaque écriture et n'a aucun repli ; un téléchargement
    est nommé par le navigateur avec un identifiant, puis renommé d'après la suggestion de la page
    réduite à des caractères sûrs, si bien qu'aucun texte de la page ne devient jamais un chemin.
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
    """Écrit les captures sous ``evidence/<AAAA-MM-JJ>/<operation_id>/`` ; n'en purge jamais."""

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
    """Envoie les téléchargements dans le volume, ou les interdit quand il est fermé."""

    def __init__(self, volume: Volume, connection: CdpConnection) -> None:
        self._volume = volume
        self._connection = connection
        self._directory: tuple[str, ...] | None = None
        self._pending: dict[str, tuple[tuple[str, ...], str]] = {}
        self._notices: list[str] = []
        self._begun = asyncio.Event()
        # Téléchargements que le navigateur a annoncés et pour lesquels il n'a pas encore rapporté de première progression.
        self._unacknowledged: set[str] = set()
        self._acknowledged = asyncio.Event()
        self._acknowledged.set()
        connection.add_event_handler(self._on_event)

    async def prepare(self, operation_id: str) -> str:
        """Avant un geste qui peut déclencher un téléchargement : où il irait, ou ``deny``."""
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
        """Attendre qu'un téléchargement commence après le dernier ``prepare`` ; un téléchargement démarre un instant après le geste.

        Le navigateur annonce un téléchargement avant d'avoir choisi le comportement qui s'y applique. Un
        réglage changé entre-temps refuse le téléchargement sans aucun événement (mesuré : environ un
        téléchargement sur dix quand le réglage était fermé à l'annonce). Donc, une fois un téléchargement
        annoncé, ceci attend aussi, au plus ``timeout_s`` de nouveau, que le navigateur ait rapporté une
        première progression pour chacun de ceux annoncés, et ferme le réglage ensuite : aucun téléchargement
        n'a été perdu ainsi dans les exécutions mesurées, bien qu'une première progression ne prouve pas le choix.
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
        """Fermer la fenêtre qu'un geste a ouverte : aucun téléchargement ne démarre entre deux actions.

        Une page peut lancer un téléchargement quand elle veut. Le réglage donné avant un geste nomme un
        répertoire du volume ; laissé ouvert, il continuerait d'y envoyer des fichiers après la fermeture du
        volume, et le navigateur crée lui-même un répertoire manquant, parents compris, ce qui est hors du
        volume et en clair. Un téléchargement déjà commencé n'est pas annulé (mesuré) : il est suivi par son
        identifiant et réglé quand il se termine. Si l'appel échoue, la connexion a disparu, et le réglage
        s'achève avec la connexion.
        """
        self._directory = None
        try:
            await self._connection.send("Browser.setDownloadBehavior", {"behavior": "deny"})
        except (ToolError, CdpError) as exc:
            _log.warning("the download setting could not be closed (%s)", type(exc).__name__)

    async def restore_default(self) -> None:
        """Rendre au navigateur son propre comportement de téléchargement, quand le serveur s'arrête."""
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
        # Seul le nom réduit est dit ; le fichier lui-même est une donnée de page, jamais ouvert.
        self._notices.append(f"fichier téléchargé dans le répertoire chiffré : {final.name}")
