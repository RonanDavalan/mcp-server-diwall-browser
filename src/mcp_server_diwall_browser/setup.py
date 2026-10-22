"""Commande d'opérateur : guider le premier lancement, du volume chiffré au navigateur dédié.

Pourquoi ce module existe :
    Le serveur refuse de travailler sans répertoire chiffré, et il ne monte, ne démonte ni ne crée
    jamais rien : un premier utilisateur n'a ni volume ni profil, et un refus sans commande suivante
    le laisse conclure qu'il ne peut pas avancer. Cette commande est lancée par l'humain dans un
    terminal. Elle lit le poste (``check``), crée ou ouvre le volume gocryptfs, écrit le profil,
    lance le navigateur sur le profil du volume et ferme le tout. Le mot de passe n'a qu'un guichet,
    le terminal que la commande passe à gocryptfs : elle ne le lit, ne le garde ni ne le transmet.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, TextIO

from . import hardening, operator_profile
from .operator_profile import ENV_VAR
from .mount import (
    GOCRYPTFS_TYPE,
    PROJECT_DIR_NAME,
    SETUP_COMMAND,
    MountChecker,
    MountEntry,
    MountInfoError,
    MountState,
    MountVerdict,
    parse_mountinfo,
)
from .output import ToolError

SESSION_DIR_NAME = "mcp-server-diwall-browser-session"
DEFAULT_CRYPT = ("Vaults", "Main.enc")
DEFAULT_MOUNT = ("Vaults", "Main")
CRYPT_SUFFIX = ".enc"
STOP_TIMEOUT_S = 30.0
# Les paquets qui apportent le mécanisme, par gestionnaire reconnu : (programme, nom court, verbe d'installation).
PACKAGE_MANAGERS = (
    ("apt-get", "apt", "apt install"),
    ("dnf", "dnf", "dnf install"),
    ("zypper", "zypper", "zypper install"),
    ("pacman", "pacman", "pacman -S"),
)
MECHANISM_PACKAGES = "gocryptfs fuse3"
BROWSER_FAMILIES = (
    ("chrome", ("google-chrome", "google-chrome-stable")),
    ("chromium", ("chromium", "chromium-browser")),
    ("brave", ("brave-browser",)),
)
CONFIRMATION_TOOLS = ("kdialog", "zenity")
# Ce que le noyau dit d'un volume chiffré que le serveur ne prend pas en charge, par type de système de fichiers.
OTHER_ENCRYPTED_TYPES = {"ecryptfs": "ecryptfs", "fuse.encfs": "encfs", "fuse.cryfs": "cryfs"}
DEVICE_MAPPER_LABEL = "device-mapper (LUKS)"
PLASMA_VAULT_DIR = (".local", "share", "plasma-vault")
MEMORY_TYPES = ("tmpfs", "ramfs")
USAGE = (
    f"usage : {SETUP_COMMAND} {{check,create,open,browser,close}}\n"
    "  check [--json]\n"
    "  create [--temporary | --crypt-dir CHEMIN --mount-dir CHEMIN] [--browser chrome|chromium|brave]\n"
    "  open | browser | close"
)


class Refused(Exception):
    """Un refus prévu ; ``code`` est le mot stable imprimé après ``refus``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class UsageError(Exception):
    """Une ligne de commande que la commande ne comprend pas."""


@dataclass(frozen=True)
class Environment:
    """Ce que la commande lit du poste et les programmes qu'elle lance ; les tests lui en donnent d'autres."""

    environ: Mapping[str, str]
    home: Path
    euid: int
    mountinfo_path: str
    swaps_path: str
    fuse_path: str
    which: Callable[[str], str | None]
    is_terminal: Callable[[], bool]
    is_crypt_device: Callable[[str], bool]
    run_interactive: Callable[[Sequence[str]], int]
    spawn_detached: Callable[[Sequence[str]], int]
    port_listening: Callable[[int], bool]
    find_browser_pids: Callable[[str], list[int]]
    stop_processes: Callable[[list[int], float], bool]
    mount_checker: MountChecker


def _run_interactive(argv: Sequence[str]) -> int:
    # Le programme hérite du terminal : c'est lui, et lui seul, qui demande et lit le mot de passe.
    return subprocess.run(list(argv), check=False).returncode


def _spawn_detached(argv: Sequence[str]) -> int:
    # Une session à lui et aucun terminal : le navigateur survit à cette commande et à son terminal.
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return process.pid


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _is_crypt_device(source: str) -> bool:
    """Dit si un périphérique de ``/dev/mapper`` est un volume chiffré (dm-crypt, donc LUKS)."""
    try:
        name = os.path.basename(os.path.realpath(source))
        uuid = Path("/sys/class/block", name, "dm", "uuid").read_text()
    except OSError:
        return os.path.basename(source).startswith("luks-")
    return uuid.startswith("CRYPT-")


def _find_browser_pids(profile_dir: str, euid: int | None = None) -> list[int]:
    """Les processus principaux du navigateur lancés sur ``profile_dir`` : ceux qui n'ont pas d'option ``--type=``."""
    wanted = f"--user-data-dir={os.path.normpath(profile_dir)}"
    owner = os.geteuid() if euid is None else euid
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != owner:
                continue
            arguments = (entry / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        normalised = [f"--user-data-dir={os.path.normpath(a.partition('=')[2])}" if a.startswith("--user-data-dir=") else a for a in arguments]
        if wanted in normalised and not any(a.startswith("--type=") for a in arguments):
            found.append(int(entry.name))
    return found


def _stop_processes(pids: list[int], timeout_s: float) -> bool:
    """Demander l'arrêt par SIGTERM et attendre que chaque processus ait disparu ; faux si le délai passe."""
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_s
    remaining = list(pids)
    while remaining and time.monotonic() < deadline:
        remaining = [pid for pid in remaining if _alive(pid)]
        if remaining:
            time.sleep(0.2)
    if not remaining:
        # Les processus fils d'un navigateur suivent son arrêt de peu ; le démontage les attend.
        time.sleep(1.0)
    return not remaining


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def production_environment() -> Environment:
    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    euid = os.geteuid()
    return Environment(
        environ=os.environ,
        home=home,
        euid=euid,
        mountinfo_path="/proc/self/mountinfo",
        swaps_path="/proc/swaps",
        fuse_path="/dev/fuse",
        which=shutil.which,
        is_terminal=lambda: os.isatty(0) and os.isatty(1),
        is_crypt_device=_is_crypt_device,
        run_interactive=_run_interactive,
        spawn_detached=_spawn_detached,
        port_listening=_port_listening,
        find_browser_pids=lambda profile_dir: _find_browser_pids(profile_dir, euid),
        stop_processes=_stop_processes,
        mount_checker=MountChecker(),
    )


# --- le poste ------------------------------------------------------------------------------------


def _shown(env: Environment, path: str | os.PathLike[str]) -> str:
    """Un chemin tel qu'on l'écrit à l'opérateur : ``~/…`` sous son domicile."""
    text = os.fspath(path)
    home = os.fspath(env.home).rstrip(os.sep)
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~/" + text[len(home) + 1 :]
    return text


def _mount_entries(env: Environment) -> list[MountEntry]:
    try:
        with open(env.mountinfo_path, "rb") as handle:
            return parse_mountinfo(handle.read().decode("utf-8", "surrogateescape"))
    except (OSError, MountInfoError):
        raise Refused("mount_unreadable", "la table des montages ne peut pas être lue : rien n'est créé par précaution") from None


def _package_manager(env: Environment) -> tuple[str | None, str | None]:
    """Le gestionnaire de paquets reconnu et la commande qui installe le mécanisme."""
    for program, name, verb in PACKAGE_MANAGERS:
        if env.which(program):
            prefix = "" if env.euid == 0 else "sudo "
            return name, f"{prefix}{verb} {MECHANISM_PACKAGES}"
    return None, None


def _modprobe_command(env: Environment) -> str:
    return "modprobe fuse" if env.euid == 0 else "sudo modprobe fuse"


def _mechanism_missing(env: Environment) -> str | None:
    """Ce qui manque au mécanisme gocryptfs, dit avec le geste qui le lève ; ``None`` quand il est utilisable."""
    missing = [name for name in ("gocryptfs", "fusermount3") if not env.which(name)]
    if missing:
        _manager, command = _package_manager(env)
        how = f"installez-les : {command}" if command else f"installez les paquets {MECHANISM_PACKAGES} avec le gestionnaire de paquets du poste (guide de l'opérateur, section 1)"
        return f"{' et '.join(missing)} {'est absent' if len(missing) == 1 else 'sont absents'} : {how}"
    if not os.path.exists(env.fuse_path):
        return (
            f"{env.fuse_path} est absent : le noyau ou le conteneur n'offre pas FUSE ; sur un poste ordinaire "
            f"`{_modprobe_command(env)}` le charge, dans un conteneur il faut le lancer avec `--device /dev/fuse`"
        )
    return None


def _scan_closed_volumes(env: Environment, entries: list[MountEntry]) -> list[Path]:
    """Les volumes gocryptfs fermés aux emplacements habituels (Plasma Vault, ``~/Vaults*``), sans jamais entrer dans un montage."""
    mounted_sources = {os.path.realpath(e.source) for e in entries if e.fs_type == GOCRYPTFS_TYPE and e.source}
    mount_points = {os.path.realpath(e.mount_point) for e in entries}
    bases = sorted(env.home.glob("Vaults*")) + [env.home.joinpath(*PLASMA_VAULT_DIR)]
    found = []
    for base in bases:
        if not base.is_dir() or os.path.realpath(base) in mount_points:
            continue
        for child in sorted(base.iterdir()):
            real = os.path.realpath(child)
            if real in mount_points or real in mounted_sources or not child.is_dir():
                continue
            if (child / "gocryptfs.conf").is_file():
                found.append(child)
    return found


def _unsupported_volumes(env: Environment, entries: list[MountEntry]) -> list[dict[str, str]]:
    found = []
    for entry in entries:
        label = OTHER_ENCRYPTED_TYPES.get(entry.fs_type)
        if label is None and entry.source.startswith("/dev/mapper/") and env.is_crypt_device(entry.source):
            label = "device-mapper"
        if label is not None:
            found.append({"mount_point": _shown(env, entry.mount_point), "type": label})
    return found


def _swap_present(env: Environment) -> bool:
    try:
        lines = Path(env.swaps_path).read_text().splitlines()
    except OSError:
        return False
    return len(lines) > 1


def _profile_state(env: Environment) -> tuple[dict[str, Any], operator_profile.Profile | None]:
    shown = _shown(env, _profile_target(env))
    try:
        profile = operator_profile.load_profile(env.environ, euid=env.euid)
    except ToolError as exc:
        if exc.extra_server.get("missing") == "profile":
            return {"state": "absent", "path": shown, "reason": None}, None
        return {"state": "invalid", "path": shown, "reason": exc.message or exc.code}, None
    return {"state": "valid", "path": shown, "reason": None}, profile


def _profile_target(env: Environment) -> Path:
    designated = env.environ.get(ENV_VAR, "")
    if designated and os.path.isabs(designated):
        return Path(designated)
    return operator_profile.standard_profile_path(env.environ)


def _browser_names(env: Environment) -> list[str]:
    return [name for _family, names in BROWSER_FAMILIES for name in names if env.which(name)]


def _default_family(env: Environment) -> str:
    for family, names in BROWSER_FAMILIES:
        if any(env.which(name) for name in names):
            return family
    return "chromium"


def _executable(env: Environment, family: str) -> str | None:
    for known, names in BROWSER_FAMILIES:
        if known == family:
            return next((path for name in names if (path := env.which(name))), None)
    return None


def _mechanisms(env: Environment, entries: list[MountEntry], missing: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    how = None
    if missing is not None:
        _manager, command = _package_manager(env)
        if not (env.which("gocryptfs") and env.which("fusermount3")):
            how = command or f"installer les paquets {MECHANISM_PACKAGES} avec le gestionnaire de paquets du poste (guide de l'opérateur, section 1)"
        else:
            how = _modprobe_command(env)
    rows.append(
        {"mechanism": "gocryptfs", "platform": "linux", "supported_by_server": True, "available_here": missing is None, "how_to_obtain": how}
    )
    beside = (
        f"non pris en charge : le serveur exige un montage gocryptfs ; la voie est un volume gocryptfs à côté "
        f"(`{SETUP_COMMAND} create`)"
    )
    labels = {item["type"] for item in _unsupported_volumes(env, entries)}
    for label in sorted(labels):
        name = DEVICE_MAPPER_LABEL if label == "device-mapper" else label
        rows.append(
            {"mechanism": name, "platform": "linux", "supported_by_server": False, "available_here": True, "how_to_obtain": beside}
        )
    return rows


def build_report(env: Environment) -> dict[str, Any]:
    """L'état du poste et la prochaine étape ; ne lit rien du contenu d'un volume et n'écrit rien."""
    try:
        entries = _mount_entries(env)
    except Refused:
        entries = []
    missing = _mechanism_missing(env)
    manager, _command = _package_manager(env)
    profile_info, profile = _profile_state(env)
    volume = None
    if profile is not None:
        verdict = env.mount_checker.check(profile.secrets_dir, profile.secrets_crypt_dir)
        volume = {
            "state": verdict.state.value,
            "reason": verdict.reason,
            "error_code": verdict.error_code,
            "mount_point": _shown(env, profile.secrets_dir),
            "encrypted_dir": None if profile.secrets_crypt_dir is None else _shown(env, profile.secrets_crypt_dir),
        }
    closed = _scan_closed_volumes(env, entries)
    port = profile.browser.debug_port if profile is not None else 9222
    report: dict[str, Any] = {
        "platform": "linux",
        "mechanisms": _mechanisms(env, entries, missing),
        "package_manager": {"name": manager},
        "fuse": {"device_present": os.path.exists(env.fuse_path)},
        "volumes": {
            "closed": [_shown(env, path) for path in closed],
            "unsupported": _unsupported_volumes(env, entries),
        },
        "profile": profile_info,
        "volume": volume,
        "browser": {
            "installed": _browser_names(env),
            "port": port,
            "listening": env.port_listening(port),
        },
        "swap": {
            "present": _swap_present(env),
            "encryption": "undetermined",
            "note": "l'espace d'échange peut recevoir le contenu de la mémoire vive ; son chiffrement est non déterminé (non confirmé dans la documentation)",
        },
        "terminal": {"interactive": env.is_terminal()},
        "display": {"available": bool(env.environ.get("DISPLAY") or env.environ.get("WAYLAND_DISPLAY"))},
        "confirmation_window": {"tool": next((tool for tool in CONFIRMATION_TOOLS if env.which(tool)), None)},
    }
    report["next_step"] = _next_step(env, report, missing, profile, closed)
    return report


def _next_step(
    env: Environment, report: dict[str, Any], missing: str | None, profile: operator_profile.Profile | None, closed: list[Path]
) -> dict[str, Any]:
    if missing is not None:
        row = report["mechanisms"][0]
        return {"command": row["how_to_obtain"], "why": f"le chiffrement ne se saute pas, il se rend possible : {missing}"}
    state = report["profile"]["state"]
    if state == "invalid":
        return {"command": None, "why": f"le profil ne se lit pas ({report['profile']['reason']}) : le corriger, ou le supprimer puis relancer `{SETUP_COMMAND} check`"}
    if profile is None:
        if closed:
            crypt = closed[0]
            mount = env.home / "Vaults" / (crypt.name[: -len(CRYPT_SUFFIX)] if crypt.name.endswith(CRYPT_SUFFIX) else crypt.name)
            command = f"{SETUP_COMMAND} create --crypt-dir {_shown(env, crypt)} --mount-dir {_shown(env, mount)}"
            return {"command": command, "why": "un volume gocryptfs fermé existe : cette commande le prend, l'ouvre (son mot de passe est le vôtre) et écrit le profil"}
        return {
            "command": f"{SETUP_COMMAND} create --temporary",
            "why": (
                "aucun profil ni volume : un essai immédiat se fait sur un volume temporaire en mémoire vive, détruit à sa fermeture ; "
                f"`{SETUP_COMMAND} create` fait un volume durable (~/Vaults/Main.enc, ouvert sur ~/Vaults/Main)"
            ),
        }
    volume = report["volume"]
    assert volume is not None
    code, mount_state = volume["error_code"], volume["state"]
    if mount_state == MountState.MOUNTED.value and code is None:
        if report["browser"]["listening"]:
            return {"command": None, "why": "tout est en place : le volume est ouvert et le navigateur répond ; redémarrez le client MCP si le serveur vient d'y être ajouté"}
        return {"command": f"{SETUP_COMMAND} browser", "why": "le volume est ouvert : cette commande lance le navigateur sur son profil ; vous y ouvrez vous-même vos sessions"}
    if mount_state == MountState.MOUNTED.value:
        root = f"{volume['mount_point']}/{PROJECT_DIR_NAME}"
        return {
            "command": f'mkdir -m 700 "{root}" "{root}/credentials"',
            "why": "le volume est ouvert mais le dossier du serveur n'y est pas : cette commande le crée",
        }
    if mount_state == MountState.CLOSED.value:
        return {"command": f"{SETUP_COMMAND} open", "why": "le volume est fermé : l'ouvrir demande votre mot de passe, que vous tapez vous-même"}
    if mount_state == MountState.NEVER_INITIALIZED.value:
        return {
            "command": f"{SETUP_COMMAND} create",
            "why": "le profil désigne un volume qui n'existe plus (une séance temporaire terminée, par exemple) : create en fait un durable, et garde vos sites autorisés",
        }
    if mount_state == MountState.NOT_GOCRYPTFS.value:
        return {
            "command": f"{SETUP_COMMAND} create",
            "why": "le répertoire du profil est monté par un autre mécanisme (LUKS, fscrypt, un autre FUSE) que le serveur ne prend pas en charge : la voie est un volume gocryptfs à côté, que create fait et que le profil désigne ensuite",
        }
    if mount_state == MountState.CLOSED_OR_ABSENT.value:
        return {
            "command": None,
            "why": "le profil ne dit pas où est le volume chiffré sur le disque : ajoutez-le au profil (guide, section 5), ou ouvrez votre volume avec votre outil habituel",
        }
    return {"command": None, "why": "la table des montages ne se lit pas : rien n'est tenté par précaution"}


def render_report(report: dict[str, Any]) -> str:
    lines = ["Premier lancement : état du poste", ""]
    for row in report["mechanisms"]:
        state = "disponible" if row["available_here"] else "manquant"
        kind = "pris en charge" if row["supported_by_server"] else "non pris en charge"
        line = f"- mécanisme {row['mechanism']} ({row['platform']}) : {kind}, {state}"
        lines.append(line + (f" ; {row['how_to_obtain']}" if row["how_to_obtain"] else ""))
    lines.append(f"- /dev/fuse : {'présent' if report['fuse']['device_present'] else 'absent'}")
    manager = report["package_manager"]["name"]
    lines.append(f"- gestionnaire de paquets : {manager or 'non reconnu'}")
    volumes = report["volumes"]
    lines.append(f"- volumes gocryptfs fermés trouvés : {', '.join(volumes['closed']) or 'aucun'}")
    for item in volumes["unsupported"]:
        lines.append(f"- volume {item['type']} monté sur {item['mount_point']} : non pris en charge par le serveur")
    profile = report["profile"]
    lines.append(f"- profil du serveur : {profile['state']} ({profile['path']})" + (f" — {profile['reason']}" if profile["reason"] else ""))
    if report["volume"] is not None:
        lines.append(f"- répertoire chiffré du profil : {report['volume']['state']}")
    browser = report["browser"]
    lines.append(
        f"- navigateurs : {', '.join(browser['installed']) or 'aucun de la famille Chromium trouvé (guide, section 1)'} ; "
        f"port {browser['port']} {'répond' if browser['listening'] else 'ne répond pas'}"
    )
    swap = report["swap"]
    lines.append(f"- espace d'échange : présent ; {swap['note']}" if swap["present"] else "- espace d'échange : absent")
    lines.append(f"- terminal interactif : {'oui' if report['terminal']['interactive'] else 'non (create, open, browser et close le demandent)'}")
    lines.append(f"- écran : {'oui' if report['display']['available'] else 'non (le navigateur en demande un)'}")
    lines.append(f"- fenêtre de confirmation : {report['confirmation_window']['tool'] or 'aucune (kdialog ou zenity à installer)'}")
    step = report["next_step"]
    lines += ["", "Prochaine étape : " + (f"`{step['command']}`" if step["command"] else "aucune commande"), f"  {step['why']}"]
    return "\n".join(lines)


# --- les commandes qui agissent --------------------------------------------------------------------


def _require_terminal(env: Environment) -> None:
    if not env.is_terminal():
        raise Refused(
            "no_terminal",
            "aucun terminal interactif : le mot de passe n'a pas de guichet ; lancez cette commande vous-même dans un terminal "
            f"(`{SETUP_COMMAND} check` se lit sans terminal)",
        )


def _absolute(text: str) -> Path:
    if not os.path.isabs(text):
        raise Refused("path_not_absolute", f"le chemin « {text} » doit être absolu (écrivez ~/… ou /…)")
    return Path(os.path.normpath(text))


def _load_profile(env: Environment) -> operator_profile.Profile:
    try:
        return operator_profile.load_profile(env.environ, euid=env.euid)
    except ToolError as exc:
        if exc.extra_server.get("missing") == "profile":
            raise Refused("profile_absent", f"aucun profil : `{SETUP_COMMAND} create` en écrit un avec le volume, `{SETUP_COMMAND} check` dit par où commencer") from None
        raise Refused("profile_invalid", f"le profil ne se lit pas : {exc.message or exc.code}") from None


def _is_temporary(env: Environment, profile: operator_profile.Profile) -> bool:
    session = _session_dir(env)
    return session is not None and os.path.realpath(profile.secrets_dir.parent) == os.path.realpath(session)


def _session_dir(env: Environment) -> Path | None:
    runtime = env.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime or not os.path.isabs(runtime):
        return None
    return Path(runtime) / SESSION_DIR_NAME


def _verdict(env: Environment, mount_dir: Path, crypt_dir: Path | None) -> MountVerdict:
    return env.mount_checker.check(mount_dir, crypt_dir)


def _run_gocryptfs(env: Environment, argv: list[str], out: TextIO) -> int:
    out.flush()
    return env.run_interactive(argv)


def _prepare_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise Refused("path_invalid", f"« {path} » n'est pas un répertoire ordinaire")


def _ensure_parents(path: Path) -> None:
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def _write_profile(env: Environment, document: dict[str, Any]) -> Path:
    target = _profile_target(env)
    directory = target.parent
    if not directory.exists():
        directory.mkdir(mode=0o700, parents=True)
    hidden = directory / f".{target.name}.{secrets.token_hex(6)}.tmp"
    fd = os.open(hidden, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            operator_profile.load_profile({ENV_VAR: os.fspath(hidden)}, euid=env.euid)
        except ToolError as exc:
            raise Refused("profile_invalid", f"le profil écrit ne passe pas la lecture du serveur : {exc.message or exc.code}") from None
        os.replace(hidden, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(hidden)
    return target


def _profile_document(
    mount_dir: Path, crypt_dir: Path, family: str, carried: dict[str, Any] | None
) -> dict[str, Any]:
    profile_dir = mount_dir / PROJECT_DIR_NAME / "browser-profile" / family
    if carried is not None:
        document = json.loads(json.dumps(carried))
        document["secrets_dir"] = os.fspath(mount_dir)
        document["secrets_crypt_dir"] = os.fspath(crypt_dir)
        document["browser"] = {**document.get("browser", {}), "family": family, "profile_dir": os.fspath(profile_dir)}
        return document
    return {
        "schema_version": 1,
        "secrets_dir": os.fspath(mount_dir),
        "secrets_crypt_dir": os.fspath(crypt_dir),
        "allowed_origins": [],
        "confirm_each_change": [],
        "local_model_origins": [],
        "origin_policy": "strict",
        "browser": {"family": family, "profile_dir": os.fspath(profile_dir), "debug_port": 9222},
    }


def _existing_profile_to_replace(env: Environment) -> dict[str, Any] | None:
    """Le profil existant dont ``create`` peut remplacer les chemins du volume, ou ``None`` s'il n'y en a pas.

    Un profil dont le volume est utilisable (ouvert ou fermé), ou qu'on ne sait pas lire, n'est jamais touché : il
    est la configuration de sécurité de l'opérateur.
    """
    target = _profile_target(env)
    if not os.path.lexists(target):
        return None
    profile = _load_profile(env)
    state = _verdict(env, profile.secrets_dir, profile.secrets_crypt_dir).state
    if state not in (MountState.NEVER_INITIALIZED, MountState.NOT_GOCRYPTFS):
        raise Refused(
            "profile_exists",
            f"un profil existe déjà et son volume est utilisable ou à confirmer (état {state.value}) : il n'est pas remplacé ; "
            f"`{SETUP_COMMAND} check` dit quoi faire",
        )
    document: dict[str, Any] = json.loads(target.read_text())
    return document


def _announce_durable(out: TextIO, env: Environment, crypt: Path, mount: Path, new_volume: bool) -> None:
    out.write(f"Volume chiffré gocryptfs{'' if new_volume else ' existant'} : {_shown(env, crypt)}, ouvert sur {_shown(env, mount)}.\n")
    out.write(
        "gocryptfs va vous demander le mot de passe : tapez-le vous-même, l'assistant ne le lit, ne le garde ni ne le transmet"
        + (" (deux fois à la création, pour le confirmer, puis une fois à l'ouverture)" if new_volume else "")
        + ".\n"
    )
    if new_volume:
        out.write(
            "Il affichera ensuite une clé maître : notez-la sur papier ou dans un gestionnaire de mots de passe, jamais dans un fichier "
            "de ce disque ; c'est le seul recours si vous perdez le mot de passe.\n"
        )


def _announce_temporary(out: TextIO, env: Environment, session: Path) -> None:
    out.write(
        f"Séance temporaire : le volume vit dans la mémoire vive ({_shown(env, session)}), rien n'est écrit sur le disque.\n"
        "Elle est détruite à `close`, à l'arrêt de la machine ou à la fin de votre session ; sont alors perdus : les sessions des sites "
        "ouvertes dans le navigateur, le journal d'audit et les preuves.\n"
        f"Pour un volume durable : `{SETUP_COMMAND} create`.\n"
    )
    if _swap_present(env):
        out.write(
            "Cette machine a un espace d'échange : la mémoire vive peut y être écrite, et son chiffrement est non déterminé "
            "(non confirmé dans la documentation).\n"
        )
    out.write(
        "gocryptfs va vous demander un mot de passe de séance : tapez-le vous-même, l'assistant ne le lit, ne le garde ni ne le transmet "
        "(deux fois à la création, pour le confirmer, puis une fois à l'ouverture).\n"
    )


def _volume_name(options: argparse.Namespace, env: Environment) -> tuple[Path, Path]:
    default_crypt = env.home.joinpath(*DEFAULT_CRYPT)
    default_mount = env.home.joinpath(*DEFAULT_MOUNT)
    if options.crypt_dir is None and options.mount_dir is None:
        return default_crypt, default_mount
    if options.crypt_dir is not None:
        crypt = _absolute(options.crypt_dir)
        stem = crypt.name[: -len(CRYPT_SUFFIX)] if crypt.name.endswith(CRYPT_SUFFIX) else crypt.name
        mount = _absolute(options.mount_dir) if options.mount_dir is not None else env.home / "Vaults" / stem
        return crypt, mount
    mount = _absolute(options.mount_dir)
    return env.home / "Vaults" / f"{mount.name}{CRYPT_SUFFIX}", mount


def _check_mount_point(env: Environment, mount: Path) -> None:
    entries = _mount_entries(env)
    real = os.path.realpath(mount)
    if any(entry.mount_point == real for entry in entries):
        raise Refused("mount_point_busy", f"« {_shown(env, mount)} » est déjà un point de montage : `{SETUP_COMMAND} check` dit ce qui y est ouvert")
    if mount.exists() and (mount.is_symlink() or not mount.is_dir() or any(mount.iterdir())):
        raise Refused("mount_point_not_empty", f"« {_shown(env, mount)} » n'est pas un répertoire vide : gocryptfs refuse de s'y monter")


def _already_mounted(env: Environment, crypt: Path, mount: Path) -> bool:
    """Dit si ``mount`` porte déjà le volume gocryptfs de ``crypt`` : relancer ``create`` après un profil refusé le reprend."""
    real = os.path.realpath(mount)
    return any(
        entry.mount_point == real and entry.fs_type == GOCRYPTFS_TYPE and os.path.realpath(entry.source) == os.path.realpath(crypt)
        for entry in _mount_entries(env)
    )


def _mount_or_refuse(env: Environment, crypt: Path, mount: Path, out: TextIO, retry: str) -> MountVerdict:
    code = _run_gocryptfs(env, ["gocryptfs", os.fspath(crypt), os.fspath(mount)], out)
    verdict = _verdict(env, mount, crypt)
    if code != 0 or verdict.state is not MountState.MOUNTED:
        raise Refused("mount_failed", f"le volume ne s'est pas ouvert (mot de passe erroné ou volume illisible) ; {retry}")
    return verdict


def _create_project_directories(mount: Path) -> None:
    root = mount / PROJECT_DIR_NAME
    for directory in (root, root / "credentials", root / "browser-profile"):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)


def _finish_create(env: Environment, crypt: Path, mount: Path, family: str, carried: dict[str, Any] | None, out: TextIO) -> None:
    _create_project_directories(mount)
    verdict = _verdict(env, mount, crypt)
    if verdict.state is not MountState.MOUNTED or verdict.error_code is not None:
        raise Refused("mount_failed", f"le volume est ouvert mais le dossier du serveur n'y est pas utilisable ({verdict.reason})")
    target = _write_profile(env, _profile_document(mount, crypt, family, carried))
    out.write(f"Volume ouvert. Profil écrit : {_shown(env, target)}.\n")
    out.write(
        "Aucun site n'est autorisé et la politique d'origines est strict : le serveur n'agit sur aucun site tant que vous n'en avez pas ajouté à "
        "allowed_origins, ou choisi la politique discovery ou open dans origin_policy (guide, section 5).\n"
    )
    if not _browser_names(env):
        out.write("Aucun navigateur de la famille Chromium n'est installé : installez-en un (guide, section 1) avant l'étape suivante.\n")
    out.write(
        f"Étape suivante : `{SETUP_COMMAND} browser` lance le navigateur sur ce volume ; vous y ouvrez vous-même vos sessions sur les sites. "
        "Si le serveur vient d'être ajouté à votre client MCP, redémarrez le client.\n"
    )


def _create(options: argparse.Namespace, env: Environment, out: TextIO) -> None:
    _require_terminal(env)
    missing = _mechanism_missing(env)
    if missing is not None:
        raise Refused("mechanism_missing", f"gocryptfs n'est pas utilisable sur ce poste : {missing}")
    family = options.browser or _default_family(env)
    if options.temporary:
        _create_temporary(env, family, out)
        return
    crypt, mount = _volume_name(options, env)
    carried = _existing_profile_to_replace(env)
    new_volume = not (crypt / "gocryptfs.conf").is_file()
    if new_volume and crypt.exists() and (crypt.is_symlink() or not crypt.is_dir() or any(crypt.iterdir())):
        raise Refused("crypt_dir_not_empty", f"« {_shown(env, crypt)} » n'est pas un volume gocryptfs et n'est pas vide : gocryptfs n'y crée rien")
    if _already_mounted(env, crypt, mount):
        out.write(f"Ce volume est déjà ouvert sur {_shown(env, mount)} : le profil est écrit sans le rouvrir.\n")
    else:
        _check_mount_point(env, mount)
        _announce_durable(out, env, crypt, mount, new_volume)
        _ensure_parents(crypt)
        _ensure_parents(mount)
        if new_volume:
            _prepare_directory(crypt)
            if _run_gocryptfs(env, ["gocryptfs", "-init", os.fspath(crypt)], out) != 0 or not (crypt / "gocryptfs.conf").is_file():
                raise Refused("init_failed", "gocryptfs n'a pas créé le volume (mots de passe différents ou refus) : rien d'autre n'est fait")
        _prepare_directory(mount)
        _mount_or_refuse(env, crypt, mount, out, "le volume existe : relancez la même commande pour l'ouvrir et écrire le profil")
    _finish_create(env, crypt, mount, family, carried, out)


def _create_temporary(env: Environment, family: str, out: TextIO) -> None:
    session = _session_dir(env)
    if session is None:
        raise Refused("no_runtime_dir", "XDG_RUNTIME_DIR n'est pas défini : la séance temporaire n'a pas de mémoire vive où vivre ; `create` fait un volume durable")
    entries = _mount_entries(env)
    real = os.path.realpath(session.parent)
    holder = max((e for e in entries if real == e.mount_point or real.startswith(e.mount_point.rstrip(os.sep) + os.sep)), key=lambda e: len(e.mount_point), default=None)
    if holder is None or holder.fs_type not in MEMORY_TYPES:
        raise Refused("runtime_dir_not_memory", "XDG_RUNTIME_DIR n'est pas en mémoire vive : rien ne serait détruit à la fermeture, rien n'est créé ; `create` fait un volume durable")
    carried = _existing_profile_to_replace(env)
    if os.path.lexists(session):
        raise Refused("session_exists", f"une séance temporaire existe déjà : `{SETUP_COMMAND} close` la détruit")
    crypt, mount = session / "crypt", session / "mount"
    _announce_temporary(out, env, session)
    session.mkdir(mode=0o700)
    try:
        crypt.mkdir(mode=0o700)
        mount.mkdir(mode=0o700)
        if _run_gocryptfs(env, ["gocryptfs", "-init", "-q", os.fspath(crypt)], out) != 0 or not (crypt / "gocryptfs.conf").is_file():
            raise Refused("init_failed", "gocryptfs n'a pas créé le volume de séance : rien n'est gardé")
        _mount_or_refuse(env, crypt, mount, out, "la séance est détruite : relancez la commande")
        _finish_create(env, crypt, mount, family, carried, out)
    except BaseException:
        if _verdict(env, mount, crypt).state is MountState.MOUNTED:
            env.run_interactive(["fusermount3", "-u", os.fspath(mount)])
        if _verdict(env, mount, crypt).state is not MountState.MOUNTED:
            shutil.rmtree(session, ignore_errors=True)
        raise


def _open(env: Environment, out: TextIO) -> None:
    _require_terminal(env)
    profile = _load_profile(env)
    verdict = _verdict(env, profile.secrets_dir, profile.secrets_crypt_dir)
    state = verdict.state
    if state is MountState.MOUNTED:
        out.write(f"Le volume est déjà ouvert sur {_shown(env, profile.secrets_dir)}.\n")
        if verdict.error_code == "project_root_missing":
            out.write(f"Le dossier du serveur n'y est pas : `{SETUP_COMMAND} check` donne la commande qui le crée.\n")
        return
    if state is MountState.NEVER_INITIALIZED:
        raise Refused("volume_missing", f"le volume du profil n'existe pas (ou plus : une séance temporaire est détruite à son arrêt) : `{SETUP_COMMAND} create` en fait un")
    if state is MountState.CLOSED_OR_ABSENT or profile.secrets_crypt_dir is None:
        raise Refused("crypt_dir_unknown", f"le profil ne dit pas où est le volume sur le disque : `{SETUP_COMMAND} check` dit quoi faire")
    if state is not MountState.CLOSED:
        raise Refused("volume_not_openable", f"l'état du volume ({state.value}) ne permet pas de l'ouvrir : `{SETUP_COMMAND} check` dit pourquoi")
    if profile.secrets_dir.exists():
        _check_mount_point(env, profile.secrets_dir)
    _prepare_directory(profile.secrets_dir)
    out.write(f"gocryptfs va vous demander le mot de passe de {_shown(env, profile.secrets_crypt_dir)} : tapez-le vous-même.\n")
    _mount_or_refuse(env, profile.secrets_crypt_dir, profile.secrets_dir, out, f"relancez `{SETUP_COMMAND} open`")
    out.write(f"Volume ouvert sur {_shown(env, profile.secrets_dir)}. Étape suivante : `{SETUP_COMMAND} browser`.\n")


def _browser(env: Environment, out: TextIO) -> None:
    _require_terminal(env)
    profile = _load_profile(env)
    verdict = _verdict(env, profile.secrets_dir, profile.secrets_crypt_dir)
    if verdict.state is not MountState.MOUNTED or verdict.error_code is not None:
        raise Refused(
            "volume_not_mounted",
            f"le volume n'est pas ouvert (état {verdict.state.value}) : le navigateur garde ses sessions dans le volume et ne se lance pas hors de lui ; "
            f"`{SETUP_COMMAND} {'open' if verdict.state is MountState.CLOSED else 'check'}`",
        )
    if env.port_listening(profile.browser.debug_port):
        out.write(f"Un navigateur répond déjà sur le port {profile.browser.debug_port} : il n'est pas relancé.\n")
        return
    if not (env.environ.get("DISPLAY") or env.environ.get("WAYLAND_DISPLAY")):
        raise Refused("no_display", "aucun écran (DISPLAY ni WAYLAND_DISPLAY) : le navigateur n'a pas où s'afficher")
    executable = _executable(env, profile.browser.family)
    if executable is None:
        raise Refused("browser_missing", f"aucun navigateur « {profile.browser.family} » n'est installé : voir le guide, section 1")
    Path(profile.browser.profile_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    pid = env.spawn_detached([executable, f"--user-data-dir={profile.browser.profile_dir}", f"--remote-debugging-port={profile.browser.debug_port}"])
    out.write(f"Navigateur lancé (processus {pid}) sur le profil du volume, détaché de ce terminal. Ouvrez-y vous-même vos sessions sur les sites.\n")


def _close(env: Environment, out: TextIO) -> None:
    _require_terminal(env)
    profile = _load_profile(env)
    verdict = _verdict(env, profile.secrets_dir, profile.secrets_crypt_dir)
    if verdict.state is MountState.MOUNTED:
        pids = env.find_browser_pids(os.fspath(profile.browser.profile_dir))
        if pids:
            out.write("Fermeture du navigateur avant le démontage.\n")
            if not env.stop_processes(pids, STOP_TIMEOUT_S):
                raise Refused("browser_still_running", "le navigateur ne s'arrête pas : fermez sa fenêtre, puis relancez `close` ; le volume reste ouvert")
        if _run_gocryptfs(env, ["fusermount3", "-u", os.fspath(profile.secrets_dir)], out) != 0:
            raise Refused(
                "volume_busy",
                "le volume est occupé : fermez les programmes qui l'utilisent, puis relancez `close` ; un démontage forcé laisserait un programme écrire hors du chiffrement",
            )
        if _verdict(env, profile.secrets_dir, profile.secrets_crypt_dir).state is MountState.MOUNTED:
            raise Refused("unmount_failed", "le volume est toujours monté après le démontage : `check` dit ce qui y est ouvert")
        out.write(f"Volume fermé : {_shown(env, profile.secrets_dir)}.\n")
    elif verdict.state in (MountState.NOT_GOCRYPTFS, MountState.UNREADABLE):
        raise Refused("volume_not_ours", f"l'état du volume ({verdict.state.value}) n'est pas celui d'un volume gocryptfs de cet utilisateur : rien n'est démonté ; `{SETUP_COMMAND} check`")
    else:
        out.write("Le volume est déjà fermé.\n")
    if _is_temporary(env, profile):
        session = _session_dir(env)
        assert session is not None
        if os.path.lexists(session):
            shutil.rmtree(session)
        out.write("Séance temporaire détruite : plus de volume en mémoire vive.\n")


def _check(options: argparse.Namespace, env: Environment, out: TextIO) -> None:
    report = build_report(env)
    out.write((json.dumps(report, ensure_ascii=False) if options.json else render_report(report)) + "\n")


# --- la ligne de commande -------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # Le message d'argparse répète ce qui a été tapé : rien n'en sort.
        raise UsageError


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=SETUP_COMMAND, add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", parser_class=_Parser)
    check = commands.add_parser("check", add_help=False, allow_abbrev=False)
    check.add_argument("--json", action="store_true")
    create = commands.add_parser("create", add_help=False, allow_abbrev=False)
    create.add_argument("--temporary", action="store_true")
    create.add_argument("--crypt-dir")
    create.add_argument("--mount-dir")
    create.add_argument("--browser", choices=[family for family, _names in BROWSER_FAMILIES])
    for name in ("open", "browser", "close"):
        commands.add_parser(name, add_help=False, allow_abbrev=False)
    return parser


def _options(argv: Sequence[str]) -> argparse.Namespace:
    """Sans sous-commande, la commande lit le poste : c'est la première que l'on tape, et elle ne change rien."""
    options = _parser().parse_args(list(argv))
    if options.command is None:
        options = _parser().parse_args(["check"])
    if options.command == "create" and options.temporary and (options.crypt_dir or options.mount_dir):
        raise UsageError
    return options


def run(argv: Sequence[str], env: Environment, stdout: TextIO, stderr: TextIO) -> int:
    """Jouer une sous-commande ; 0 en cas de succès, 1 sur un refus (``refus <code> : <message>``), 2 sur un usage erroné."""
    try:
        options = _options(argv)
    except UsageError:
        stderr.write(USAGE + "\n")
        return 2
    try:
        if options.command == "check":
            _check(options, env, stdout)
        elif options.command == "create":
            _create(options, env, stdout)
        elif options.command == "open":
            _open(env, stdout)
        elif options.command == "browser":
            _browser(env, stdout)
        else:
            _close(env, stdout)
    except Refused as refused:
        stderr.write(f"refus {refused.code} : {refused.message}\n")
        return 1
    except OSError as failure:
        stderr.write(f"refus io_error : {failure.strerror or type(failure).__name__}\n")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    hardening.restrict_umask()
    return run(sys.argv[1:] if argv is None else argv, production_environment(), sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
