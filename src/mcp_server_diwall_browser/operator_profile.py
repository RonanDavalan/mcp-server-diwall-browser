"""Read the operator's profile, the only configuration of the server.

Why this module exists:
    Which sites are allowed, where the encrypted volume lives and how far the
    model may go are the operator's decisions. They must be read from one
    file, by one module, strictly: a typo or an unexpected key is refused
    rather than silently ignored, and nothing about irreversible actions can
    be configured at all.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

from .origin import Origin, OriginError, parse_origin
from .output import ToolError

ENV_VAR = "MCP_DIWALL_BROWSER_PROFILE"
PROFILE_DIR_NAME = "mcp-server-diwall-browser"
PROFILE_FILE_NAME = "profile.json"
MAX_PROFILE_BYTES = 256 * 1024
MAX_PATH_LENGTH = 4096
REDLIST_KEYS = frozenset(
    {"payment", "deletion", "delete", "secret_rotation", "rotate_secret", "red_list", "redlist"}
)
_ORIGIN_LIST_KEYS = ("allowed_origins", "confirm_each_change", "local_model_origins")


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
    allowed_origins: tuple[Origin, ...]
    confirm_each_change: tuple[Origin, ...]
    local_model_origins: tuple[Origin, ...]
    declarations: Declarations
    browser: BrowserSettings
    limits: Limits


def standard_profile_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    config_home = env.get("XDG_CONFIG_HOME", "")
    if not config_home or not os.path.isabs(config_home):
        home = env.get("HOME") or os.path.expanduser("~")
        config_home = os.path.join(home, ".config")
    return Path(config_home, PROFILE_DIR_NAME, PROFILE_FILE_NAME)


def load_profile(environ: Mapping[str, str] | None = None, *, euid: int | None = None) -> Profile:
    """Locate, check and validate the profile; read again on every call.

    Order: the rights of the file and of its directory (owner, mode 600,
    directory 700) are checked on the open descriptor before a single byte
    is read, so the content of a file others may write is never read; then
    the keys that would configure the red list; then the schema.
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
    """Refuse any key that would look like a setting of the red list.

    The red list is fixed in code; a profile that tries to configure it
    shows a misunderstanding the operator must hear about, not a typo.
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
    origin_lists: dict[str, tuple[Origin, ...]] = {}
    for key in _ORIGIN_LIST_KEYS:
        parsed = []
        for index, text in enumerate(getattr(model, key)):
            try:
                parsed.append(parse_origin(text))
            except OriginError as error:
                _invalid(f"profil refusé : {key}[{index}] ({error.reason})", "bad_origin")
        origin_lists[key] = tuple(parsed)
    return Profile(
        source=path,
        schema_version=model.schema_version,
        secrets_dir=Path(model.secrets_dir),
        secrets_crypt_dir=None if model.secrets_crypt_dir is None else Path(model.secrets_crypt_dir),
        allowed_origins=origin_lists["allowed_origins"],
        confirm_each_change=origin_lists["confirm_each_change"],
        local_model_origins=origin_lists["local_model_origins"],
        declarations=model.declarations,
        browser=model.browser,
        limits=model.limits,
    )


def _raise_from_validation(exc: ValidationError, standard: Path) -> NoReturn:
    # The default pydantic text quotes input values; only locations and our
    # own reason words are let through.
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
