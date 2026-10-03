"""The server as the client starts it: a process speaking over standard input and output."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import write_profile
from fixtures.fake_cdp import FakeBrowser, FakeCdp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODULE = "mcp_server_diwall_browser"
SOCKET_RE = re.compile(r"^socket:\[(\d+)\]$")
LISTEN = "0A"


# --- reading the kernel's tables -------------------------------------------------------------------


def socket_inodes(pid: int) -> set[str]:
    """Inodes of the sockets a process holds, from its open descriptors."""
    inodes = set()
    for entry in os.listdir(f"/proc/{pid}/fd"):
        try:
            target = os.readlink(f"/proc/{pid}/fd/{entry}")
        except OSError:
            continue
        match = SOCKET_RE.match(target)
        if match:
            inodes.add(match.group(1))
    return inodes


def listening_inodes() -> set[str]:
    """Inodes of every listening TCP socket of the network namespace, IPv4 and IPv6."""
    found = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as handle:
                rows = handle.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) > 9 and fields[3] == LISTEN:
                found.add(fields[9])
    return found


def listening_sockets_of(pid: int) -> set[str]:
    # The tables cover the whole namespace: a socket is the process's by its
    # inode, never by its port.
    return socket_inodes(pid) & listening_inodes()


def core_limit(pid: int) -> tuple[str, str]:
    with open(f"/proc/{pid}/limits") as handle:
        for line in handle:
            if line.startswith("Max core file size"):
                soft, hard = line[len("Max core file size") :].split()[:2]
                return soft, hard
    raise AssertionError("no core limit line")


def child_running(module: str) -> int:
    """The process of this test that runs ``module``."""
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as handle:
                parent = int(handle.read().rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                command = handle.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if parent == me and module in command:
            return int(entry)
    raise AssertionError(f"no child process runs {module}")


async def test_the_detection_sees_a_listening_socket_of_another_program(tmp_path: Path) -> None:
    program = tmp_path / "listener.py"
    program.write_text(
        "import socket, sys, time\n"
        "s = socket.socket()\n"
        "s.bind(('127.0.0.1', 0))\n"
        "s.listen(1)\n"
        "sys.stdout.write('ready\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, str(program), stdout=asyncio.subprocess.PIPE)
    try:
        assert process.stdout is not None
        assert (await asyncio.wait_for(process.stdout.readline(), 10)).strip() == b"ready"
        assert len(listening_sockets_of(process.pid)) == 1
    finally:
        process.kill()
        await process.wait()


# --- the server process ------------------------------------------------------------------------------


@pytest.fixture
async def fake() -> AsyncIterator[FakeCdp]:
    server = FakeCdp()
    await server.start()
    server.browser = FakeBrowser(server)  # type: ignore[attr-defined]
    yield server
    await server.stop()


def server_environment(tmp_path: Path, port: int) -> dict[str, str]:
    volume = tmp_path / "vol"
    volume.mkdir(mode=0o700)
    profile = write_profile(
        tmp_path,
        secrets_dir=str(volume),
        allowed_origins=["https://example.test"],
        browser={"family": "chromium", "profile_dir": str(tmp_path / "dedicated-profile"), "debug_port": port},
    )
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    return {
        "MCP_DIWALL_BROWSER_PROFILE": str(profile),
        "HOME": str(home),
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


@asynccontextmanager
async def stdio_session(command: list[str], env: dict[str, str], errlog: Any) -> AsyncIterator[tuple[ClientSession, list[Exception]]]:
    """A client session over a process's standard streams, with every unreadable message kept."""
    received: list[Exception] = []

    async def keep(message: Any) -> None:
        if isinstance(message, Exception):
            received.append(message)

    parameters = StdioServerParameters(command=command[0], args=command[1:], env=env)
    async with stdio_client(parameters, errlog=errlog) as (read, write):
        async with ClientSession(read, write, message_handler=keep) as session:
            await session.initialize()
            yield session, received


async def test_the_process_speaks_only_the_protocol_opens_no_port_and_has_no_core(tmp_path: Path, fake: FakeCdp) -> None:
    env = server_environment(tmp_path, fake.port)
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, "-m", MODULE], env, errlog) as (session, unreadable):
            tools = (await session.list_tools()).tools
            status = await session.call_tool("status", {})
            pid = child_running(MODULE)
            sockets = listening_sockets_of(pid)
            limit = core_limit(pid)
    assert len(tools) == 14
    envelope = json.loads(status.content[0].text)
    assert envelope["ok"] is True and envelope["server"]["configured"] is True
    assert envelope["server"]["browser"]["attached"] is True
    assert unreadable == [], "the process wrote something else than the protocol on its standard output"
    assert sockets == set(), "the server process listens on a socket"
    assert limit == ("0", "0")
    runtime_dir = tmp_path / "run" / "mcp-server-diwall-browser"
    assert not runtime_dir.exists() or runtime_dir.stat().st_mode & 0o777 == 0o700


async def test_when_its_input_closes_the_process_gives_downloads_back_and_leaves_tabs_open(tmp_path: Path, fake: FakeCdp) -> None:
    env = server_environment(tmp_path, fake.port)
    browser: FakeBrowser = fake.browser  # type: ignore[attr-defined]
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, "-m", MODULE], env, errlog) as (session, _unreadable):
            await session.call_tool("open_tab", {})
            assert browser.download_behaviors == [{"behavior": "deny"}]
    for _ in range(100):
        if browser.download_behaviors[-1] == {"behavior": "default"}:
            break
        await asyncio.sleep(0.05)
    assert browser.download_behaviors == [{"behavior": "deny"}, {"behavior": "default"}]
    assert "Target.closeTarget" not in fake.methods()


async def test_protocol_check_catches_a_process_that_writes_something_else(tmp_path: Path) -> None:
    parasite = tmp_path / "parasite.py"
    parasite.write_text(
        "import sys\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "sys.stdout.write('starting the server\\n')\n"
        "sys.stdout.flush()\n"
        "FastMCP(name='parasite').run(transport='stdio')\n"
    )
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, str(parasite)], {"HOME": str(tmp_path)}, errlog) as (session, unreadable):
            await session.list_tools()
    assert unreadable != []


async def test_without_a_profile_the_process_starts_and_says_what_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "config")}
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, "-m", MODULE], env, errlog) as (session, _unreadable):
            status = await session.call_tool("status", {})
            opened = await session.call_tool("open_tab", {})
    assert json.loads(status.content[0].text)["server"]["configured"] is False
    assert json.loads(opened.content[0].text)["server"]["error"]["code"] == "not_configured"
    assert opened.isError is True
