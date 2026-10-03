"""The smoke test script of the product, run as an operator runs it."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures.browser_launcher import SKIP_REASON, find_browser

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_test.py"


def run(path_value: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": path_value}
    return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, env=env, timeout=180, check=False)


def test_without_a_browser_the_smoke_test_says_so_and_returns_3(tmp_path: Path) -> None:
    completed = run(str(tmp_path))
    assert completed.returncode == 3
    assert "no Chromium-family browser found" in completed.stderr and completed.stdout == ""


def test_the_smoke_test_is_executable_and_takes_no_option_that_turns_a_check_off() -> None:
    assert os.access(SCRIPT, os.X_OK)
    # The options of the script itself (the browser's own options are not the script's).
    options = re.findall(r"add_argument\(\s*\"(--[a-z-]+)\"", SCRIPT.read_text())
    assert options == ["--save-image"]


@pytest.mark.browser
def test_the_smoke_test_observes_the_demo_page_and_returns_0(tmp_path: Path) -> None:
    if find_browser() is None:
        pytest.skip(SKIP_REASON)
    image = tmp_path / "observe.png"
    completed = run(os.environ.get("PATH", ""), "--save-image", str(image))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "result: conforming" in completed.stdout
    assert re.search(r"^redacted_fields\s+1$", completed.stdout, re.MULTILINE)
    assert image.read_bytes().startswith(b"\x89PNG")
