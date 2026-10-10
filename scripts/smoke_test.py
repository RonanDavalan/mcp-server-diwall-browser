#!/usr/bin/env python3
"""Test de fumée : démarrer le serveur contre un navigateur jetable et observer une page de démonstration locale.

Pourquoi ce module existe :
    Après l'installation, un opérateur veut savoir que le serveur, le navigateur et le protocole
    fonctionnent ensemble avant de leur confier une vraie session. Ce script le prouve sur une page de
    démonstration servie en local, avec un profil de navigateur vierge et un profil d'opérateur vide qui
    lui sont propres, et ne touche rien d'autre.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY = Path(__file__).resolve().parent.parent
DEMO_DIR = REPOSITORY / "docs" / "demo"
BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser")
EXIT_OK, EXIT_DIFFERENT, EXIT_NO_BROWSER = 0, 1, 3
WAITING_STATES = {"closed", "closed_or_absent", "never_initialized"}


def find_browser() -> str | None:
    for name in BROWSERS:
        path = shutil.which(name)
        if path is not None:
            return path
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature of the base class
        pass


@contextmanager
def demo_site() -> Iterator[str]:
    """La page de démonstration, servie sur 127.0.0.1 seulement ; donne son adresse."""
    handler = functools.partial(_QuietHandler, directory=str(DEMO_DIR))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def browser_version(port: int) -> dict[str, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        return json.loads(response.read())
    finally:
        connection.close()


@contextmanager
def throw_away_browser(executable: str, profile_dir: Path) -> Iterator[tuple[int, str]]:
    """Un navigateur sans interface sur un profil vierge et un port de bouclage libre ; donne le port et sa version."""
    port = free_port()
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
        # Le navigateur refuse de tourner en root avec son bac à sable.
        arguments.append("--no-sandbox")
    arguments.append("about:blank")
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                version = browser_version(port).get("Browser", "unknown")
                break
            except (OSError, ValueError, http.client.HTTPException):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("the browser did not open its debugging port") from None
                time.sleep(0.1)
        yield port, version
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def write_operator_profile(work: Path, origin: str, profile_dir: Path, port: int) -> Path:
    """Un profil d'opérateur temporaire : mode 600 dans un répertoire 700, l'origine de la démonstration autorisée."""
    directory = work / "config"
    directory.mkdir(mode=0o700)
    secrets_dir = work / "encrypted-directory-not-mounted"
    secrets_dir.mkdir(mode=0o700)
    profile = {
        "schema_version": 1,
        "secrets_dir": str(secrets_dir),
        "allowed_origins": [origin],
        "browser": {"family": "chromium", "profile_dir": str(profile_dir), "debug_port": port},
    }
    path = directory / "profile.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(profile, handle)
    return path


def envelope_of(result: Any) -> dict[str, Any]:
    return json.loads(result.content[0].text)


async def run_server(profile: Path, home: Path, url: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes | None]:
    env = {"MCP_DIWALL_BROWSER_PROFILE": str(profile), "HOME": str(home)}
    if "XDG_RUNTIME_DIR" in os.environ:
        env["XDG_RUNTIME_DIR"] = os.environ["XDG_RUNTIME_DIR"]
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server_diwall_browser"], env=env)
    with open(home / "server-stderr.log", "w") as errlog:
        async with stdio_client(parameters, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                status = envelope_of(await session.call_tool("status", {}))
                opened = envelope_of(await session.call_tool("open_tab", {"url": url, "intent": "smoke test"}))
                image = None
                observed: dict[str, Any] = {"ok": False, "server": {}, "page_content": None}
                if opened.get("ok"):
                    result = await session.call_tool("observe", {"tab_id": opened["server"]["tab_id"]})
                    observed = envelope_of(result)
                    if len(result.content) > 1 and result.content[1].type == "image":
                        image = base64.b64decode(result.content[1].data)
    return status, opened, observed, image


def summary(status: dict[str, Any], opened: dict[str, Any], observed: dict[str, Any], image: bytes | None, origin: str) -> list[tuple[str, str, bool]]:
    """Chaque ligne du rapport, et si c'est ce que le test de fumée attend."""
    server = status.get("server", {})
    volume = server.get("encrypted_directory", {})
    observed_server = observed.get("server", {})
    counters = observed_server.get("counters", {})
    page = observed.get("page_content") or {}
    browser = server.get("browser", {})
    return [
        ("server", f"{server.get('server_version', '?')}, configured: {server.get('configured')}", server.get("configured") is True),
        ("browser", f"{browser.get('product', '?')}/{browser.get('version', '?')}, attached: {browser.get('attached')}", browser.get("attached") is True),
        (
            "encrypted directory",
            f"{volume.get('state', '?')} ({volume.get('code', 'no code')}), action expected: {volume.get('action_expected', 'none')}",
            volume.get("state") in WAITING_STATES,
        ),
        ("tab origin", str(opened.get("server", {}).get("origin")), opened.get("server", {}).get("origin") == origin),
        ("marks", str(counters.get("marks")), isinstance(counters.get("marks"), int) and counters.get("marks", 0) >= 3),
        ("image", "yes" if image else "no", image is not None and image.startswith(b"\x89PNG")),
        ("tree lines", str(counters.get("tree_lines")), isinstance(counters.get("tree_lines"), int) and counters.get("tree_lines", 0) > 0),
        ("redacted_fields", str(counters.get("redacted_fields")), counters.get("redacted_fields") == 1),
        ("password value in the result", "absent" if "not-a-real-password" not in json.dumps(observed) else "present", "not-a-real-password" not in json.dumps(observed)),
        ("elements", ", ".join(f"{e.get('mark')} {e.get('role')} \"{e.get('name')}\"" for e in page.get("elements", [])), bool(page.get("elements"))),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the server against a throw-away browser and observe a local demo page.")
    parser.add_argument("--save-image", type=Path, help="where to write the image the server published (PNG)")
    options = parser.parse_args()

    executable = find_browser()
    if executable is None:
        sys.stderr.write("smoke test: no Chromium-family browser found (looked for: " + ", ".join(BROWSERS) + ")\n")
        return EXIT_NO_BROWSER

    work = Path(tempfile.mkdtemp(prefix="diwall-smoke-"))
    os.chmod(work, 0o700)
    try:
        home = work / "home"
        home.mkdir(mode=0o700)
        browser_profile = work / "browser-profile"
        with demo_site() as url, throw_away_browser(executable, browser_profile) as (port, version):
            origin = url.split("/index.html")[0]
            profile = write_operator_profile(work, origin, browser_profile, port)
            status, opened, observed, image = asyncio.run(run_server(profile, home, url))
        lines = summary(status, opened, observed, image, origin)
        width = max(len(label) for label, _, _ in lines)
        sys.stdout.write(f"browser executable: {Path(executable).name} ({version})\n")
        for label, value, expected in lines:
            sys.stdout.write(f"{label.ljust(width)}  {value}{'' if expected else '   <- not as expected'}\n")
        if image is not None and options.save_image is not None:
            options.save_image.write_bytes(image)
            sys.stdout.write(f"image written to {options.save_image}\n")
        conforming = all(expected for _, _, expected in lines)
        sys.stdout.write("result: conforming\n" if conforming else "result: different from what was expected\n")
        return EXIT_OK if conforming else EXIT_DIFFERENT
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
