"""Lire le profil de l'opérateur, la seule configuration du serveur.

Pourquoi ce module existe :
    Quels sites sont autorisés, où vit le volume chiffré et jusqu'où le modèle peut aller sont des
    décisions de l'opérateur. Elles doivent être lues dans un seul fichier, par un seul module,
    strictement : une faute de frappe ou une clé inattendue est refusée plutôt qu'ignorée en silence,
    et rien des actions irréversibles ne peut être configuré du tout.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

from .origin import Origin, OriginEntry, OriginError, parse_origin, parse_origin_pattern
from .output import ToolError

ENV_VAR = "MCP_DIWALL_BROWSER_PROFILE"
PROFILE_DIR_NAME = "mcp-server-diwall-browser"
PROFILE_FILE_NAME = "profile.json"
ORIGIN_POLICIES = ("strict", "discovery", "open")
DEFAULT_ORIGIN_POLICY = "strict"
MAX_PROFILE_BYTES = 256 * 1024
MAX_PATH_LENGTH = 4096
REDLIST_KEYS = frozenset(
    {"payment", "deletion", "delete", "secret_rotation", "rotate_secret", "red_list", "redlist"}
)


def _absolute_path(value: str) -> str:
    if len(value) > MAX_PATH_LENGTH:
        raise ValueError("path_too_long")
    if "\x00" in value:
        raise ValueError("path_invalid")
    if value.startswith("~") or not os.path.isabs(value):
        raise ValueError("path_not_absolute")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Declarations(_Strict):
    local_model_client: StrictBool = False
    client_does_not_auto_confirm: StrictBool = False


class BrowserSettings(_Strict):
    family: Literal["chrome", "chromium", "brave"]
    profile_dir: StrictStr
    debug_port: StrictInt = Field(default=9222, ge=1024, le=65535)

    @field_validator("profile_dir")
    @classmethod
    def _path(cls, value: str) -> str:
        return _absolute_path(value)


class Limits(_Strict):
    min_action_delay_ms: StrictInt = Field(default=250, ge=0, le=60000)
    max_actions_per_session: StrictInt = Field(default=200, ge=1, le=100000)
    max_pages_per_session: StrictInt = Field(default=50, ge=1, le=100000)


class _ProfileModel(_Strict):
    schema_version: StrictInt
    secrets_dir: StrictStr
    secrets_crypt_dir: StrictStr | None = None
    allowed_origins: list[StrictStr] = Field(max_length=200)
    confirm_each_change: list[StrictStr] = Field(default_factory=list)
    local_model_origins: list[StrictStr] = Field(default_factory=list)
    declarations: Declarations = Field(default_factory=Declarations)
    browser: BrowserSettings
    limits: Limits = Field(default_factory=Limits)
    origin_policy: Literal["strict", "discovery", "open"] = DEFAULT_ORIGIN_POLICY

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported_schema_version")
        return value

    @field_validator("secrets_dir", "secrets_crypt_dir")
    @classmethod
    def _paths(cls, value: str | None) -> str | None:
        return None if value is None else _absolute_path(value)


@dataclass(frozen=True)
class Profile:
    source: Path
    schema_version: int
    secrets_dir: Path
    secrets_crypt_dir: Path | None
    allowed_origins: tuple[OriginEntry, ...]
    confirm_each_change: tuple[OriginEntry, ...]
    local_model_origins: tuple[Origin, ...]
    declarations: Declarations
    browser: BrowserSettings
    limits: Limits
    origin_policy: str = DEFAULT_ORIGIN_POLICY


@dataclass(frozen=True)
class EnvironmentDirs:
    """Les répertoires que l'environnement du processus nomme, où les navigateurs gardent leurs données par défaut."""

    home: str
    config_home: str | None


def environment_dirs(environ: Mapping[str, str] | None = None) -> EnvironmentDirs:
    """Lu dans l'environnement dans ce module seulement : aucun autre module ne le regarde."""
    env = os.environ if environ is None else environ
    return EnvironmentDirs(
        home=env.get("HOME") or os.path.expanduser("~"),
        config_home=env.get("XDG_CONFIG_HOME") or None,
    )


def standard_profile_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    config_home = env.get("XDG_CONFIG_HOME", "")
    if not config_home or not os.path.isabs(config_home):
        home = env.get("HOME") or os.path.expanduser("~")
        config_home = os.path.join(home, ".config")
    return Path(config_home, PROFILE_DIR_NAME, PROFILE_FILE_NAME)


def load_profile(environ: Mapping[str, str] | None = None, *, euid: int | None = None) -> Profile:
    """Localiser, contrôler et valider le profil ; relu à chaque appel.

    Ordre : les droits du fichier et de son répertoire (propriétaire, mode 600, répertoire 700) sont
    contrôlés sur le descripteur ouvert avant qu'un seul octet soit lu, si bien que le contenu d'un
    fichier que d'autres peuvent écrire n'est jamais lu ; puis les clés qui configureraient la liste
    rouge ; puis le schéma.
    """
    env = os.environ if environ is None else environ
    standard = standard_profile_path(env)
    designated = env.get(ENV_VAR, "")
    if designated:
        if not os.path.isabs(designated):
            _invalid(f"la variable {ENV_VAR} doit désigner un chemin absolu", "env_path_not_absolute")
        path = Path(designated)
    else:
        path = standard
    data = _read_json(path, standard, os.geteuid() if euid is None else euid)
    _reject_redlist_keys(data)
    return _validate(data, path, standard)


def add_allowed_origin(profile: Profile, origin: Origin, *, euid: int | None = None) -> None:
    """Ajouter une origine exacte à la fin de ``allowed_origins``, dans le fichier d'où ce profil a été lu.

    C'est la seule écriture du profil par le serveur, et elle n'est appelée que par la réponse de l'opérateur à la
    fenêtre de ``discovery`` : aucun outil du modèle n'y mène. Le fichier est relu avec les contrôles de droits de
    ``load_profile``, le résultat est validé par le schéma avant d'être écrit, puis écrit dans un fichier voisin en
    mode 600 et mis en place par un remplacement atomique : un échec laisse l'ancien profil entier.
    """
    standard = standard_profile_path()
    data = _read_json(profile.source, standard, os.geteuid() if euid is None else euid)
    _reject_redlist_keys(data)
    if not isinstance(data, dict) or not isinstance(data.get("allowed_origins"), list):
        _invalid("le profil doit porter une liste allowed_origins", "schema")
    if origin.canonical in data["allowed_origins"]:
        return
    updated = {**data, "allowed_origins": [*data["allowed_origins"], origin.canonical]}
    _validate(updated, profile.source, standard)
    real = Path(os.path.realpath(profile.source))
    fd, temporary = tempfile.mkstemp(prefix=".profile-", suffix=".tmp", dir=real.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, real)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    _sync_directory(real.parent)


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _how_to_provide(standard: Path) -> str:
    return (
        f"le fournir dans le fichier {standard} (mode 600, répertoire 700) "
        f"ou désigner un fichier par la variable d'environnement {ENV_VAR} (chemin absolu)"
    )


def _read_json(path: Path, standard: Path, euid: int) -> Any:
    try:
        real = Path(os.path.realpath(path))
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ToolError(
            "not_configured",
            f"profil de l'opérateur introuvable{'' if path == standard else f' ({path})'} : "
            f"{_how_to_provide(standard)}",
            extra_server={"missing": "profile"},
        ) from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _invalid(f"le profil {path} n'est pas un fichier ordinaire", "permissions")
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            _invalid(f"le profil {real} n'est pas un fichier ordinaire", "permissions")
        if info.st_uid != euid:
            _invalid(f"le profil {real} doit appartenir à l'utilisateur du serveur", "permissions")
        if info.st_mode & 0o077:
            _invalid(f"le profil {real} est lisible par d'autres : corriger avec : chmod 600 {real}", "permissions")
        parent = real.parent
        parent_info = os.stat(parent)
        if parent_info.st_uid != euid or parent_info.st_mode & 0o077:
            _invalid(
                f"le répertoire du profil {parent} n'est pas privé : corriger avec : chmod 700 {parent}",
                "permissions",
            )
        raw = os.read(fd, MAX_PROFILE_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_PROFILE_BYTES:
        _invalid("le profil est trop volumineux", "too_large")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError):
        _invalid("le profil n'est pas un document JSON valide (clés en double comprises)", "not_json")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_redlist_keys(data: Any) -> None:
    """Refuser toute clé qui ressemblerait à un réglage de la liste rouge.

    La liste rouge est fixée dans le code ; un profil qui essaie de la configurer montre un
    malentendu que l'opérateur doit entendre, pas une faute de frappe.
    """
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(key, str) and key.lower() in REDLIST_KEYS:
                    raise ToolError(
                        "redlist_in_profile",
                        f"le profil contient la clé « {key} » : la liste rouge n'est pas configurable, "
                        "retirer cette clé",
                    )
                stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)


def _validate(data: Any, path: Path, standard: Path) -> Profile:
    if not isinstance(data, dict):
        _invalid("le profil doit être un objet JSON", "not_object")
    try:
        model = _ProfileModel.model_validate(data)
    except ValidationError as exc:
        _raise_from_validation(exc, standard)
    return Profile(
        source=path,
        schema_version=model.schema_version,
        secrets_dir=Path(model.secrets_dir),
        secrets_crypt_dir=None if model.secrets_crypt_dir is None else Path(model.secrets_crypt_dir),
        allowed_origins=_origins(model.allowed_origins, "allowed_origins", parse_origin_pattern),
        confirm_each_change=_origins(model.confirm_each_change, "confirm_each_change", parse_origin_pattern),
        local_model_origins=_origins(model.local_model_origins, "local_model_origins", parse_origin),
        declarations=model.declarations,
        browser=model.browser,
        limits=model.limits,
        origin_policy=model.origin_policy,
    )


def _origins[T](texts: list[str], key: str, parse: Callable[[str], T]) -> tuple[T, ...]:
    """Analyser une liste d'origines. Seules ``allowed_origins`` et ``confirm_each_change`` admettent un joker de
    sous-domaine : ``local_model_origins`` désigne des origines exactes."""
    parsed = []
    for index, text in enumerate(texts):
        try:
            parsed.append(parse(text))
        except OriginError as error:
            _invalid(f"profil refusé : {key}[{index}] ({error.reason})", "bad_origin")
    return tuple(parsed)


def _raise_from_validation(exc: ValidationError, standard: Path) -> NoReturn:
    # Le texte pydantic par défaut cite les valeurs d'entrée ; seuls les emplacements et nos propres
    # mots de raison passent.
    errors = exc.errors(include_url=False, include_input=False)
    missing = [_location(e["loc"]) for e in errors if e["type"] == "missing"]
    if missing:
        raise ToolError(
            "not_configured",
            f"valeur obligatoire absente du profil : {', '.join(missing)} ; {_how_to_provide(standard)}",
            extra_server={"missing": missing},
        )
    first = errors[0]
    if first["type"] == "extra_forbidden":
        detail = "clé inconnue"
    elif first["type"] == "value_error" and "error" in first.get("ctx", {}):
        detail = str(first["ctx"]["error"])
    else:
        detail = first["type"]
    _invalid(f"profil refusé : {_location(first['loc'])} ({detail})", "schema")


def _location(loc: tuple[int | str, ...]) -> str:
    text = ""
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text


def _invalid(message: str, reason: str) -> NoReturn:
    raise ToolError("profile_invalid", message, extra_server={"reason": reason})
