"""Layout and hygiene of the repository: names, pins, data, comments and module headers."""

from __future__ import annotations

import ast
import io
import ipaddress
import re
import subprocess
import sys
import tokenize
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "src" / "mcp_server_diwall_browser"


# --- no module of the package hides a module of the standard library -----------------------------------


def stdlib_shadows(names: set[str]) -> set[str]:
    return names & set(sys.stdlib_module_names)


def package_module_names() -> set[str]:
    names = {path.stem for path in PACKAGE_DIR.rglob("*.py") if path.stem != "__init__"}
    names |= {path.name for path in PACKAGE_DIR.rglob("*") if path.is_dir() and path.name != "__pycache__"}
    return names


def test_no_module_of_the_package_has_the_name_of_a_standard_module() -> None:
    assert stdlib_shadows(package_module_names()) == set()


def test_shadow_check_catches_secrets_and_profile() -> None:
    assert stdlib_shadows({"secrets", "profile", "gate"}) == {"secrets", "profile"}


# --- pinned dependencies, locked as tested, never optional ------------------------------------------------

PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s;]+)$")


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_versions(text: str) -> dict[str, str]:
    versions = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN_RE.match(line)
        assert match is not None, f"not an exact pin in the lock: {line}"
        versions[normalized(match["name"])] = match["version"]
    return versions


def unlocked_dependencies(pyproject: dict[str, object], lock_text: str) -> list[str]:
    project = pyproject["project"]
    assert isinstance(project, dict)
    declared = list(project["dependencies"]) + [spec for group in project.get("optional-dependencies", {}).values() for spec in group]
    locked = lock_versions(lock_text)
    problems = []
    for spec in declared:
        match = PIN_RE.match(spec)
        if match is None:
            problems.append(f"{spec}: not pinned with ==")
        elif locked.get(normalized(match["name"])) != match["version"]:
            problems.append(f"{spec}: lock has {locked.get(normalized(match['name']))}")
    return problems


def test_every_dependency_is_pinned_and_locked_at_the_same_version() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert unlocked_dependencies(pyproject, (ROOT / "requirements.lock").read_text()) == []


def test_lock_check_catches_a_loose_pin_and_a_lock_that_differs() -> None:
    pyproject = {"project": {"dependencies": ["mcp>=1.25", "pillow==12.0.0"], "optional-dependencies": {}}}
    assert len(unlocked_dependencies(pyproject, "mcp==1.25.0\npillow==11.0.0\n")) == 2


def optional_imports(source: str) -> list[int]:
    """Lines of a ``try: import … except ImportError`` that would make a protection optional."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        imports = any(isinstance(item, (ast.Import, ast.ImportFrom)) for item in node.body)
        catches = any(
            isinstance(handler.type, ast.Name) and handler.type.id in ("ImportError", "ModuleNotFoundError")
            or isinstance(handler.type, ast.Tuple) and any(isinstance(e, ast.Name) and e.id in ("ImportError", "ModuleNotFoundError") for e in handler.type.elts)
            for handler in node.handlers
        )
        if imports and catches:
            found.append(node.lineno)
    return found


def test_no_module_makes_an_import_optional() -> None:
    assert {path.name: optional_imports(path.read_text()) for path in PACKAGE_DIR.rglob("*.py") if optional_imports(path.read_text())} == {}


def test_optional_import_check_catches_a_fallback() -> None:
    assert optional_imports("try:\n    import resource\nexcept ImportError:\n    resource = None\n") == [1]
    assert optional_imports("try:\n    from PIL import Image\nexcept (ModuleNotFoundError, OSError):\n    Image = None\n") == [1]


# --- no real data in the repository --------------------------------------------------------------------------

# Names reserved for examples and tests: they can never designate a real host.
ALLOWED_HOSTS = {"example.invalid", "example.test", "localhost", "127.0.0.1", "[::1]"}
RESERVED_SUFFIXES = (".test", ".invalid")
DOCUMENTATION_NETWORKS = [ipaddress.ip_network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8", "0.0.0.0/32")]
HOME_RE = re.compile(r"~/(?!alice\b)[A-Za-z0-9_.-]+")
IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
URL_HOST_RE = re.compile(r"\b(?:https?|wss?)://(?:[^@/\s\"'<>`]+@)?(\[[^\]\s]*\]?|[^/:\s\"'<>`)\]?#]+)")
# A host name that could be real: labels separated by dots, the last one alphabetic.
HOST_NAME_RE = re.compile(r"^[^.\s]+(?:\.[^.\s]+)*\.[^\W\d_]{2,}$")


def host_is_allowed(host: str) -> bool:
    """Reserved names, loopback and documentation addresses; IP literals are judged by the address check."""
    host = host.lower().rstrip(".")
    if host in ALLOWED_HOSTS or host.endswith(RESERVED_SUFFIXES) or "{" in host:
        return True
    if host.startswith("[") or not HOST_NAME_RE.match(host):
        # An address literal, or a test vector that is no host name at all.
        return True
    return False


def real_data(text: str) -> list[str]:
    found = [f"home path {m.group(0)}" for m in HOME_RE.finditer(text)]
    for match in IPV4_RE.finditer(text):
        try:
            address = ipaddress.ip_address(match.group(1))
        except ValueError:
            continue
        if address.is_private and not address.is_loopback and not any(address in net for net in DOCUMENTATION_NETWORKS):
            found.append(f"private address {match.group(1)}")
    found += [f"address {m.group(0)}" for m in EMAIL_RE.finditer(text) if not host_is_allowed(m.group(1))]
    found += [f"host {m.group(1)}" for m in URL_HOST_RE.finditer(text) if not host_is_allowed(m.group(1))]
    return found


def tracked_text_files() -> list[Path]:
    listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    files = []
    for name in listed:
        path = ROOT / name
        if path.suffix in (".png", ".ico", ".pdf") or not path.is_file():
            continue
        files.append(path)
    return files


def test_no_tracked_file_holds_real_data() -> None:
    found = {}
    for path in tracked_text_files():
        problems = real_data(path.read_text(errors="replace"))
        if problems:
            found[path.relative_to(ROOT).as_posix()] = problems
    assert found == {}


# Built from parts, so that this file itself holds none of them.
PLANTED = [
    "profile_dir = '~/" + "bob/.config/chromium'",
    "server = '192." + "168.1.20'",
    "gateway " + "10.0.0." + "1",
    "contact: bob" + "@" + "example.org",
    "see https://" + "www.example" + ".com/page",
    "ws://" + "172.16." + "4.2:9222/devtools",
]


@pytest.mark.parametrize("text", PLANTED)
def test_real_data_check_catches_a_name_a_private_address_or_a_real_host(tmp_path: Path, text: str) -> None:
    planted = tmp_path / "planted.md"
    planted.write_text(f"An example.\n{text}\n")
    assert real_data(planted.read_text()) != []


def test_real_data_check_leaves_the_example_values_alone() -> None:
    assert real_data("alice at ~/Vaults/Main, https://shop.example.test/pay, http://127.0.0.1:9222, user@example.invalid, 203.0.113.5") == []


# --- comments and docstrings: one language, no project history, no labels -----------------------------------

FORBIDDEN_WORDS = ("chantier", "jalon", "axe", "tranche", "volet", "lot")
LABEL_RE = re.compile(r"\b(?:SEC|COF|PER|INV)-\d+|\bD\d+\b|\bR-\d|\bsession\s+\d+\b", re.IGNORECASE)
FRENCH_WORDS = {"le", "la", "les", "des", "une", "est", "pour", "avec", "dans", "qui"}


def comments_and_docstrings(source: str) -> list[tuple[int, str]]:
    found = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            found.append((token.start[0], token.string.lstrip("#").strip()))
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                found.append((first.lineno, first.value.value))
    return found


def text_problems(text: str) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ]+", text.lower())
    problems = [f"word {word}" for word in FORBIDDEN_WORDS if word in words]
    problems += [f"label {m.group(0)}" for m in LABEL_RE.finditer(text)]
    french = sorted(FRENCH_WORDS & set(words))
    if len(french) >= 2:
        problems.append(f"french ({', '.join(french)})")
    return problems


def identifiers_not_ascii(source: str) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Dict):
            names += [key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
    return [name for name in names if not name.isascii()]


def source_problems(source: str) -> list[str]:
    problems = [f"line {line}: {problem}" for line, text in comments_and_docstrings(source) for problem in text_problems(text)]
    return problems + [f"identifier {name}" for name in identifiers_not_ascii(source)]


def test_comments_and_docstrings_are_english_and_carry_no_history_nor_label() -> None:
    found = {path.relative_to(ROOT).as_posix(): source_problems(path.read_text()) for path in PACKAGE_DIR.rglob("*.py")}
    assert {name: problems for name, problems in found.items() if problems} == {}


@pytest.mark.parametrize(
    "source",
    [
        "# Ce contrôle est fait pour la page\nx = 1\n",
        '"""Check the page.\n\nNext lot: the gate."""\n',
        "# Added for SEC-12.\nx = 1\n",
        "# Decided in session 4.\nx = 1\n",
        "def f():\n    \"\"\"See D11.\"\"\"\n",
        "délai = 3\n",
        "settings = {'clé': 1}\n",
    ],
)
def test_comment_check_catches_french_history_labels_and_non_ascii_names(source: str) -> None:
    assert source_problems(source) != []


# --- each module says why it exists ---------------------------------------------------------------------------------

HEADER = "Why this module exists"
AUTHOR_OR_DATE_RE = re.compile(r"(?i)\bauthor\b|@\w+\.\w+|\b(19|20)\d\d-\d\d-\d\d\b")


def header_problems(path: Path) -> list[str]:
    source = path.read_text()
    if path.name == "__init__.py" and not source.strip():
        return []
    docstring = ast.get_docstring(ast.parse(source)) or ""
    problems = []
    if HEADER not in docstring:
        problems.append("no 'Why this module exists'")
    if AUTHOR_OR_DATE_RE.search(docstring):
        problems.append("author or date in the header")
    return problems


def test_every_module_says_why_it_exists() -> None:
    found = {path.relative_to(ROOT).as_posix(): header_problems(path) for path in PACKAGE_DIR.rglob("*.py")}
    assert {name: problems for name, problems in found.items() if problems} == {}


def test_header_check_catches_a_module_without_its_reason_or_with_an_author(tmp_path: Path) -> None:
    bare = tmp_path / "bare.py"
    bare.write_text('"""Does things."""\n')
    signed = tmp_path / "signed.py"
    signed.write_text('"""Does things.\n\nWhy this module exists:\n    Reasons.\n\nAuthor: someone, 2026-01-02\n"""\n')
    empty = tmp_path / "__init__.py"
    empty.write_text("")
    assert header_problems(bare) and header_problems(signed) and header_problems(empty) == []
