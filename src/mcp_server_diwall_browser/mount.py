"""Savoir si le répertoire chiffré est monté, et y écrire en sécurité.

Pourquoi ce module existe :
    Un secret ou une capture de page écrits pendant que le volume chiffré est fermé atterrissent en
    clair sur le disque sous-jacent. Savoir si le volume est vraiment monté doit se lire dans le noyau
    à chaque accès, jamais se supposer ni se mémoriser, et toute écriture dedans doit passer par ce contrôle.
"""

from __future__ import annotations

import enum
import errno
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .output import ToolError

PROJECT_DIR_NAME = "mcp-server-diwall-browser"
GOCRYPTFS_TYPE = "fuse.gocryptfs"
_CONF_NAME = "gocryptfs.conf"

_OCTAL_ESCAPE_RE = re.compile(r"\\([0-7]{3})")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")

SETUP_COMMAND = "mcp-server-diwall-browser-setup"
OPERATOR_OPENS_DIRECTORY = "operator_opens_directory"


class MountInfoError(ValueError):
    """Une ligne de la table des montages n'a pas pu être comprise."""


@dataclass(frozen=True)
class MountEntry:
    mount_point: str
    fs_type: str
    super_options: tuple[str, ...]
    source: str = ""


class MountState(enum.Enum):
    UNREADABLE = "unreadable"
    MOUNTED = "mounted"
    NOT_GOCRYPTFS = "not_gocryptfs"
    CLOSED = "closed"
    NEVER_INITIALIZED = "never_initialized"
    CLOSED_OR_ABSENT = "closed_or_absent"


_STATE_CODES: dict[MountState, str | None] = {
    MountState.UNREADABLE: "mount_unreadable",
    MountState.MOUNTED: None,
    MountState.NOT_GOCRYPTFS: "mount_not_gocryptfs",
    MountState.CLOSED: "encrypted_dir_closed",
    MountState.NEVER_INITIALIZED: "encrypted_dir_undetermined",
    MountState.CLOSED_OR_ABSENT: "encrypted_dir_undetermined",
}


@dataclass(frozen=True)
class MountVerdict:
    state: MountState
    error_code: str | None
    reason: str


def _verdict(state: MountState, reason: str) -> MountVerdict:
    return MountVerdict(state=state, error_code=_STATE_CODES[state], reason=reason)


def _unescape(field: str) -> str:
    return _OCTAL_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> list[MountEntry]:
    """Analyser le contenu de ``/proc/self/mountinfo``.

    Une ligne mal formée lève au lieu d'être sautée : la sauter pourrait cacher le montage même que
    l'on cherche.
    """
    entries = []
    for number, line in enumerate(text.splitlines(), start=1):
        fields = line.split(" ")
        try:
            separator = fields.index("-", 6)
        except ValueError:
            raise MountInfoError(f"line {number}: no separator") from None
        tail = fields[separator + 1 :]
        if len(tail) != 3 or not all(fields[:5]) or not tail[0]:
            raise MountInfoError(f"line {number}: wrong number of fields")
        entries.append(
            MountEntry(
                mount_point=_unescape(fields[4]),
                fs_type=tail[0],
                super_options=tuple(tail[2].split(",")),
                source=_unescape(tail[1]),
            )
        )
    return entries


class MountChecker:
    """Lire la table des montages et décider, à chaque appel, de l'état du volume."""

    def __init__(
        self,
        *,
        mountinfo_path: str = "/proc/self/mountinfo",
        euid: int | None = None,
        stat_fn: Callable[[str], os.stat_result] = os.stat,
        realpath_fn: Callable[[str], str] = os.path.realpath,
        isfile_fn: Callable[[str], bool] = os.path.isfile,
    ) -> None:
        self._mountinfo_path = mountinfo_path
        self._euid = euid
        self.stat_fn = stat_fn
        self.realpath_fn = realpath_fn
        self._isfile_fn = isfile_fn

    @property
    def euid(self) -> int:
        return os.geteuid() if self._euid is None else self._euid

    def check(self, secrets_dir: str | os.PathLike[str], secrets_crypt_dir: str | os.PathLike[str] | None = None) -> MountVerdict:
        try:
            with open(self._mountinfo_path, "rb") as handle:
                raw = handle.read()
            entries = parse_mountinfo(raw.decode("utf-8", "surrogateescape"))
        except (OSError, MountInfoError):
            return _verdict(MountState.UNREADABLE, "mountinfo_unreadable")
        if not entries:
            # Le système de fichiers racine est toujours listé ; une table vide n'est pas une lecture digne de confiance.
            return _verdict(MountState.UNREADABLE, "mountinfo_empty")

        try:
            target = self.realpath_fn(os.fspath(secrets_dir))
        except OSError:
            return _verdict(MountState.UNREADABLE, "realpath_failed")
        matches = [entry for entry in entries if entry.mount_point == target]
        if matches:
            return self._check_entry(matches[-1], target)

        if secrets_crypt_dir is None:
            return _verdict(MountState.CLOSED_OR_ABSENT, "no_mount_entry")
        if self._isfile_fn(os.path.join(os.fspath(secrets_crypt_dir), _CONF_NAME)):
            return _verdict(MountState.CLOSED, "no_mount_entry")
        return _verdict(MountState.NEVER_INITIALIZED, "no_volume_config")

    def _check_entry(self, entry: MountEntry, target: str) -> MountVerdict:
        if entry.fs_type != GOCRYPTFS_TYPE:
            return _verdict(MountState.NOT_GOCRYPTFS, "fs_type")
        if f"user_id={self.euid}" not in entry.super_options:
            return _verdict(MountState.NOT_GOCRYPTFS, "user_id")
        if "allow_other" in entry.super_options:
            return _verdict(MountState.NOT_GOCRYPTFS, "allow_other")
        try:
            mount_dev = self.stat_fn(target).st_dev
            parent_dev = self.stat_fn(os.path.dirname(target)).st_dev
        except OSError:
            return _verdict(MountState.UNREADABLE, "stat_failed")
        project = os.path.join(target, PROJECT_DIR_NAME)
        try:
            project_stat = self.stat_fn(project)
        except FileNotFoundError:
            project_stat = None
        except OSError:
            return _verdict(MountState.UNREADABLE, "stat_failed")
        if mount_dev == parent_dev:
            return _verdict(MountState.NOT_GOCRYPTFS, "device_mismatch")
        if project_stat is None or not stat.S_ISDIR(project_stat.st_mode):
            return MountVerdict(MountState.MOUNTED, "project_root_missing", "project_root_missing")
        if project_stat.st_dev != mount_dev:
            return _verdict(MountState.NOT_GOCRYPTFS, "device_mismatch")
        return _verdict(MountState.MOUNTED, "ok")


_STATE_MESSAGES: dict[MountState, str] = {
    MountState.UNREADABLE: (
        "l'état de montage du répertoire chiffré ne peut pas être lu : refus par précaution ; "
        f"`{SETUP_COMMAND} check` dit ce que le poste permet"
    ),
    MountState.NOT_GOCRYPTFS: (
        "le répertoire déclaré n'est pas un volume gocryptfs monté pour l'utilisateur du serveur seul : "
        f"refus par précaution ; `{SETUP_COMMAND} check` dit ce qui est en place et la voie gocryptfs"
    ),
    MountState.CLOSED: (
        f"le répertoire chiffré est fermé : l'opérateur l'ouvre avec `{SETUP_COMMAND} open`, puis relancer"
    ),
    MountState.CLOSED_OR_ABSENT: (
        "le répertoire chiffré n'est pas confirmé : fermé ou jamais créé ; l'opérateur lance "
        f"`{SETUP_COMMAND} check`, qui dit lequel des deux, puis relancer"
    ),
    MountState.NEVER_INITIALIZED: (
        "aucun volume chiffré à l'emplacement déclaré dans le profil : l'opérateur lance "
        f"`{SETUP_COMMAND} check`, puis `{SETUP_COMMAND} create`"
    ),
}
_PROJECT_ROOT_MISSING_MESSAGE = (
    "le répertoire chiffré est ouvert mais le dossier du serveur n'y est pas : l'opérateur lance "
    f"`{SETUP_COMMAND} check`, qui donne la commande à taper, puis relancer"
)
_OUTSIDE_MESSAGE = "un chemin du répertoire chiffré mène hors du volume : refus par précaution"
_LINK_MESSAGE = "un chemin du répertoire chiffré passe par un lien symbolique : refus par précaution"
# Un chemin refusé à l'intérieur d'un volume monté n'est pas un volume d'un mauvais type : il a son propre
# code, la raison précise reste dans mount_reason.
PATH_REFUSED = "volume_path_refused"
_DIR_MODE_MESSAGE = (
    "un répertoire du volume chiffré n'est pas privé (mode 700, propriétaire du serveur) : "
    "refus par précaution"
)


def verdict_error(verdict: MountVerdict) -> ToolError:
    """Le refus qui correspond à un verdict autre qu'un montage utilisable."""
    code = verdict.error_code or "mount_unreadable"
    action = None
    if verdict.state in (MountState.CLOSED, MountState.CLOSED_OR_ABSENT):
        action = OPERATOR_OPENS_DIRECTORY
    if code == "project_root_missing":
        message = _PROJECT_ROOT_MISSING_MESSAGE
    else:
        message = _STATE_MESSAGES[verdict.state]
    return ToolError(code, message, action_expected=action, extra_server={"mount_reason": verdict.reason})


@dataclass(frozen=True)
class VolumeFile:
    data: bytes
    st_mode: int
    st_uid: int


class VolumeFileError(Exception):
    """Un fichier du volume a été refusé avant ou pendant sa lecture.

    ``reason`` est l'une de ``not_regular_file``, ``bad_owner``, ``bad_mode``, ``too_large``,
    ``missing``, ``outside_volume`` (le chemin sort du volume ou de son périphérique) ou ``link``
    (un lien a été suivi sous le répertoire du volume).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Volume:
    """La seule voie d'accès au volume monté ; contrôle le montage à chaque appel."""

    def __init__(
        self,
        checker: MountChecker,
        secrets_dir: str | os.PathLike[str],
        secrets_crypt_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._checker = checker
        self._secrets_dir = os.fspath(secrets_dir)
        self._secrets_crypt_dir = None if secrets_crypt_dir is None else os.fspath(secrets_crypt_dir)

    @property
    def euid(self) -> int:
        return self._checker.euid

    def require_mounted(self) -> Path:
        """Recontrôler le montage et rendre la vraie racine du projet, ou refuser."""
        verdict = self._checker.check(self._secrets_dir, self._secrets_crypt_dir)
        if verdict.state is not MountState.MOUNTED or verdict.error_code is not None:
            raise verdict_error(verdict)
        mount_point = self._checker.realpath_fn(self._secrets_dir)
        return Path(self._checker.realpath_fn(os.path.join(mount_point, PROJECT_DIR_NAME)))

    def holds(self, path: str | os.PathLike[str]) -> bool:
        """Dit si ``path`` se trouve dans le volume monté, sur son périphérique ; faux quand il n'est pas monté.

        Une réponse sur un chemin que l'opérateur a déclaré, comme le profil dédié du navigateur : elle dit
        où le chemin mène maintenant, pas ce que le navigateur en fait.
        """
        try:
            self.require_mounted()
        except ToolError:
            return False
        mount_point = Path(self._checker.realpath_fn(self._secrets_dir))
        real = Path(self._checker.realpath_fn(os.fspath(path)))
        if real != mount_point and mount_point not in real.parents:
            return False
        existing = real
        while not os.path.lexists(existing) and existing != mount_point:
            existing = existing.parent
        try:
            return self._checker.stat_fn(os.fspath(existing)).st_dev == self._checker.stat_fn(os.fspath(mount_point)).st_dev
        except OSError:
            return False

    def resolve_inside(self, *parts: str) -> Path:
        """Vrai chemin de ``parts`` sous la racine du projet, refusé s'il sort du volume."""
        root = self.require_mounted()
        return self._resolve(root, parts)

    def _resolve(self, root: Path, parts: tuple[str, ...]) -> Path:
        for part in parts:
            if not isinstance(part, str) or part in ("", ".", "..") or "/" in part or "\x00" in part:
                raise ValueError("path parts must be plain names")
        real = Path(self._checker.realpath_fn(os.path.join(root, *parts)))
        if real != root and root not in real.parents:
            raise ToolError(PATH_REFUSED, _OUTSIDE_MESSAGE, extra_server={"mount_reason": "path_outside_volume"})
        existing = real
        while not os.path.lexists(existing) and existing != root:
            existing = existing.parent
        try:
            same_device = self._checker.stat_fn(os.fspath(existing)).st_dev == self._checker.stat_fn(os.fspath(root)).st_dev
        except OSError:
            same_device = False
        if not same_device:
            raise ToolError(PATH_REFUSED, _OUTSIDE_MESSAGE, extra_server={"mount_reason": "path_outside_volume"})
        return real

    def _resolve_exact(self, root: Path, parts: tuple[str, ...]) -> Path:
        # Ouvrir le chemin résolu suivrait un lien que O_NOFOLLOW est censé refuser : un fichier n'est donc
        # atteint que par un chemin sans lien.
        real = self._resolve(root, parts)
        if real != root.joinpath(*parts):
            raise ToolError(PATH_REFUSED, _LINK_MESSAGE, extra_server={"mount_reason": "symlink"})
        return real

    def ensure_dir(self, *parts: str) -> Path:
        """Créer chaque niveau manquant en mode 700 et vérifier que chaque niveau est privé."""
        root = self.require_mounted()
        path = root
        for depth in range(1, len(parts) + 1):
            path = self._resolve_exact(root, parts[:depth])
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                pass
            info = os.lstat(path)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != self.euid
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise ToolError(PATH_REFUSED, _DIR_MODE_MESSAGE, extra_server={"mount_reason": "dir_not_private"})
        return path

    def open_append(self, *parts: str) -> int:
        """Ouvrir un fichier du volume en ajout ; l'appelant ferme le descripteur."""
        path = self._resolve_exact(self.require_mounted(), parts)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(path, flags, 0o600)
        try:
            self._check_private_file(fd)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def settle_file(self, *parts: str, source: str, wanted: str) -> Path:
        """Rendre privée ``source``, dans un répertoire du volume, et lui donner le nom ``wanted``.

        Pour un fichier qu'un autre processus a écrit sous un nom à lui (un téléchargement du navigateur) :
        le fichier est ouvert sans suivre de lien, doit être un fichier ordinaire de l'utilisateur du
        serveur, passe en mode 600, et prend ``wanted`` (un nom simple) avec un suffixe numérique si ce nom
        est pris.
        """
        if wanted != safe_name(wanted):
            raise ValueError("the new name must be a plain safe name")
        root = self.require_mounted()
        directory = self._resolve_exact(root, parts)
        current = self._resolve_exact(root, (*parts, source))
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        fd = os.open(current, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.euid:
                raise ToolError(PATH_REFUSED, _DIR_MODE_MESSAGE, extra_server={"mount_reason": "not_regular_file"})
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        stem, dot, extension = wanted.rpartition(".")
        if not stem:
            stem, dot, extension = wanted, "", ""
        target = directory / wanted
        number = 0
        while os.path.lexists(target):
            number += 1
            target = directory / f"{stem}-{number}{dot}{extension}"
        os.rename(current, self._resolve_exact(root, (*parts, target.name)))
        return target

    def write_new(self, *parts: str, data: bytes, mode: int = 0o600) -> Path:
        """Créer un fichier qui ne doit pas encore exister et y écrire ``data``."""
        if mode & 0o077:
            raise ValueError("files under the volume are private to their owner")
        path = self._resolve_exact(self.require_mounted(), parts)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(path, flags, mode)
        try:
            self._check_private_file(fd)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)
        return path

    def publish_new(self, *parts: str, data: bytes) -> Path:
        """Créer un fichier qui ne doit pas encore exister, en entier ou pas du tout, sans jamais en remplacer un.

        Les données vont dans un fichier caché du même répertoire (mode 600), qui prend son nom par un lien
        dur qui échoue quand le nom est pris ; ainsi un lecteur ne rencontre jamais un demi-fichier, et rien
        d'autre qu'un fichier complet ne reste sous le nom. Sur un système de fichiers qui ne peut pas faire
        de lien, le nom est pris d'abord (créé en exclusivité) puis rempli, et retiré si cela échoue.
        """
        root = self.require_mounted()
        path = self._resolve_exact(root, parts)
        if os.path.lexists(path):
            raise FileExistsError(errno.EEXIST, "the name is taken")
        hidden = self._resolve_exact(root, (*parts[:-1], f".{parts[-1]}.{os.urandom(6).hex()}.tmp"))
        self._write_exclusive(hidden, data)
        try:
            os.link(hidden, path)
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.ENOSYS, errno.EXDEV, errno.EMLINK):
                raise
            self._write_exclusive(path, data, remove_on_failure=True)
        finally:
            os.unlink(hidden)
        return path

    def replace_file(self, *parts: str, data: bytes) -> Path:
        """Donner à un fichier ``data`` en entier, qu'il existe ou non : un lecteur ne voit jamais un demi-fichier.

        Les données vont dans un fichier caché du même répertoire (mode 600) qui prend le nom par un
        renommage ; le répertoire doit exister (``ensure_dir``).
        """
        root = self.require_mounted()
        path = self._resolve_exact(root, parts)
        hidden = self._resolve_exact(root, (*parts[:-1], f".{parts[-1]}.{os.urandom(6).hex()}.tmp"))
        self._write_exclusive(hidden, data)
        try:
            os.replace(hidden, path)
        except BaseException:
            os.unlink(hidden)
            raise
        return path

    def _write_exclusive(self, path: Path, data: bytes, *, remove_on_failure: bool = True) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(path, flags, 0o600)
        try:
            self._check_private_file(fd)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            if remove_on_failure:
                os.unlink(path)
            raise
        os.close(fd)

    def read_file(self, *parts: str, max_bytes: int) -> VolumeFile:
        """Lire un fichier ordinaire privé du volume, d'au plus ``max_bytes``."""
        try:
            path = self._resolve_exact(self.require_mounted(), parts)
        except ToolError as exc:
            if exc.extra_server.get("mount_reason") == "symlink":
                raise VolumeFileError("not_regular_file") from None
            raise
        return self._read_private(path, max_bytes)

    def read_file_at(self, path: str | os.PathLike[str], *, max_bytes: int) -> VolumeFile:
        """Lire un fichier ordinaire privé n'importe où sous le volume monté, d'au plus ``max_bytes``.

        Pour un fichier gardé à côté du répertoire du projet, comme un ancien fichier d'identifiants à
        convertir. Le chemin doit être absolu. Il est résolu d'abord et doit mener dans le volume, sur son
        périphérique ; et aucun lien ne doit avoir été suivi sous le répertoire propre du volume, puisqu'un
        lien pourrait mener là où l'opérateur n'a jamais regardé. Le fichier est ensuite ouvert sans suivre
        de lien et doit appartenir à l'utilisateur du serveur, en mode 600.
        """
        return self._read_private(Path(self._contained_real(path)), max_bytes)

    def list_directory_at(self, path: str | os.PathLike[str]) -> list[str]:
        """Les noms, triés, de ce qui n'est pas un répertoire dans un répertoire du volume monté.

        Mêmes règles de chemin que ``read_file_at`` : absolu, dans le volume, sur son périphérique, sans
        lien suivi. Un lien reste dans la liste : le lire est refusé, ce qui le rend visible plutôt que de
        le taire. Ne descend jamais dans un sous-répertoire.
        """
        real = self._contained_real(path)
        try:
            with os.scandir(real) as entries:
                return sorted(entry.name for entry in entries if not entry.is_dir(follow_symlinks=False))
        except FileNotFoundError:
            raise VolumeFileError("missing") from None
        except NotADirectoryError:
            raise VolumeFileError("not_regular_file") from None

    def _contained_real(self, path: str | os.PathLike[str]) -> str:
        self.require_mounted()
        given = os.path.normpath(os.fspath(path))
        if not os.path.isabs(given):
            raise ValueError("the path must be absolute")
        mount_point = self._checker.realpath_fn(self._secrets_dir)
        real = self._checker.realpath_fn(given)
        if real != mount_point and not real.startswith(mount_point.rstrip(os.sep) + os.sep):
            raise VolumeFileError("outside_volume")
        # Le répertoire du volume tel que l'opérateur l'a écrit, et tel qu'il est vraiment : sous l'un ou l'autre,
        # le chemin tel que donné doit être le vrai, sinon un lien a été suivi.
        starts = [prefix for prefix in (os.path.normpath(self._secrets_dir), mount_point) if given == prefix or given.startswith(prefix.rstrip(os.sep) + os.sep)]
        if not any(os.path.normpath(os.path.join(mount_point, os.path.relpath(given, prefix))) == real for prefix in starts):
            raise VolumeFileError("link")
        try:
            same_device = self._checker.stat_fn(real).st_dev == self._checker.stat_fn(mount_point).st_dev
        except FileNotFoundError:
            raise VolumeFileError("missing") from None
        except OSError:
            same_device = False
        if not same_device:
            raise VolumeFileError("outside_volume")
        return real

    def _read_private(self, path: Path, max_bytes: int) -> VolumeFile:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            raise VolumeFileError("missing") from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise VolumeFileError("not_regular_file") from None
            raise
        try:
            info = self._check_private_file(fd, raise_tool_error=False)
            chunks = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise VolumeFileError("too_large")
        return VolumeFile(data=data, st_mode=info.st_mode, st_uid=info.st_uid)

    def _check_private_file(self, fd: int, *, raise_tool_error: bool = True) -> os.stat_result:
        info = os.fstat(fd)
        reason = None
        if not stat.S_ISREG(info.st_mode):
            reason = "not_regular_file"
        elif info.st_uid != self.euid:
            reason = "bad_owner"
        elif info.st_mode & 0o077:
            reason = "bad_mode"
        if reason is None:
            return info
        if raise_tool_error:
            raise ToolError(PATH_REFUSED, _DIR_MODE_MESSAGE, extra_server={"mount_reason": reason})
        raise VolumeFileError(reason)


def safe_name(text: str, max_len: int = 64) -> str:
    """Un nom de fichier construit à partir d'un texte non fiable : caractères simples seulement, borné."""
    name = _SAFE_NAME_RE.sub("_", text).lstrip(".")[:max_len]
    return name or "unnamed"


NEXT_COMMANDS: dict[MountState, str] = {
    MountState.CLOSED: f"{SETUP_COMMAND} open",
    MountState.NEVER_INITIALIZED: f"{SETUP_COMMAND} check",
    MountState.CLOSED_OR_ABSENT: f"{SETUP_COMMAND} check",
    MountState.NOT_GOCRYPTFS: f"{SETUP_COMMAND} check",
    MountState.UNREADABLE: f"{SETUP_COMMAND} check",
}


def next_command(verdict: MountVerdict) -> str | None:
    """La sous-commande de l'assistant qui lève un refus du volume, ou ``None`` quand le volume est utilisable."""
    if verdict.error_code == "project_root_missing":
        return f"{SETUP_COMMAND} check"
    return None if verdict.error_code is None else NEXT_COMMANDS[verdict.state]
