"""Mechanical properties of the code base, replayed on every test run."""

from __future__ import annotations

import ast
import io
import os
import re
import shutil
import tokenize
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import mounted_volume, write_credential

from mcp_server_diwall_browser import credentials, hardening
from mcp_server_diwall_browser.evidence import EvidenceWriter
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
    "CdpConnection": "cdp.py",
    "InternalScript": "cdp.py",
    "parse_proc_net_tcp": "browser.py",
    "verify_loopback_listening": "browser.py",
    "check_profile_dir": "browser.py",
    "check_navigable": "browser.py",
    "TabRegistry": "browser.py",
    "Perception": "perception.py",
    "capture_masked_png": "perception.py",
    "bounded_text": "output.py",
    "origin_allowed": "origin.py",
    "split_host_port": "origin.py",
    "holds": "mount.py",
    "environment_dirs": "operator_profile.py",
    "classify": "gate.py",
    "choose_channel": "confirm.py",
    "tool": "output.py",
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
        ("server.py", "def redact_text(text):\n    return text\n"),
        ("tools/status.py", "def holds(volume, path):\n    return str(path).startswith(str(volume))\n"),
        ("tools/click.py", "def classify(ref, **kw):\n    return None\n"),
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


# --- evidence and downloaded files are private and never follow a link ----------------------

PNG = b"\x89PNG\r\n\x1a\n-test"


def evidence_writer(volume: Volume, data: bytes) -> Path:
    return EvidenceWriter(volume, today=lambda: "2026-01-02").before("op-1", data)


def plain_evidence_writer(volume: Volume, data: bytes) -> Path:
    # Defective: a plain write that follows links and takes the umask mode.
    directory = volume.require_mounted() / "evidence" / "2026-01-02" / "op-1"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "before.png"
    with open(path, "wb") as handle:
        handle.write(data)
    return path


EvidenceWriterFn = Callable[[Volume, bytes], Path]


def evidence_files_are_private_and_unfollowed(writer: EvidenceWriterFn, tmp_path: Path) -> bool:
    (tmp_path / "first").mkdir()
    paths, _fake, volume = mounted_volume(tmp_path / "first")
    old_umask = os.umask(0)
    try:
        try:
            path = writer(volume, PNG)
        except Exception:  # noqa: BLE001
            return False
    finally:
        os.umask(old_umask)
    if path.stat().st_mode & 0o077 or any(
        parent.stat().st_mode & 0o777 != 0o700 for parent in (path.parent, path.parent.parent, paths.project / "evidence")
    ):
        return False

    (tmp_path / "second").mkdir()
    paths, _fake, volume = mounted_volume(tmp_path / "second")
    directory = paths.project / "evidence" / "2026-01-02" / "op-1"
    directory.mkdir(parents=True, mode=0o700)
    for parent in (paths.project / "evidence", directory.parent, directory):
        os.chmod(parent, 0o700)
    decoy = paths.project / "decoy"
    decoy.write_bytes(b"")
    os.chmod(decoy, 0o600)
    (directory / "before.png").symlink_to(decoy)
    try:
        writer(volume, PNG)
    except (ToolError, OSError):
        pass
    return decoy.read_bytes() == b""


def test_evidence_files_are_private_and_never_follow_a_link(tmp_path: Path) -> None:
    assert evidence_files_are_private_and_unfollowed(evidence_writer, tmp_path)


def test_evidence_check_catches_a_plain_writer(tmp_path: Path) -> None:
    assert not evidence_files_are_private_and_unfollowed(plain_evidence_writer, tmp_path)


Settler = Callable[[Volume, str, str], Path]


def volume_settler(volume: Volume, source: str, wanted: str) -> Path:
    return volume.settle_file("downloads", "op-1", source=source, wanted=wanted)


def plain_settler(volume: Volume, source: str, wanted: str) -> Path:
    # Defective: renames whatever the name points to and keeps the browser's mode.
    directory = volume.require_mounted() / "downloads" / "op-1"
    target = directory / wanted
    os.rename(directory / source, target)
    return target


def downloads_are_private_and_unfollowed(settler: Settler, tmp_path: Path) -> bool:
    (tmp_path / "first").mkdir()
    paths, _fake, volume = mounted_volume(tmp_path / "first")
    directory = volume.ensure_dir("downloads", "op-1")
    # The browser writes the file with its own mode.
    fd = os.open(directory / "guid-1", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    os.close(fd)
    os.chmod(directory / "guid-1", 0o644)
    try:
        path = settler(volume, "guid-1", "invoice.pdf")
    except Exception:  # noqa: BLE001
        return False
    if path.stat().st_mode & 0o077 or directory.stat().st_mode & 0o777 != 0o700:
        return False

    (tmp_path / "second").mkdir()
    paths, _fake, volume = mounted_volume(tmp_path / "second")
    directory = volume.ensure_dir("downloads", "op-1")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"operator file")
    os.chmod(outside, 0o644)
    (directory / "guid-2").symlink_to(outside)
    try:
        settler(volume, "guid-2", "invoice.pdf")
    except (ToolError, OSError):
        pass
    return outside.stat().st_mode & 0o777 == 0o644 and not (directory / "invoice.pdf").exists()


def test_downloaded_files_are_private_and_never_follow_a_link(tmp_path: Path) -> None:
    assert downloads_are_private_and_unfollowed(volume_settler, tmp_path)


def test_download_check_catches_a_plain_rename(tmp_path: Path) -> None:
    assert not downloads_are_private_and_unfollowed(plain_settler, tmp_path)


# --- page scripts come from the fixed table only, never formatted ----------------------------

CDP_MODULE = "cdp.py"
# The parameters that carry script text; only the CDP client may write them.
SCRIPT_FIELD_STRINGS = ("functionDeclaration", '"expression"', "'expression'")
# Calls whose string arguments reach the browser, and the script constructor.
SCRIPT_CALLS = frozenset({"call_internal", "send", "InternalScript"})


def script_field_mentions(package: Path) -> list[str]:
    """Modules other than cdp.py that mention a parameter carrying script text."""
    found = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        if relative == CDP_MODULE:
            continue
        text = path.read_text()
        found.extend(f"{relative}: {needle}" for needle in SCRIPT_FIELD_STRINGS if needle in text)
    return found


def _is_formatting(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(value, ast.FormattedValue) for value in node.values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return bool(node.args or node.keywords)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = node.left
        return isinstance(left, ast.JoinedStr) or (isinstance(left, ast.Constant) and isinstance(left.value, str))
    return False


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _bound_names(target: ast.expr) -> list[str]:
    """Names an assignment binds; an attribute or an item of an object binds none."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _bound_names(element)]
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    return []


def formatted_script_strings(source: str, calls: frozenset[str] = SCRIPT_CALLS) -> list[str]:
    """Strings formed with a value by f-string, .format( or % that reach a script call.

    The arguments of ``calls`` (by default call_internal, send and
    InternalScript) are searched, and so are the values assigned to a name
    those arguments use.
    """
    tree = ast.parse(source)
    assigned: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            targets = [node.target]
        for target in targets:
            for name in _bound_names(target):
                assigned.setdefault(name, []).append(node.value)  # type: ignore[union-attr]
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node.func) in calls):
            continue
        pending: list[ast.AST] = [*node.args, *(keyword.value for keyword in node.keywords)]
        seen: set[str] = set()
        while pending:
            for sub in ast.walk(pending.pop()):
                if _is_formatting(sub):
                    found.append(f"line {sub.lineno}: formatted string in {_call_name(node.func)}()")
                elif isinstance(sub, ast.Name) and sub.id in assigned and sub.id not in seen:
                    seen.add(sub.id)
                    pending.extend(assigned[sub.id])
    return found


def scripts_are_never_formatted(package: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for mention in script_field_mentions(package):
        result.setdefault(mention.split(":")[0], []).append(mention)
    for path in sorted(package.rglob("*.py")):
        found = formatted_script_strings(path.read_text())
        if found:
            result.setdefault(path.relative_to(package).as_posix(), []).extend(found)
    return result


def test_page_scripts_are_written_in_cdp_py_only_and_never_formatted() -> None:
    assert scripts_are_never_formatted(PACKAGE_DIR) == {}


def test_the_script_parameters_are_written_in_cdp_py() -> None:
    text = (PACKAGE_DIR / CDP_MODULE).read_text()
    assert "functionDeclaration" in text and '"expression"' in text


@pytest.mark.parametrize(
    ("module", "source"),
    [
        (
            "actions.py",
            "async def focus(conn, sid, selector):\n"
            "    await conn.call_internal(f'focus {selector}', session_id=sid)\n",
        ),
        (
            "actions.py",
            "from .cdp import InternalScript\n"
            "def build(selector):\n"
            "    return InternalScript('evaluate', f\"document.querySelector('{selector}')\")\n",
        ),
        (
            "actions.py",
            "from .cdp import InternalScript\n"
            "def build(selector):\n"
            "    text = \"document.querySelector('{}')\".format(selector)\n"
            "    return InternalScript('evaluate', text)\n",
        ),
        (
            "perception.py",
            "from .cdp import InternalScript\n"
            "def build(selector):\n"
            "    return InternalScript(kind='evaluate', text=\"document.title = '%s'\" % selector)\n",
        ),
        (
            "perception.py",
            "async def title(conn, sid, value):\n"
            "    await conn.send('Page.navigate', {'url': 'https://example.test/?q=%s' % value}, session_id=sid)\n",
        ),
        (
            "tools/evaluate.py",
            "async def run(conn, sid, code):\n"
            "    await conn.send('Runtime.evaluate', {\"expression\": code}, session_id=sid)\n",
        ),
        (
            "tools/call.py",
            "async def run(conn, sid, code, oid):\n"
            "    await conn.send('Runtime.callFunctionOn', {'functionDeclaration': code, 'objectId': oid})\n",
        ),
    ],
)
def test_script_check_catches_a_formatted_or_foreign_script(tmp_path: Path, module: str, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / module).parent.mkdir(parents=True, exist_ok=True)
    (copy / module).write_text(source)
    assert module in scripts_are_never_formatted(copy)


@pytest.mark.parametrize(
    "source",
    [
        "async def f(conn, sid, oid, value):\n"
        "    await conn.call_internal('focus', session_id=sid, object_id=oid, arguments=[{'value': value}])\n",
        "MESSAGE = f'{1}'\nasync def f(conn):\n    await conn.send('Browser.getVersion')\n",
        "from .cdp import InternalScript\nFOCUS = InternalScript('call_function_on', 'function () { this.focus(); }')\n",
    ],
)
def test_script_check_leaves_plain_scripts_and_protocol_arguments_alone(source: str) -> None:
    assert formatted_script_strings(source) == []


# The calls by which the actions reach the page: page reads, gestures and the script table.
ACTION_CALLS = SCRIPT_CALLS | {"read", "_read", "run_script", "_script", "_gesture"}
ACTIONS_MODULE = "actions.py"


def script_strings_in_actions(source: str) -> list[str]:
    """In the actions: no script parameter, no script method, no string formed for a page call."""
    found = [needle for needle in SCRIPT_FIELD_STRINGS if needle in source]
    found.extend(f"method {text}" for text in _string_literals(source) if text.startswith("Runtime."))
    found.extend(formatted_script_strings(source, ACTION_CALLS))
    return found


def test_the_actions_form_no_script_string() -> None:
    assert script_strings_in_actions((PACKAGE_DIR / ACTIONS_MODULE).read_text()) == []


@pytest.mark.parametrize(
    "addition",
    [
        "async def bad(self, tab, mark):\n"
        "    return await self._read(tab, 'Runtime.evaluate', {'expression': f'document.getElementById({mark})'})\n",
        "async def bad(self, tab, name):\n    return await self._script(tab, f'select_{name}')\n",
        "async def bad(self, tab, key):\n    return await self._context.registry.run_script(tab, 'focus_%s' % key)\n",
        "async def bad(self, run, tab, text):\n"
        "    await self._gesture(run, tab, [('Input.insertText', {'text': '{}'.format(text)})])\n",
    ],
)
def test_actions_script_check_catches_a_formed_script(addition: str) -> None:
    source = (PACKAGE_DIR / ACTIONS_MODULE).read_text() + "\n\n" + addition
    assert script_strings_in_actions(source) != []


# --- the WebSocket and the unchecked transport stay inside cdp.py ----------------------------

_PRIVATE_TRANSPORT_RE = re.compile(r"\b(websockets|_exchange)\b")


def transport_uses_outside_cdp(package: Path) -> list[str]:
    return [
        path.relative_to(package).as_posix()
        for path in sorted(package.rglob("*.py"))
        if path.relative_to(package).as_posix() != CDP_MODULE and _PRIVATE_TRANSPORT_RE.search(path.read_text())
    ]


def test_only_cdp_py_reaches_the_browser_socket() -> None:
    assert transport_uses_outside_cdp(PACKAGE_DIR) == []


@pytest.mark.parametrize(
    "source",
    [
        "from websockets.asyncio.client import connect\n",
        "import websockets\n",
        "async def f(conn):\n    await conn._exchange('Target.getTargets', {}, None, 5)\n",
    ],
)
def test_transport_check_catches_a_second_way_to_the_browser(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "perception.py").write_text(source)
    assert transport_uses_outside_cdp(copy) == ["perception.py"]


# --- no switch turns a check off: no environment read outside its module, no option, no flag ---

ENVIRONMENT_MODULES = {"operator_profile.py", "confirm.py"}
_ENVIRONMENT_RE = re.compile(r"\bos\.environ\b|\bgetenv\b|\bputenv\b")
_COMMAND_LINE_RE = re.compile(r"\bsys\.argv\b|\bargparse\b|\boptparse\b|\bgetopt\b|\bclick\.option\b")
_FLAG_RE = re.compile(r"(?i)^(testing|test_mode|is_test|under_test|unsafe|insecure|skip_checks?|disable_checks?|no_checks?|bypass\w*)$")


def _identifiers(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return names


def switches(package: Path) -> list[str]:
    """Ways a check could be turned off from outside: environment, command line, a test flag."""
    found: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        text = path.read_text()
        tree = ast.parse(text)
        if relative not in ENVIRONMENT_MODULES and _ENVIRONMENT_RE.search(text):
            found.append(f"{relative}: environment")
        if _COMMAND_LINE_RE.search(text):
            found.append(f"{relative}: command line")
        # The confirmation window's own options (--no-label, --no-markup) are its tool's, not the server's.
        if relative != CONFIRM_MODULE_NAME and any(text_.startswith("--no-") for text_ in _string_literals(text)):
            found.append(f"{relative}: --no- option")
        found.extend(f"{relative}: flag {name}" for name in _identifiers(tree) if _FLAG_RE.match(name))
    return found


CONFIRM_MODULE_NAME = "confirm.py"


def test_no_environment_option_or_flag_turns_a_check_off() -> None:
    assert switches(PACKAGE_DIR) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os\nGATE = os.environ.get('DIWALL_NO_GATE')\n",
        "import os\nDEBUG = os.getenv('DIWALL_DEBUG')\n",
        "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--no-confirm')\n",
        "import sys\nFAST = '--fast' in sys.argv\n",
        "OPTIONS = ['--no-gate']\n",
        "TESTING = True\n",
        "def build(skip_checks=False):\n    return skip_checks\n",
    ],
)
def test_switch_check_catches_an_environment_option_or_flag(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "server.py").write_text(source)
    assert {entry.split(":")[0] for entry in switches(copy)} == {"server.py"}


# --- the server never starts a browser: the one process it starts is the confirmation window ---

CONFIRM_MODULE = "confirm.py"
_PROCESS_START_RE = re.compile(
    r"\b(subprocess|Popen|create_subprocess_exec|create_subprocess_shell|os\.system|os\.exec\w*|os\.spawn\w*|os\.fork|pty)\b"
)


def process_starts_outside_confirm(package: Path) -> list[str]:
    return [
        path.relative_to(package).as_posix()
        for path in sorted(package.rglob("*.py"))
        if path.relative_to(package).as_posix() != CONFIRM_MODULE and _PROCESS_START_RE.search(path.read_text())
    ]


def test_only_the_confirmation_module_starts_a_process() -> None:
    assert process_starts_outside_confirm(PACKAGE_DIR) == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['google-chrome', '--remote-debugging-port=9222'])\n",
        "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('chromium')\n",
        "import os\nos.system('chromium')\n",
    ],
)
def test_process_check_catches_a_module_that_starts_a_browser(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "perception.py").write_text(source)
    assert process_starts_outside_confirm(copy) == ["perception.py"]


# --- the server listens on no port and relays nothing -----------------------------------------

# ``socket`` itself is imported legitimately (the host name, the IPv6 support
# probe); what is forbidden is a server module or a call that listens or binds.
_LISTENING_MODULES = {"socketserver", "http.server", "uvicorn", "starlette"}
_LISTENING_CALLS = {"bind", "listen", "start_server", "start_unix_server", "serve_forever", "create_datagram_endpoint"}


def listening_code(package: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(package.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _LISTENING_CALLS:
                names = [node.func.attr]
            if any(name.split(".")[0] in _LISTENING_MODULES or name in _LISTENING_CALLS or name in _LISTENING_MODULES for name in names):
                found.append(f"{path.relative_to(package).as_posix()}: {names[0]}")
    return found


def test_no_module_listens_on_a_port_or_relays_one() -> None:
    assert listening_code(PACKAGE_DIR) == []


@pytest.mark.parametrize(
    "source",
    [
        "import socket\ns = socket.socket()\ns.bind(('127.0.0.1', 9333))\n",
        "from http.server import HTTPServer\n",
        "import asyncio\nasync def f():\n    await asyncio.start_server(None, '127.0.0.1', 9333)\n",
        "def f(sock):\n    sock.listen(1)\n",
    ],
)
def test_listening_check_catches_a_server_or_a_relay(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "perception.py").write_text(source)
    assert [entry.split(":")[0] for entry in listening_code(copy)] == ["perception.py"]


# --- perception asks the one predicate whether a field is sensitive ----------------------------

PERCEPTION_MODULE = "perception.py"
# Spellings that would mean a second detection of sensitive fields; they are
# looked for anywhere in the file, comments included.
DETECTION_SPELLINGS = ("password", "passwd", "autocomplete", "cc-", "one-time-code", "current-password", "new-password")


def _string_literals(source: str) -> list[str]:
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def second_detection(source: str) -> list[str]:
    """Detection knowledge found in the perception source: words of the predicate, or a call to anything but it."""
    from mcp_server_diwall_browser import sensitive_field

    found = [f"spelling {word!r}" for word in DETECTION_SPELLINGS if word in source.lower()]
    words = sensitive_field.SENSITIVE_WORDS
    substrings = (*sensitive_field.SENSITIVE_SUBSTRINGS, *sensitive_field.SENSITIVE_AUTOCOMPLETE)
    for literal in _string_literals(source):
        lowered = literal.lower()
        tokens = set(re.split(r"[^a-z0-9]+", lowered))
        found += [f"literal {literal!r}: word {w!r}" for w in sorted(words & tokens)]
        found += [f"literal {literal!r}: {s!r}" for s in substrings if s in lowered]
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
    }
    if "is_sensitive" not in calls:
        found.append("no call to is_sensitive")
    return found


def test_perception_decides_sensitivity_through_the_predicate_only() -> None:
    assert second_detection((PACKAGE_DIR / PERCEPTION_MODULE).read_text()) == []


@pytest.mark.parametrize(
    "addition",
    [
        "\ndef _also_sensitive(node):\n    return node.attributes.get('type') == 'password'\n",
        "\ndef _card(node):\n    return 'cc-number' in node.attributes.get('autocomplete', '')\n",
        "\n_WORDS = ('otp', 'pin')\n",
        "\n# a comment that says password\n",
    ],
)
def test_detection_check_catches_a_second_predicate(addition: str) -> None:
    source = (PACKAGE_DIR / PERCEPTION_MODULE).read_text() + addition
    assert second_detection(source)


def test_detection_check_catches_a_module_that_never_asks_the_predicate() -> None:
    source = (PACKAGE_DIR / PERCEPTION_MODULE).read_text().replace("is_sensitive(", "is_plain(")
    assert "no call to is_sensitive" in second_detection(source)


# --- the repository is written in English ---------------------------------------------------

REPOSITORY = PACKAGE_DIR.parent.parent
FRENCH_LETTERS = re.compile(r"[éèêëàâçùûîïôœæÉÈÊËÀÂÇÙÛÎÏÔŒÆ«»]")
FRENCH_WORDS = frozenset({"le", "la", "les", "des", "une", "est", "pour", "dans", "avec", "pas", "qui", "sur", "aux", "du", "cette", "sont"})
_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def _looks_french(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.lower()))
    return bool(FRENCH_LETTERS.search(text)) or len(words & FRENCH_WORDS) >= 2


def _comments_and_docstrings(source: str) -> list[tuple[int, str]]:
    found = [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                found.append((getattr(node, "lineno", 1), docstring))
    return found


def french_prose(root: Path) -> list[str]:
    """Comments, docstrings and documentation prose written in French.

    Quoted code and test data (between backticks, or in string literals)
    may hold French: page text, operator messages, the confirmation text.
    """
    hits = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts:
            continue
        for line, text in _comments_and_docstrings(path.read_text()):
            if _looks_french(text):
                hits.append(f"{path.relative_to(root)}:{line}")
    for path in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        if not path.exists():
            continue
        prose = _CODE_SPAN_RE.sub(" ", path.read_text())
        for number, line in enumerate(prose.splitlines(), start=1):
            if _looks_french(line):
                hits.append(f"{path.relative_to(root)}:{number}")
    return hits


def test_comments_docstrings_and_documentation_are_in_english() -> None:
    assert french_prose(REPOSITORY) == []


@pytest.mark.parametrize(
    ("relative", "text"),
    [
        ("src/mcp_server_diwall_browser/gate.py", "# Vérifie la liste rouge avant le clic\nX = 1\n"),
        ("src/mcp_server_diwall_browser/gate.py", '"""Le module qui garde la porte pour les actions."""\n'),
        ("docs/guide.md", "# Guide\n\nLe serveur est lancé par l'opérateur.\n"),
    ],
)
def test_language_check_catches_french_prose(tmp_path: Path, relative: str, text: str) -> None:
    copy = tmp_path / "repo"
    (copy / "src").mkdir(parents=True)
    shutil.copytree(PACKAGE_DIR, copy / "src" / "mcp_server_diwall_browser")
    shutil.copytree(REPOSITORY / "docs", copy / "docs")
    shutil.copy(REPOSITORY / "README.md", copy / "README.md")
    assert french_prose(copy) == []
    target = copy / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    assert french_prose(copy) != []


# --- no HTTP server stack in the package ------------------------------------------------------

# The server speaks over standard input and output and opens no port; the MCP
# library brings an HTTP stack with it, which the package never imports.
HTTP_STACK = frozenset({"starlette", "uvicorn", "sse_starlette"})


def http_stack_imports(package: Path) -> list[str]:
    found = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            elif isinstance(node, ast.Call) and _call_name(node.func) in ("import_module", "__import__"):
                names = [arg.value for arg in node.args[:1] if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            found += [f"{relative}:{node.lineno}: {name}" for name in names if name.split(".")[0] in HTTP_STACK]
    return found


def test_no_module_imports_an_http_server_stack() -> None:
    assert http_stack_imports(PACKAGE_DIR) == []


@pytest.mark.parametrize(
    "source",
    [
        "from starlette.applications import Starlette\n",
        "import uvicorn\n",
        "import sse_starlette.sse as sse\n",
        "import importlib\nserver = importlib.import_module('uvicorn')\n",
    ],
)
def test_http_stack_check_catches_an_import(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "server.py").write_text(source)
    assert http_stack_imports(copy) != []


# --- a renamed helper is used under its public name only ---------------------------------------

RENAMED = {"_origin_label": "origin_label"}


def old_names_in_use(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "test_invariants.py" or ".venv" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
            if isinstance(node, ast.alias):
                name = node.name
            if name in RENAMED:
                found.append(f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}: {name}")
    return found


def test_the_origin_label_helper_is_only_used_by_its_public_name() -> None:
    assert old_names_in_use(REPOSITORY / "src") == []
    assert old_names_in_use(REPOSITORY / "tests") == []


def test_rename_check_catches_the_old_name(tmp_path: Path) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "perception_old.py").write_text("from .browser import _origin_label\n")
    assert old_names_in_use(copy) != []


# --- no specification label or project history in the code ------------------------------------

LABEL_RE = re.compile(r"\b(?:SEC|COF|PER)-\d+|\bINV-\d+|\b[PR]-\d+\b|\bD\d{1,2}\b|\bP[1-6]\b")
HISTORY_WORDS = frozenset({"chantier", "jalon", "axe", "tranche", "volet", "lot"})


def labels_in_code(root: Path) -> list[str]:
    """Labels of the specification or words of project history in comments, docstrings and test names."""
    hits = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts:
            continue
        source = path.read_text()
        texts = _comments_and_docstrings(source)
        texts += [
            (node.lineno, node.name)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        ]
        for line, text in texts:
            words = set(re.findall(r"[a-z0-9]+", text.lower()))
            sessions = {f"p{number}" for number in range(1, 7)}
            if LABEL_RE.search(text) or words & HISTORY_WORDS or (text.startswith("test_") and words & sessions):
                hits.append(f"{path.relative_to(root)}:{line}")
    return hits


def test_no_specification_label_in_comments_docstrings_or_test_names() -> None:
    assert labels_in_code(REPOSITORY / "src") == []
    assert labels_in_code(REPOSITORY / "tests") == []


@pytest.mark.parametrize(
    "source",
    [
        "# required by SEC-12\nX = 1\n",
        '"""Implements INV-4."""\n',
        "def test_gate_for_p4():\n    pass\n",
        "# first jalon of the gate\nX = 1\n",
    ],
)
def test_label_check_catches_a_label(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "gate.py").write_text(source)
    assert labels_in_code(copy) != []


# --- every registered tool calls the single exit point ---------------------------------------------


def test_every_tool_of_the_production_server_goes_through_the_exit_point() -> None:
    from mcp_server_diwall_browser.server import create_server, production_dependencies
    from test_server import undecorated

    assert undecorated(create_server(production_dependencies())) == []


def test_exit_point_invariant_catches_a_tool_added_without_it() -> None:
    from mcp_server_diwall_browser.server import create_server, production_dependencies
    from test_server import undecorated

    server = create_server(production_dependencies())

    def stray() -> str:
        """A tool written outside the exit point."""
        return "{}"

    server.add_tool(stray, name="stray")
    assert undecorated(server) == ["stray"]


# --- nothing but the protocol on standard output ------------------------------------------------------

_STDOUT_RE = re.compile(r"\bsys\.(?:stdout|__stdout__)\b|\bos\.write\(\s*1\b|\bprint\(")


def standard_output_writes(package: Path) -> list[str]:
    """Places that would write on standard output, which carries the protocol only."""
    found = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                found.append(f"{relative}:{node.lineno}: print")
        found.extend(f"{relative}: {match.group(0)}" for match in _STDOUT_RE.finditer(path.read_text()) if match.group(0) != "print(")
    return found


def test_nothing_writes_on_standard_output_but_the_protocol() -> None:
    assert standard_output_writes(PACKAGE_DIR) == []


@pytest.mark.parametrize(
    "source",
    [
        "print('ready')\n",
        "import sys\nsys.stdout.write('ready')\n",
        "import os\nos.write(1, b'ready')\n",
        "import sys\nsys.__stdout__.flush()\n",
    ],
)
def test_output_check_catches_a_write_on_standard_output(tmp_path: Path, source: str) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / "server.py").write_text(source)
    assert [entry.split(":")[0] for entry in standard_output_writes(copy)] != []


# --- no DevTools method but the allowed ones, page scripts in the client only ---------------------------

# The domains of the DevTools protocol: a literal "Domain.name" with one of them names a method or an event.
CDP_DOMAINS = (
    "Accessibility", "Animation", "Audits", "Autofill", "BackgroundService", "Browser", "CacheStorage", "Cast",
    "Console", "CSS", "Database", "Debugger", "DeviceAccess", "DeviceOrientation", "DOM", "DOMDebugger",
    "DOMSnapshot", "DOMStorage", "Emulation", "EventBreakpoints", "Extensions", "FedCm", "Fetch", "FileSystem",
    "HeadlessExperimental", "HeapProfiler", "IndexedDB", "Input", "Inspector", "IO", "LayerTree", "Log", "Media",
    "Memory", "Network", "Overlay", "Page", "Performance", "PerformanceTimeline", "Preload", "Profiler", "PWA",
    "Runtime", "Schema", "Security", "ServiceWorker", "Storage", "SystemInfo", "Target", "Tethering", "Tracing",
    "WebAudio", "WebAuthn",
)
_CDP_NAME_RE = re.compile(rf"^(?:{'|'.join(CDP_DOMAINS)})\.[a-z][A-Za-z]*$")
# Events the server listens to: names the browser sends, never methods it is asked to run.
CDP_EVENTS = frozenset(
    {
        "Browser.downloadProgress",
        "Browser.downloadWillBegin",
        "Inspector.targetCrashed",
        "Page.frameStartedLoading",
        "Page.frameStoppedLoading",
        "Page.javascriptDialogClosed",
        "Page.javascriptDialogOpening",
        "Page.loadEventFired",
        "Target.detachedFromTarget",
        "Target.targetCrashed",
        "Target.targetCreated",
        "Target.targetDestroyed",
    }
)
SCRIPT_METHODS = frozenset({"Runtime.evaluate", "Runtime.callFunctionOn"})


def devtools_names_out_of_list(package: Path, allowed: frozenset[str]) -> list[str]:
    """Method-like literals that are neither an allowed method nor a known event; script methods outside cdp.py."""
    found = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        for text in _string_literals(path.read_text()):
            if not _CDP_NAME_RE.match(text):
                continue
            if text in SCRIPT_METHODS and relative != CDP_MODULE:
                found.append(f"{relative}: {text}")
            elif text not in allowed and text not in CDP_EVENTS:
                found.append(f"{relative}: {text}")
    return found


def test_only_allowed_devtools_methods_appear_and_page_scripts_stay_in_the_client() -> None:
    from mcp_server_diwall_browser.cdp import ALLOWED_METHODS

    assert devtools_names_out_of_list(PACKAGE_DIR, ALLOWED_METHODS) == []


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("actions.py", "async def cookies(registry, tab):\n    return await registry.read(tab, 'Network.getCookies')\n"),
        ("perception.py", "METHOD = 'Runtime.evaluate'\n"),
        ("tools/observe.py", "DOCUMENT = 'DOM.getOuterHTML'\n"),
    ],
)
def test_devtools_check_catches_a_method_out_of_the_list(tmp_path: Path, module: str, source: str) -> None:
    from mcp_server_diwall_browser.cdp import ALLOWED_METHODS

    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    (copy / module).write_text(source)
    assert [entry.split(":")[0] for entry in devtools_names_out_of_list(copy, ALLOWED_METHODS)] == [module]


# --- the factory takes its dependencies from its caller only --------------------------------------------

ENVIRONMENT_NAMES = frozenset({"HOME", "XDG_CONFIG_HOME", "MCP_DIWALL_BROWSER_PROFILE", "ENV_VAR", "WAYLAND_DISPLAY", "DISPLAY"})


def environment_names_read(package: Path) -> set[str]:
    """Names looked up in the environment by the two modules allowed to read it."""
    names: set[str] = set()
    for module in ENVIRONMENT_MODULES:
        for node in ast.walk(ast.parse((package / module).read_text())):
            target: ast.expr | None = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop", "setdefault"):
                owner = node.func.value
                owner_name = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else ""
                if "env" in owner_name.lower() and node.args:
                    target = node.args[0]
            elif isinstance(node, ast.Subscript):
                owner = node.value
                owner_name = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else ""
                if "env" in owner_name.lower():
                    target = node.slice
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                names.add(target.value)
            elif isinstance(target, ast.Name):
                names.add(target.id)
            elif target is not None:
                names.add("<computed>")
    return names


def test_the_environment_is_read_for_named_variables_only() -> None:
    import inspect

    from mcp_server_diwall_browser import server

    assert environment_names_read(PACKAGE_DIR) <= ENVIRONMENT_NAMES
    assert list(inspect.signature(server.create_server).parameters) == ["deps"]
    assert switches(PACKAGE_DIR) == []


def test_environment_name_check_catches_a_variable_that_would_switch_a_check(tmp_path: Path) -> None:
    copy = tmp_path / "pkg"
    shutil.copytree(PACKAGE_DIR, copy)
    source = (copy / "confirm.py").read_text()
    (copy / "confirm.py").write_text(source + "\n\ndef skip(environ):\n    return environ.get('DIWALL_SKIP_CONFIRMATION')\n")
    assert "DIWALL_SKIP_CONFIRMATION" in environment_names_read(copy) - ENVIRONMENT_NAMES


# --- no forbidden file is tracked -------------------------------------------------------------------------

_FORBIDDEN_NAMES = {"CLAUDE.md", "AGENTS.md", "LICENSE", "SECURITY.md", "CHANGELOG.md", "profile.json"}
_FORBIDDEN_DIRECTORIES = {".claude", "credentials", "evidence", "downloads", "browser-profile"}


def forbidden_files(tracked: list[str]) -> list[str]:
    found = []
    for name in tracked:
        parts = name.split("/")
        if parts[-1] in _FORBIDDEN_NAMES or parts[-1].endswith(".jsonl") or _FORBIDDEN_DIRECTORIES & set(parts[:-1]):
            found.append(name)
    return found


def tracked_files() -> list[str]:
    import subprocess

    listed = subprocess.run(["git", "ls-files"], cwd=PACKAGE_DIR.parent.parent, capture_output=True, text=True, check=True)
    return listed.stdout.splitlines()


def test_no_forbidden_file_is_tracked() -> None:
    tracked = tracked_files()
    assert tracked and forbidden_files(tracked) == []


def test_forbidden_file_check_catches_each_kind() -> None:
    planted = [
        "CLAUDE.md",
        "docs/AGENTS.md",
        ".claude/settings.json",
        "LICENSE",
        "SECURITY.md",
        "CHANGELOG.md",
        "config/profile.json",
        "journal/operations.jsonl",
        "vault/credentials/service.json",
        "vault/evidence/2026-01-02/op/before.png",
        "vault/downloads/op/invoice.pdf",
        "vault/browser-profile/chrome/Preferences",
    ]
    assert forbidden_files(planted + ["README.md", "tests/fixtures/pages/basic.html"]) == planted
