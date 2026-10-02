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


# --- page scripts come from the fixed table only (INV-2, extended) -----------------------

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


def formatted_script_strings(source: str) -> list[str]:
    """Strings formed with a value by f-string, .format( or % that reach a script call.

    The arguments of call_internal, send and InternalScript are searched,
    and so are the values assigned to a name those arguments use.
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
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    assigned.setdefault(name.id, []).append(node.value)  # type: ignore[union-attr]
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node.func) in SCRIPT_CALLS):
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


# --- perception asks the one predicate whether a field is sensitive (INV-2) ---------------------

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
