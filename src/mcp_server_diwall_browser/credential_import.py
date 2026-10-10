"""Commande d'opérateur : reprendre le nom d'utilisateur et le mot de passe d'un ancien fichier d'identifiants, sans les montrer.

Pourquoi ce module existe :
    Un opérateur garde déjà des identifiants dans un autre format de fichier sur le répertoire
    chiffré. Les retaper ferait passer chaque valeur par un éditeur, par les yeux de l'opérateur et
    par la session du modèle qui lui a dit quoi taper. Cette commande convertit un fichier dans
    son propre processus et ne montre aucune valeur, si bien que le schéma strict que le serveur
    lit reste tel qu'il est.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn, TextIO

from . import credentials, hardening, operator_profile
from .mount import MountChecker, Volume, VolumeFileError
from .origin import DEFAULT_PORTS, Origin, OriginError, parse_origin
from .output import ToolError, redact_text, register_secret

SOURCE_LIMIT = 64 * 1024
# Ce qui passe dans le nouveau fichier, dans l'ordre où le fichier le liste.
CARRIED = ("username", "password")
# Ce que l'ancien format appelle les sites auxquels un fichier est destiné : des noms d'hôte, sans schéma, sans port.
SOURCE_ORIGINS = "origines_autorisees"
# Ce que l'ancien format additionne pour contrôler un fichier ; lu pour vérifier, jamais transporté.
CHECKSUM = "checksum"
CHECKSUM_PREFIX = "sha256:"
CHECKSUMMED = ("username", "password", "totp_cle", SOURCE_ORIGINS)
# Valeurs de l'ancien fichier qui sont tenues hors de toute sortie, transportées ou non.
UNSHOWN = ("username", "password", "totp_cle", "ntfy_topic")
# Ce que dit le volume quand il refuse une source, et le mot qu'on en imprime.
_SOURCE_REFUSALS = {"bad_mode": "source_mode", "outside_volume": "source_not_in_volume"}


class ImportRefused(Exception):
    """Un refus prévu de la conversion ; ``code`` est le mot stable imprimé après ``error:``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Dependencies:
    """Ce que la commande lit de son environnement ; les tests lui en donnent d'autres."""

    load_profile: Callable[[], operator_profile.Profile]
    mount_checker: MountChecker


def production_dependencies() -> Dependencies:
    return Dependencies(load_profile=operator_profile.load_profile, mount_checker=MountChecker())


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # Le message d'argparse répète ce qui a été tapé : seul le mot sort.
        raise ImportRefused("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="credential_import", add_help=False, allow_abbrev=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--origin", action="append", default=[])
    return parser


def _origin(text: str) -> Origin:
    try:
        return parse_origin(text)
    except OriginError:
        raise ImportRefused("origin_invalid") from None


def _hosts_as_origins(hosts: object) -> list[Origin] | None:
    """Les sites d'un ancien fichier comme origines https ; ``None`` sauf si chaque entrée est un nom d'hôte simple."""
    if not isinstance(hosts, list) or not hosts:
        return None
    found = []
    for host in hosts:
        if not isinstance(host, str):
            return None
        try:
            origin = parse_origin(f"https://{host}")
        except OriginError:
            return None
        # Un port, une majuscule ou un point final n'est pas un nom d'hôte simple de l'ancien format.
        if origin.port != DEFAULT_PORTS["https"] or origin.canonical != f"https://{host}":
            return None
        found.append(origin)
    return found


def _once(origins: Sequence[Origin]) -> list[Origin]:
    return list(dict.fromkeys(origins))


def _read_source(volume: Volume, path: str) -> dict[str, Any]:
    if not os.path.isabs(path):
        raise ImportRefused("source_invalid")
    try:
        content = volume.read_file_at(path, max_bytes=SOURCE_LIMIT)
    except VolumeFileError as exc:
        raise ImportRefused(_SOURCE_REFUSALS.get(exc.reason, "source_invalid")) from None
    except OSError:
        raise ImportRefused("source_invalid") from None
    try:
        data = credentials.loads_strict(content.data)
    except ValueError:
        raise ImportRefused("source_invalid") from None
    if not isinstance(data, dict):
        raise ImportRefused("source_invalid")
    # À partir d'ici les valeurs sont en mémoire : rien de ce qui est imprimé ne doit les porter.
    for key in UNSHOWN:
        if isinstance(data.get(key), str):
            register_secret(data[key])
    return data


def _verify_checksum(data: dict[str, Any]) -> None:
    if CHECKSUM not in data:
        return
    subset = {key: data[key] for key in CHECKSUMMED if key in data}
    expected = CHECKSUM_PREFIX + hashlib.sha256(json.dumps(subset, sort_keys=True).encode("utf-8")).hexdigest()
    claimed = data[CHECKSUM]
    if not isinstance(claimed, str) or not hmac.compare_digest(claimed.encode("utf-8"), expected.encode("utf-8")):
        raise ImportRefused("checksum_mismatch")


def _carried(data: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in CARRIED:
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ImportRefused("source_invalid")
        if value != "":
            found[key] = value
    if not found:
        raise ImportRefused("nothing_to_carry")
    return found


def _convert(argv: Sequence[str], dependencies: Dependencies) -> dict[str, Any]:
    options = _parser().parse_args(list(argv))
    if not credentials.valid_name(options.name):
        raise ImportRefused("name_invalid")
    explicit = _once([_origin(text) for text in options.origin])
    profile = dependencies.load_profile()
    volume = Volume(dependencies.mount_checker, profile.secrets_dir, profile.secrets_crypt_dir)
    volume.require_mounted()

    data = _read_source(volume, options.source)
    _verify_checksum(data)

    from_source = _hosts_as_origins(data.get(SOURCE_ORIGINS))
    if explicit:
        targets, assumed = explicit, False
    elif from_source is not None:
        targets, assumed = _once(from_source), True
    else:
        raise ImportRefused("origin_invalid")
    differs = from_source is None or {o.canonical for o in targets} != {o.canonical for o in from_source}

    carried = _carried(data)
    not_carried = sum(1 for key in data if key not in carried and key != SOURCE_ORIGINS)

    basename = f"{options.name}.json"
    document = {"allowed_origins": [origin.canonical for origin in targets], **carried}
    raw = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    credentials.validate(basename, raw)
    if credentials.credentials_directory(volume) is None:
        raise ToolError("credential_invalid", "répertoire d'identifiants « credentials » absent : le créer avec : mkdir -m 700 credentials")
    try:
        volume.publish_new(credentials.CREDENTIALS_DIR, basename, data=raw)
    except FileExistsError:
        raise ImportRefused("target_exists", basename) from None
    return {
        "written": basename,
        "origins": [origin.canonical for origin in targets],
        "carried": list(carried),
        "not_carried": not_carried,
        "origins_differ_from_source": differs,
        "origins_assumed_https": assumed,
    }


def _emit(stream: TextIO, text: str) -> None:
    # Le seul endroit où quoi que ce soit est écrit : les valeurs enregistrées en ont disparu.
    stream.write(redact_text(text) + "\n")
    stream.flush()


def run(argv: Sequence[str], dependencies: Dependencies, stdout: TextIO, stderr: TextIO) -> int:
    """Convertir un fichier ; 0 et une ligne de JSON en cas de succès, 1 et une ligne ``error: <code>`` en cas de refus."""
    try:
        # Avant que la première valeur soit lue : un plantage ne doit laisser aucun fichier core.
        hardening.disable_core_dumps()
        summary = _convert(argv, dependencies)
    except ImportRefused as refused:
        _emit(stderr, f"error: {refused.code}" + (f" {refused.detail}" if refused.detail else ""))
        return 1
    except ToolError as refused:
        _emit(stderr, f"error: {refused.code}")
        return 1
    except Exception:  # noqa: BLE001 - rien d'un échec imprévu ne doit atteindre la sortie
        _emit(stderr, "error: internal_error")
        return 1
    _emit(stdout, json.dumps(summary))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    hardening.restrict_umask()
    return run(sys.argv[1:] if argv is None else argv, production_dependencies(), sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
