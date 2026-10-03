from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "mcp_server_diwall_browser"


START_ORDER = ["umask", "core dumps", "logging", "dependencies", "server", "run stdio"]


def start_order(main: Callable[[], None], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the entry point does, in order, with each step replaced by a recorder."""
    from mcp_server_diwall_browser import __main__ as entry

    calls: list[str] = []

    class Server:
        def run(self, transport: str) -> None:
            calls.append(f"run {transport}")

    monkeypatch.setattr(entry.hardening, "restrict_umask", lambda: calls.append("umask") or 0o022)
    monkeypatch.setattr(entry.hardening, "disable_core_dumps", lambda: calls.append("core dumps"))
    monkeypatch.setattr(entry.output, "configure_logging", lambda: calls.append("logging"))
    monkeypatch.setattr(entry, "production_dependencies", lambda: calls.append("dependencies") or object())
    monkeypatch.setattr(entry, "create_server", lambda deps: calls.append("server") or Server())
    main()
    return calls


def test_the_entry_point_hardens_the_process_before_building_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_server_diwall_browser import __main__ as entry

    assert start_order(entry.main, monkeypatch) == START_ORDER


def test_order_check_catches_a_server_built_before_core_dumps_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_server_diwall_browser import __main__ as entry

    def late_hardening() -> None:
        # Defective: the server, which may read a secret, exists before the limit is set.
        entry.output.configure_logging()
        server = entry.create_server(entry.production_dependencies())
        entry.hardening.restrict_umask()
        entry.hardening.disable_core_dumps()
        server.run(transport="stdio")

    assert start_order(late_hardening, monkeypatch) != START_ORDER


def test_no_module_calls_print() -> None:
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
