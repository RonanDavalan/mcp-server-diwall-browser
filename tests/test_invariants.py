"""Mechanical properties of the code base, replayed on every test run."""

from __future__ import annotations

import ast
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import mounted_volume, write_credential

from mcp_server_diwall_browser import credentials, hardening
from mcp_server_diwall_browser.journal import Journal
from mcp_server_diwall_browser.mount import Volume
from mcp_server_diwall_browser.origin import Origin, parse_origin
from mcp_server_diwall_browser.output import ToolError

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "mcp_server_diwall_browser"


# --- core dumps are disabled before the first secret is loaded -----------------------

Resolver = Callable[..., credentials.Secret]


def core_dumps_disabled_before_credential_open(
    resolve_impl: Resolver, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> bool:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    events: list[str] = []
    real_disable = hardening.disable_core_dumps
    real_open = os.open

    def spy_disable() -> None:
        events.append("disable_core_dumps")
        real_disable()

    def spy_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(paths.credentials) in os.fspath(path):  # type: ignore[arg-type]
            events.append("open_credential")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hardening, "disable_core_dumps", spy_disable)
    monkeypatch.setattr(os, "open", spy_open)
    try:
        resolve_impl(volume, parse_origin("https://example.invalid"), "password")
    finally:
        monkeypatch.undo()
    assert "open_credential" in events, "the scenario must reach the credential file"
    return "disable_core_dumps" in events and events.index("disable_core_dumps") < events.index("open_credential")


def resolve_that_opens_first(volume: Volume, page_origin: Origin, key: str, name: str | None = None) -> credentials.Secret:
    # Defective: touches the credential file before hardening the process.
    root = volume.require_mounted()
    fd = os.open(root / "credentials" / "service.json", os.O_RDONLY)
    os.close(fd)
    return credentials.resolve(volume, page_origin, key, name)


def test_core_dumps_are_disabled_before_a_credential_file_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert core_dumps_disabled_before_credential_open(credentials.resolve, tmp_path, monkeypatch)


def test_core_dump_ordering_check_catches_a_resolver_that_opens_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not core_dumps_disabled_before_credential_open(resolve_that_opens_first, tmp_path, monkeypatch)


# --- each security definition exists once, in its own module ---------------------------

SINGLE_DEFINITIONS = {
    "parse_origin": "origin.py",
    "origin_of_url": "origin.py",
    "split_url": "origin.py",
    "register_secret": "output.py",
    "redact_text": "output.py",
    "sanitize_url": "output.py",
    "finalize": "output.py",
    "is_sensitive": "sensitive_field.py",
    "MountChecker": "mount.py",
    "parse_mountinfo": "mount.py",
    "resolve_inside": "mount.py",
    "resolve": "credentials.py",
    "load_profile": "operator_profile.py",
}

# Standard library helpers that parse an address; only origin.py may parse one.
URL_PARSERS_RE = re.compile(r"\b(urlsplit|urlparse|urlunsplit|urlunparse|urljoin|urldefrag|parse_qs|parse_qsl)\b")


def definition_sites(package: Path) -> dict[str, list[str]]:
    sites: dict[str, list[str]] = {name: [] for name in SINGLE_DEFINITIONS}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        for name in SINGLE_DEFINITIONS:
            pattern = re.compile(rf"^\s*(?:async\s+def|def|class)\s+{name}\b", re.MULTILINE)
            sites[name].extend(relative for _ in pattern.finditer(path.read_text()))
    return sites


def url_parser_uses(package: Path) -> list[str]:
    return [
        path.relative_to(package).as_posix()
        for path in sorted(package.rglob("*.py"))
        if URL_PARSERS_RE.search(path.read_text())
    ]


def definitions_are_single(package: Path) -> bool:
    sites = definition_sites(package)
    return all(found == [SINGLE_DEFINITIONS[name]] for name, found in sites.items()) and not url_parser_uses(package)


def test_security_definitions_exist_once_in_their_module() -> None:
    assert definitions_are_single(PACKAGE_DIR), definition_sites(PACKAGE_DIR)


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("actions.py", "def sanitize_url(url):\n    return url\n"),
        ("perception.py", "def is_sensitive(**kw):\n    return False\n"),
        ("browser.py", "class MountChecker:\n    pass\n"),
        ("gate.py", "async def resolve(volume, origin, key):\n    pass\n"),
        ("tools/fill.py", "from urllib.parse import urlsplit\n"),
    ],
)
def test_definition_check_catches_a_second_copy(tmp_path: Path, module: str, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / module).parent.mkdir(parents=True, exist_ok=True)
    (copy / module).write_text(source)
    assert not definitions_are_single(copy)


# --- every write under the volume goes through the mount check ---------------------------

_READ_ONLY_FLAGS = {"O_RDONLY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"}
_WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch", "symlink_to", "hardlink_to", "rename", "replace"}
_WRITE_FUNCTIONS = {
    ("os", "mkdir"),
    ("os", "makedirs"),
    ("os", "rename"),
    ("os", "replace"),
    ("os", "symlink"),
    ("os", "link"),
    ("os", "mkfifo"),
    ("os", "mknod"),
}
_WRITE_MODULES = {"shutil", "tempfile"}
VOLUME_MODULE = "mount.py"


def _names_in(node: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(node) if isinstance(n, ast.Name)
    }


def _mode_argument(call: ast.Call, position: int) -> ast.AST | None:
    if len(call.args) > position:
        return call.args[position]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


def write_calls(source: str) -> list[str]:
    """Calls in ``source`` that can create or modify a file or directory."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            pair = (func.value.id, func.attr)
            if pair == ("os", "open"):
                flags = node.args[1] if len(node.args) > 1 else None
                names = set() if flags is None else _names_in(flags) - {"os"}
                # Flags that are not spelled out cannot be proven read-only.
                if not names or not names <= _READ_ONLY_FLAGS:
                    found.append(f"line {node.lineno}: os.open for writing")
                continue
            if pair in _WRITE_FUNCTIONS or func.value.id in _WRITE_MODULES:
                found.append(f"line {node.lineno}: {func.value.id}.{func.attr}")
                continue
        if isinstance(func, ast.Attribute) and func.attr in _WRITE_METHODS:
            found.append(f"line {node.lineno}: .{func.attr}()")
            continue
        is_open = (isinstance(func, ast.Name) and func.id == "open") or (
            isinstance(func, ast.Attribute) and func.attr == "open" and not (isinstance(func.value, ast.Name) and func.value.id == "os")
        )
        if is_open:
            mode = _mode_argument(node, 1 if isinstance(func, ast.Name) else 0)
            if mode is not None and not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
                found.append(f"line {node.lineno}: open with a computed mode")
            elif mode is not None and set(mode.value) & set("wax+"):
                found.append(f"line {node.lineno}: open for writing")
    return found


def writes_outside_the_volume_module(package: Path) -> dict[str, list[str]]:
    result = {}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        if relative == VOLUME_MODULE:
            continue
        calls = write_calls(path.read_text())
        if calls:
            result[relative] = calls
    return result


def test_no_module_but_mount_opens_a_file_for_writing() -> None:
    assert writes_outside_the_volume_module(PACKAGE_DIR) == {}


def test_volume_module_is_where_the_writes_are() -> None:
    assert write_calls((PACKAGE_DIR / VOLUME_MODULE).read_text())


@pytest.mark.parametrize(
    "source",
    [
        "import os\nfd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)\n",
        "import os\nfd = os.open(path, os.O_RDWR)\n",
        "import os\nfd = os.open(path, flags)\n",
        "with open(path, 'a') as h:\n    h.write(x)\n",
        "with open(path, mode='wb') as h:\n    pass\n",
        "with open(path, 'r+') as h:\n    pass\n",
        "path.write_text(data)\n",
        "path.write_bytes(data)\n",
        "with path.open('w') as h:\n    pass\n",
        "import os\nos.makedirs(path)\n",
        "import shutil\nshutil.copy(a, b)\n",
        "import tempfile\ntempfile.NamedTemporaryFile()\n",
    ],
)
def test_write_scan_catches_each_kind_of_write(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "evidence.py").write_text(source)
    assert writes_outside_the_volume_module(copy) == {"evidence.py": write_calls(source)}
    assert write_calls(source)


@pytest.mark.parametrize(
    "source",
    [
        "import os\nfd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)\n",
        "with open(path, 'rb') as h:\n    pass\n",
        "with open(path) as h:\n    pass\n",
        "import os\nos.write(fd, data)\n",
    ],
)
def test_write_scan_leaves_reads_alone(source: str) -> None:
    assert write_calls(source) == []


# --- files created from page or model data are private and never follow a link ------------

Writer = Callable[[Volume, bytes], None]


def journal_writer(volume: Volume, line: bytes) -> None:
    Journal(volume, lambda: None)._append(line)


def plain_open_writer(volume: Volume, line: bytes) -> None:
    # Defective: a plain append that follows links and takes the umask mode.
    root = volume.require_mounted()
    (root / "journal").mkdir(exist_ok=True)
    with open(root / "journal" / "operations.jsonl", "ab") as handle:
        handle.write(line)


def journal_files_are_private_and_unfollowed(writer: Writer, tmp_path: Path) -> bool:
    (tmp_path / "first").mkdir()
    paths, _fake, volume = mounted_volume(tmp_path / "first")
    old_umask = os.umask(0)
    try:
        try:
            writer(volume, b"{}\n")
        except Exception:  # noqa: BLE001
            return False
    finally:
        os.umask(old_umask)
    directory = paths.project / "journal"
    if (directory / "operations.jsonl").stat().st_mode & 0o077 or directory.stat().st_mode & 0o777 != 0o700:
        return False

    (tmp_path / "second").mkdir()
    paths, _fake, volume = mounted_volume(tmp_path / "second")
    (paths.project / "journal").mkdir(mode=0o700)
    decoy = paths.project / "decoy"
    decoy.write_bytes(b"")
    os.chmod(decoy, 0o600)
    (paths.project / "journal" / "operations.jsonl").symlink_to(decoy)
    try:
        writer(volume, b"{}\n")
    except (ToolError, OSError):
        pass
    return decoy.read_bytes() == b""


def test_journal_files_are_private_and_never_follow_a_link(tmp_path: Path) -> None:
    assert journal_files_are_private_and_unfollowed(journal_writer, tmp_path)


def test_file_check_catches_a_plain_append_writer(tmp_path: Path) -> None:
    assert not journal_files_are_private_and_unfollowed(plain_open_writer, tmp_path)
