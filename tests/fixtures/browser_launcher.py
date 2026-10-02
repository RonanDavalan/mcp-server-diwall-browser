"""Launch a throw-away Chromium-family browser for the integration tests.

Why this module exists:
    The facts the perception and the actions rely on are measured against a
    real browser. The tests start their own, headless, on a blank profile in
    a temporary directory that is deleted afterwards; they never touch the
    operator's browser nor any existing profile.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any

CANDIDATES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser")
SKIP_REASON = "aucun navigateur de la famille Chromium trouvé"


def find_browser() -> str | None:
    for name in CANDIDATES:
        path = shutil.which(name)
        if path is not None:
            return path
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fetch_version(port: int, timeout: float = 3.0) -> dict[str, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise OSError(f"status {response.status}")
        return json.loads(body)
    finally:
        connection.close()


@dataclass
class LaunchedBrowser:
    executable: str
    process: subprocess.Popen[bytes]
    port: int
    profile_dir: str
    version_info: dict[str, Any]

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        shutil.rmtree(self.profile_dir, ignore_errors=True)


def launch_browser(timeout: float = 30.0) -> LaunchedBrowser | None:
    """Start a headless browser on a free port and a fresh profile; None if none is installed."""
    executable = find_browser()
    if executable is None:
        return None
    port = free_port()
    profile_dir = tempfile.mkdtemp(prefix="diwall-test-profile-")
    arguments = [
        executable,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-dev-shm-usage",
    ]
    if os.geteuid() == 0:
        arguments.append("--no-sandbox")
    arguments.append("about:blank")
    process = subprocess.Popen(
        arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    launched = LaunchedBrowser(executable, process, port, profile_dir, {})
    deadline = time.monotonic() + timeout
    while True:
        try:
            launched.version_info = fetch_version(port)
            return launched
        except (OSError, ValueError, http.client.HTTPException):
            if process.poll() is not None or time.monotonic() > deadline:
                launched.stop()
                raise RuntimeError("the test browser did not open its debugging port") from None
            time.sleep(0.1)
