"""Know whether the encrypted directory is mounted, and write inside it safely.

Why this module exists:
    A secret or a page capture written while the encrypted volume is closed
    lands in clear text on the underlying disk. Whether the volume is really
    mounted must be read from the kernel on every access, never assumed nor
    remembered, and every write into it must go through that check.
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

_WAITING_MESSAGE = (
    "le répertoire chiffré est fermé ou non confirmé : l'opérateur doit l'ouvrir, puis relancer"
)
OPERATOR_OPENS_DIRECTORY = "operator_opens_directory"


class MountInfoError(ValueError):
    """A line of the mount table could not be understood."""


@dataclass(frozen=True)
class MountEntry:
    mount_point: str
    fs_type: str
    super_options: tuple[str, ...]


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
    """Parse the content of ``/proc/self/mountinfo``.

    A malformed line raises instead of being skipped: skipping it could hide
    the very mount that is being looked for.
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
            )
        )
    return entries


class MountChecker:
    """Read the mount table and decide, on every call, the state of the volume."""

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
            # The root file system is always listed; an empty table is not
            # a trustworthy reading.
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


_MESSAGES = {
    "mount_unreadable": "l'état de montage du répertoire chiffré ne peut pas être lu : refus par précaution",
    "mount_not_gocryptfs": (
        "le répertoire déclaré n'est pas un volume gocryptfs monté pour l'utilisateur du serveur "
        "seul : refus par précaution"
    ),
    "encrypted_dir_closed": _WAITING_MESSAGE,
    "encrypted_dir_undetermined": _WAITING_MESSAGE,
    "project_root_missing": (
        "le répertoire chiffré est ouvert mais le sous-répertoire du projet n'existe pas : "
        "l'opérateur doit le créer (mkdir -m 700 \"<secrets_dir>/mcp-server-diwall-browser\"), "
        "puis relancer"
    ),
}
_NEVER_INITIALIZED_MESSAGE = (
    "le répertoire chiffré n'est pas confirmé : le répertoire chiffré déclaré dans le profil "
    "(secrets_crypt_dir) ne porte pas de volume initialisé ; l'opérateur doit vérifier le profil"
)
_OUTSIDE_MESSAGE = "un chemin du répertoire chiffré mène hors du volume : refus par précaution"
_LINK_MESSAGE = "un chemin du répertoire chiffré passe par un lien symbolique : refus par précaution"
# A refused path inside a mounted volume is not a volume of the wrong kind:
# it has its own code, the precise reason stays in mount_reason.
PATH_REFUSED = "volume_path_refused"
_DIR_MODE_MESSAGE = (
    "un répertoire du volume chiffré n'est pas privé (mode 700, propriétaire du serveur) : "
    "refus par précaution"
)


def verdict_error(verdict: MountVerdict) -> ToolError:
    """The refusal that matches a verdict other than a usable mount."""
    code = verdict.error_code or "mount_unreadable"
    action = None
    if verdict.state in (MountState.CLOSED, MountState.CLOSED_OR_ABSENT):
        action = OPERATOR_OPENS_DIRECTORY
    message = _MESSAGES[code]
    if verdict.state is MountState.NEVER_INITIALIZED:
        message = _NEVER_INITIALIZED_MESSAGE
    return ToolError(code, message, action_expected=action, extra_server={"mount_reason": verdict.reason})


@dataclass(frozen=True)
class VolumeFile:
    data: bytes
    st_mode: int
    st_uid: int


class VolumeFileError(Exception):
    """A file of the volume was refused before or while being read.

    ``reason`` is one of ``not_regular_file``, ``bad_owner``, ``bad_mode``,
    ``too_large`` or ``missing``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Volume:
    """The only way into the mounted volume; checks the mount on every call."""

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
        """Re-check the mount and return the real project root, or refuse."""
        verdict = self._checker.check(self._secrets_dir, self._secrets_crypt_dir)
        if verdict.state is not MountState.MOUNTED or verdict.error_code is not None:
            raise verdict_error(verdict)
        mount_point = self._checker.realpath_fn(self._secrets_dir)
        return Path(self._checker.realpath_fn(os.path.join(mount_point, PROJECT_DIR_NAME)))

    def resolve_inside(self, *parts: str) -> Path:
        """Real path of ``parts`` under the project root, refused if it leaves the volume."""
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
        # Opening the resolved path would follow a link that O_NOFOLLOW is
        # meant to refuse, so a file is only reached by a path without links.
        real = self._resolve(root, parts)
        if real != root.joinpath(*parts):
            raise ToolError(PATH_REFUSED, _LINK_MESSAGE, extra_server={"mount_reason": "symlink"})
        return real

    def ensure_dir(self, *parts: str) -> Path:
        """Create each missing level in mode 700 and check every level is private."""
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
        """Open a file of the volume for appending; the caller closes the descriptor."""
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
        """Make ``source`` of a volume directory private and give it the name ``wanted``.

        For a file another process wrote under a name of its own (a browser
        download): the file is opened without following a link, must be a
        regular file of the server's user, becomes mode 600, and takes
        ``wanted`` (a plain name) with a numeric suffix if that name is taken.
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
        """Create a file that must not exist yet and write ``data`` into it."""
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

    def read_file(self, *parts: str, max_bytes: int) -> VolumeFile:
        """Read a private regular file of the volume, at most ``max_bytes`` long."""
        try:
            path = self._resolve_exact(self.require_mounted(), parts)
        except ToolError as exc:
            if exc.extra_server.get("mount_reason") == "symlink":
                raise VolumeFileError("not_regular_file") from None
            raise
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
    """A file name built from untrusted text: plain characters only, bounded."""
    name = _SAFE_NAME_RE.sub("_", text).lstrip(".")[:max_len]
    return name or "unnamed"
