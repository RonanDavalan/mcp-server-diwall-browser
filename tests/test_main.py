from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "mcp_server_diwall_browser"


def test_entry_point_refuses_to_start_until_wired() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "mcp_server_diwall_browser"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "not wired" in completed.stderr


def test_no_module_calls_print() -> None:
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
