from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fixtures.fake_cdp import NO_REPLY, FakeBrowser, FakeCdp, FakeCdpError, settle, until

from mcp_server_diwall_browser import browser
from mcp_server_diwall_browser.browser import (
    BrowserAttachment,
    Listener,
    ProcNetError,
    Tab,
    TabRegistry,
    check_debugger_url,
    check_profile_dir,
    default_http_get,
    parse_proc_net_tcp,
    read_proc_net_tcp,
    verify_loopback_listening,
)
from mcp_server_diwall_browser.cdp import CdpConnection
from mcp_server_diwall_browser.output import ToolError

TCP_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"
)
TCP6_HEADER = (
    "  sl  local_address                         remote_address                        st tx_queue "
    "rx_queue tr tm->when retrnsmt   uid  timeout inode"
)
TAIL = "00000000:00000000 00:00000000 00000000  1000        0 41562 1 0000000000000000 100 0 0 10 0"

V4_LOOPBACK = "0100007F"
V4_ANY = "00000000"
V4_DOC = "057100CB"  # 203.0.113.5
V6_LOOPBACK = "00000000000000000000000001000000"
V6_MAPPED_LOOPBACK = "0000000000000000FFFF00000100007F"
V6_ANY = "0" * 32


def tcp_line(slot: int, address: str, port: int, state: str = "0A", remote: str | None = None) -> str:
    remote = remote or "0" * len(address)
    return f"{slot:4d}: {address}:{port:04X} {remote}:0000 {state} {TAIL}"


def tcp_table(*entries: tuple[str, int] | tuple[str, int, str]) -> str:
    return "\n".join([TCP_HEADER] + [tcp_line(i, *entry) for i, entry in enumerate(entries)]) + "\n"


def tcp6_table(*entries: tuple[str, int] | tuple[str, int, str]) -> str:
    return "\n".join([TCP6_HEADER] + [tcp_line(i, *entry) for i, entry in enumerate(entries)]) + "\n"


EMPTY_TCP = tcp_table()
EMPTY_TCP6 = tcp6_table()

# --- /proc/net/tcp parsing ---------------------------------------------------------------


def test_parse_reads_address_port_and_state_of_ipv4_sockets() -> None:
    listeners = parse_proc_net_tcp(tcp_table((V4_LOOPBACK, 9222), (V4_ANY, 9222), (V4_DOC, 443, "01")), ipv6=False)
    assert listeners == [
        Listener(ipaddress.IPv4Address("127.0.0.1"), 9222, "LISTEN"),
        Listener(ipaddress.IPv4Address("0.0.0.0"), 9222, "LISTEN"),
        Listener(ipaddress.IPv4Address("203.0.113.5"), 443, "ESTABLISHED"),
    ]


def test_parse_reads_ipv6_sockets() -> None:
    listeners = parse_proc_net_tcp(tcp6_table((V6_LOOPBACK, 9222), (V6_MAPPED_LOOPBACK, 9222), (V6_ANY, 9222)), ipv6=True)
    assert [str(entry.address) for entry in listeners] == ["::1", "::ffff:127.0.0.1", "::"]
    assert {entry.port for entry in listeners} == {9222}


def test_parse_reads_the_line_given_as_example() -> None:
    [entry] = parse_proc_net_tcp(f"{TCP_HEADER}\n   0: 0100007F:2406 00000000:0000 0A {TAIL}\n", ipv6=False)
    assert entry == Listener(ipaddress.IPv4Address("127.0.0.1"), 9222, "LISTEN")


@pytest.mark.parametrize(
    "text",
    [
        "garbage",
        TCP_HEADER + "\n   0: 0100007F 00000000:0000 0A " + TAIL,
        TCP_HEADER + "\n   0: 0100007F:24 00000000:0000 0A " + TAIL,
        TCP_HEADER + "\n   0: 0100007G:2406 00000000:0000 0A " + TAIL,
        TCP_HEADER + "\n   0: 0100007F:2406 00000000:0000 " ,
        TCP_HEADER + "\n   x: 0100007F:2406 00000000:0000 0A " + TAIL,
        TCP_HEADER + "\n   0: 0100007F:2406 00000000:0000 0A " + TAIL + "\n\n",
        TCP_HEADER + "\n   0: " + V6_LOOPBACK + ":2406 " + V6_ANY + ":0000 0A " + TAIL,
        "   0: 0100007F:2406 00000000:0000 0A " + TAIL,
    ],
)
def test_malformed_ipv4_table_raises(text: str) -> None:
    with pytest.raises(ProcNetError):
        parse_proc_net_tcp(text, ipv6=False)


def test_ipv4_line_in_the_ipv6_table_raises() -> None:
    with pytest.raises(ProcNetError):
        parse_proc_net_tcp(tcp_table((V4_LOOPBACK, 9222)), ipv6=True)


def test_empty_text_is_a_table_without_sockets() -> None:
    assert parse_proc_net_tcp("", ipv6=True) == []


# --- loopback verification ----------------------------------------------------------------

Verifier = Callable[..., None]


def loopback_verdict(verify: Verifier, port: int, tcp_text: str | None, tcp6_text: str | None) -> str:
    try:
        verify(port, tcp_text=tcp_text, tcp6_text=tcp6_text)
    except ToolError as exc:
        return exc.code
    return "accepted"


LOOPBACK_CASES = [
    ("only 0.0.0.0", tcp_table((V4_ANY, 9222)), EMPTY_TCP6, "debug_endpoint_not_loopback"),
    ("only 127.0.0.1", tcp_table((V4_LOOPBACK, 9222)), EMPTY_TCP6, "accepted"),
    ("127.0.0.1 and 0.0.0.0", tcp_table((V4_LOOPBACK, 9222), (V4_ANY, 9222)), EMPTY_TCP6, "debug_endpoint_not_loopback"),
    ("0.0.0.0 then 127.0.0.1", tcp_table((V4_ANY, 9222), (V4_LOOPBACK, 9222)), EMPTY_TCP6, "debug_endpoint_not_loopback"),
    ("[::]", EMPTY_TCP, tcp6_table((V6_ANY, 9222)), "debug_endpoint_not_loopback"),
    ("127.0.0.1 and [::]", tcp_table((V4_LOOPBACK, 9222)), tcp6_table((V6_ANY, 9222)), "debug_endpoint_not_loopback"),
    ("::1", EMPTY_TCP, tcp6_table((V6_LOOPBACK, 9222)), "accepted"),
    ("::ffff:127.0.0.1", EMPTY_TCP, tcp6_table((V6_MAPPED_LOOPBACK, 9222)), "accepted"),
    ("127.0.0.1 and ::1", tcp_table((V4_LOOPBACK, 9222)), tcp6_table((V6_LOOPBACK, 9222)), "accepted"),
    ("127.1.2.3", tcp_table(("0302017F", 9222)), EMPTY_TCP6, "accepted"),
    ("203.0.113.5", tcp_table((V4_DOC, 9222)), EMPTY_TCP6, "debug_endpoint_not_loopback"),
    ("other port on 0.0.0.0", tcp_table((V4_LOOPBACK, 9222), (V4_ANY, 9223), (V4_ANY, 80)), EMPTY_TCP6, "accepted"),
    (
        "connection from outside to a loopback listener",
        tcp_table((V4_LOOPBACK, 9222), (V4_DOC, 9222, "01")),
        EMPTY_TCP6,
        "accepted",
    ),
    ("nothing on the port", tcp_table((V4_LOOPBACK, 9223)), EMPTY_TCP6, "browser_unreachable"),
    ("only a connection on the port", tcp_table((V4_LOOPBACK, 9222, "01")), EMPTY_TCP6, "browser_unreachable"),
    ("tcp unreadable", None, EMPTY_TCP6, "debug_endpoint_undetermined"),
    ("tcp6 unreadable", tcp_table((V4_LOOPBACK, 9222)), None, "debug_endpoint_undetermined"),
    ("tcp malformed", "garbage", EMPTY_TCP6, "debug_endpoint_undetermined"),
    ("tcp6 malformed", tcp_table((V4_LOOPBACK, 9222)), "garbage", "debug_endpoint_undetermined"),
    ("kernel without IPv6", tcp_table((V4_LOOPBACK, 9222)), "", "accepted"),
]


@pytest.mark.parametrize(("case", "tcp_text", "tcp6_text", "expected"), LOOPBACK_CASES, ids=[c[0] for c in LOOPBACK_CASES])
def test_loopback_verification_table(case: str, tcp_text: str | None, tcp6_text: str | None, expected: str) -> None:
    assert loopback_verdict(verify_loopback_listening, 9222, tcp_text, tcp6_text) == expected


def naive_verify(port: int, *, tcp_text: str | None, tcp6_text: str | None) -> None:
    # Defective: looks for the loopback address anywhere in the table.
    if "0100007F:" not in (tcp_text or "") and "127.0.0.1" not in (tcp_text or ""):
        raise ToolError("debug_endpoint_not_loopback")


def test_loopback_check_catches_a_text_search() -> None:
    verdicts = [loopback_verdict(naive_verify, 9222, c[1], c[2]) for c in LOOPBACK_CASES]
    expected = [c[3] for c in LOOPBACK_CASES]
    assert verdicts != expected
    # The case the text search gets wrong: 0.0.0.0 next to a loopback line.
    assert loopback_verdict(naive_verify, 9222, LOOPBACK_CASES[2][1], LOOPBACK_CASES[2][2]) == "accepted"


def write_proc(directory: Path, tcp: str | None, tcp6: str | None) -> None:
    directory.mkdir(exist_ok=True)
    if tcp is not None:
        (directory / "tcp").write_text(tcp)
    if tcp6 is not None:
        (directory / "tcp6").write_text(tcp6)


def test_read_proc_net_tcp_returns_both_tables(tmp_path: Path) -> None:
    write_proc(tmp_path / "net", tcp_table((V4_LOOPBACK, 9222)), EMPTY_TCP6)
    tcp, tcp6 = read_proc_net_tcp(directory=str(tmp_path / "net"))
    assert tcp == tcp_table((V4_LOOPBACK, 9222))
    assert tcp6 == EMPTY_TCP6


def test_missing_ipv6_table_is_empty_only_on_a_kernel_without_ipv6(tmp_path: Path) -> None:
    write_proc(tmp_path / "net", tcp_table((V4_LOOPBACK, 9222)), None)
    without = read_proc_net_tcp(directory=str(tmp_path / "net"), ipv6_supported=lambda: False)
    assert without[1] == ""
    assert loopback_verdict(verify_loopback_listening, 9222, *without) == "accepted"
    with_ipv6 = read_proc_net_tcp(directory=str(tmp_path / "net"), ipv6_supported=lambda: True)
    assert with_ipv6[1] is None
    assert loopback_verdict(verify_loopback_listening, 9222, *with_ipv6) == "debug_endpoint_undetermined"


def test_unreadable_tables_give_undetermined(tmp_path: Path) -> None:
    net = tmp_path / "net"
    net.mkdir()
    (net / "tcp").mkdir()  # a directory cannot be read as a file
    (net / "tcp6").write_text(EMPTY_TCP6)
    tcp, tcp6 = read_proc_net_tcp(directory=str(net))
    assert tcp is None
    assert loopback_verdict(verify_loopback_listening, 9222, tcp, tcp6) == "debug_endpoint_undetermined"


def test_empty_or_binary_file_is_unreadable(tmp_path: Path) -> None:
    write_proc(tmp_path / "net", "", EMPTY_TCP6)
    assert read_proc_net_tcp(directory=str(tmp_path / "net"))[0] is None
    (tmp_path / "net" / "tcp").write_bytes(b"\xff\xfe")
    assert read_proc_net_tcp(directory=str(tmp_path / "net"))[0] is None


def test_real_tables_are_readable() -> None:
    tcp, tcp6 = read_proc_net_tcp()
    assert tcp is not None and tcp6 is not None
    parse_proc_net_tcp(tcp, ipv6=False)
    parse_proc_net_tcp(tcp6, ipv6=True)


# --- dedicated profile ---------------------------------------------------------------------

FAMILY_DIRS = {
    "chrome": (".config", "google-chrome"),
    "chromium": (".config", "chromium"),
    "brave": (".config", "BraveSoftware", "Brave-Browser"),
}

ProfileCheck = Callable[[str, str, str], None]


def profile_verdict(check: ProfileCheck, family: str, profile_dir: Path, home: Path) -> str:
    try:
        check(family, str(profile_dir), str(home))
    except ToolError as exc:
        return exc.code
    return "accepted"


def make_home(tmp_path: Path, family: str) -> tuple[Path, Path]:
    home = tmp_path / "home"
    default = home.joinpath(*FAMILY_DIRS[family])
    default.mkdir(parents=True)
    return home, default


def profile_cases(tmp_path: Path, family: str) -> list[tuple[str, Path, str]]:
    home, default = make_home(tmp_path, family)
    (default / "Default").mkdir()
    link = tmp_path / "link-to-default"
    link.symlink_to(default)
    dedicated = tmp_path / "vault" / "browser-profile"
    dedicated.mkdir(parents=True)
    sibling = default.parent / (default.name + "-dedicated")
    sibling.mkdir()
    return [
        ("equal", default, "profile_dir_is_default"),
        ("equal with trailing slash", Path(str(default) + "/"), "profile_dir_is_default"),
        ("inside", default / "Default", "profile_dir_is_default"),
        ("dotted path into it", default / "Default" / ".." / "Default", "profile_dir_is_default"),
        ("symbolic link to it", link, "profile_dir_is_default"),
        ("link inside a link", link / "Default", "profile_dir_is_default"),
        ("dedicated directory", dedicated, "accepted"),
        ("sibling sharing the prefix", sibling, "accepted"),
    ]


@pytest.mark.parametrize("family", sorted(FAMILY_DIRS))
def test_profile_dir_check(tmp_path: Path, family: str) -> None:
    home = tmp_path / "home"
    for case, profile_dir, expected in profile_cases(tmp_path, family):
        assert profile_verdict(check_profile_dir, family, profile_dir, home) == expected, case


def string_profile_check(family: str, profile_dir: str, home: str) -> None:
    # Defective: compares the texts without resolving links.
    default = os.path.join(home, *FAMILY_DIRS[family])
    if profile_dir == default or profile_dir.startswith(default + os.sep):
        raise ToolError("profile_dir_is_default")


def test_profile_check_catches_a_comparison_without_realpath(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cases = profile_cases(tmp_path, "chromium")
    link_case = next(c for c in cases if c[0] == "symbolic link to it")
    assert profile_verdict(string_profile_check, "chromium", link_case[1], home) == "accepted"
    assert any(profile_verdict(string_profile_check, "chromium", c[1], home) != c[2] for c in cases)


def test_profile_check_also_covers_the_xdg_configuration_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "xdg"
    default = config / "google-chrome"
    default.mkdir(parents=True)
    with pytest.raises(ToolError) as caught:
        check_profile_dir("chrome", str(default), str(home), config_home=str(config))
    assert caught.value.code == "profile_dir_is_default"
    check_profile_dir("chrome", str(default), str(home))
    check_profile_dir("chrome", str(default), str(home), config_home="relative/xdg")


def test_profile_check_never_touches_the_real_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "nowhere"))
    home, default = make_home(tmp_path, "brave")
    with pytest.raises(ToolError):
        check_profile_dir("brave", str(default), str(home))


def test_unknown_family_is_a_programming_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        check_profile_dir("firefox", str(tmp_path), str(tmp_path))


# --- announced WebSocket address ---------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("ws://127.0.0.1:9222/devtools/browser/abc", "accepted"),
        ("ws://[::1]:9222/devtools/browser/abc", "accepted"),
        ("ws://localhost:9222/devtools/browser/abc", "debug_endpoint_not_loopback"),
        ("ws://203.0.113.5:9222/devtools/browser/abc", "debug_endpoint_not_loopback"),
        ("ws://0.0.0.0:9222/devtools/browser/abc", "debug_endpoint_not_loopback"),
        ("ws://127.0.0.2:9222/devtools/browser/abc", "debug_endpoint_not_loopback"),
        ("ws://127.0.0.1.example.test:9222/x", "debug_endpoint_not_loopback"),
        ("ws://127.0.0.1:9333/devtools/browser/abc", "debug_endpoint_undetermined"),
        ("ws://127.0.0.1/devtools/browser/abc", "debug_endpoint_undetermined"),
        ("ws://user@127.0.0.1:9222/devtools/browser/abc", "debug_endpoint_undetermined"),
        ("wss://127.0.0.1:9222/devtools/browser/abc", "debug_endpoint_undetermined"),
        ("http://127.0.0.1:9222/devtools/browser/abc", "debug_endpoint_undetermined"),
        ("ws://127.0.0.1:9222/a b", "debug_endpoint_undetermined"),
        (None, "debug_endpoint_undetermined"),
    ],
)
def test_announced_websocket_address(url: Any, expected: str) -> None:
    try:
        check_debugger_url(url, 9222)
    except ToolError as exc:
        assert exc.code == expected
    else:
        assert expected == "accepted"


def test_default_http_get_targets_127_0_0_1_only(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts: list[tuple[str, int, float]] = []

    class Recording:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            hosts.append((host, port, timeout))

        def request(self, method: str, path: str) -> None:
            raise ConnectionRefusedError

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", Recording)
    with pytest.raises(ConnectionRefusedError):
        default_http_get(9222)
    assert hosts == [("127.0.0.1", 9222, 3.0)]


# --- attachment ----------------------------------------------------------------------------


@pytest.fixture
async def fake() -> AsyncIterator[FakeCdp]:
    server = FakeCdp()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def fake_browser(fake: FakeCdp) -> FakeBrowser:
    return FakeBrowser(fake)


def profile_for(port: int, profile_dir: str, family: str = "chromium") -> Any:
    return SimpleNamespace(browser=SimpleNamespace(family=family, profile_dir=profile_dir, debug_port=port))


def loopback_tables(port: int) -> tuple[str, str]:
    return tcp_table((V4_LOOPBACK, port)), EMPTY_TCP6


class Steps:
    """Records the order in which attach reaches each of its dependencies."""

    def __init__(self, fake: FakeCdp, port: int) -> None:
        self.calls: list[str] = []
        self.fake = fake
        self.port = port

    def proc_net(self, tables: tuple[str | None, str | None] | None = None) -> Callable[[], tuple[str | None, str | None]]:
        def read() -> tuple[str | None, str | None]:
            self.calls.append("proc_net")
            return tables if tables is not None else loopback_tables(self.port)

        return read

    def http_get(self, payload: dict[str, Any] | None = None, error: Exception | None = None, status: int = 200) -> Callable[[int], tuple[int, bytes]]:
        def get(port: int) -> tuple[int, bytes]:
            self.calls.append("http_get")
            if error is not None:
                raise error
            if payload is None:
                return default_http_get(port)
            return status, json.dumps(payload).encode()

        return get

    def factory(self) -> CdpConnection:
        self.calls.append("connect")
        return CdpConnection()


async def test_attach_follows_the_order_and_describes_the_browser(fake: FakeCdp, fake_browser: FakeBrowser, tmp_path: Path) -> None:
    steps = Steps(fake, fake.port)
    attachment = await BrowserAttachment.attach(
        profile_for(fake.port, str(tmp_path / "profile")),
        cdp_factory=steps.factory,
        http_get=steps.http_get(),
        proc_net=steps.proc_net(),
        home=str(tmp_path / "home"),
        config_home="",
    )
    try:
        assert steps.calls == ["proc_net", "http_get", "connect"]
        assert fake.methods() == ["Browser.getVersion", "Target.setDiscoverTargets"]
        assert fake.received[1]["params"] == {"discover": True}
        assert attachment.describe() == {
            "product": "Chrome",
            "version": "154.0.0.0",
            "profile_dir_declared": str(tmp_path / "profile"),
        }
    finally:
        await attachment.close()


async def test_attach_against_the_fake_reads_the_real_socket_tables(fake: FakeCdp, fake_browser: FakeBrowser, tmp_path: Path) -> None:
    attachment = await BrowserAttachment.attach(profile_for(fake.port, str(tmp_path / "p")), home=str(tmp_path))
    await attachment.close()
    assert fake.methods() == ["Browser.getVersion", "Target.setDiscoverTargets"]


TablesOf = Callable[[int], tuple[str | None, str | None]]


@pytest.mark.parametrize(
    ("tables_of", "expected"),
    [
        (lambda port: (tcp_table((V4_LOOPBACK, port), (V4_ANY, port)), EMPTY_TCP6), "debug_endpoint_not_loopback"),
        (lambda port: (tcp_table((V4_LOOPBACK, port)), tcp6_table((V6_ANY, port))), "debug_endpoint_not_loopback"),
        (lambda port: (None, EMPTY_TCP6), "debug_endpoint_undetermined"),
        (lambda port: (EMPTY_TCP, EMPTY_TCP6), "browser_unreachable"),
    ],
)
async def test_attach_stops_at_the_loopback_check(
    fake: FakeCdp, tmp_path: Path, tables_of: TablesOf, expected: str
) -> None:
    steps = Steps(fake, fake.port)
    with pytest.raises(ToolError) as caught:
        await BrowserAttachment.attach(
            profile_for(fake.port, str(tmp_path / "p")),
            cdp_factory=steps.factory,
            http_get=steps.http_get(),
            proc_net=steps.proc_net(tables_of(fake.port)),
            home=str(tmp_path),
        )
    assert caught.value.code == expected
    assert steps.calls == ["proc_net"]
    await settle()
    assert fake.received == []


async def test_attach_refuses_the_default_profile_before_any_request(fake: FakeCdp, tmp_path: Path) -> None:
    steps = Steps(fake, fake.port)
    home, default = make_home(tmp_path, "chrome")
    with pytest.raises(ToolError) as caught:
        await BrowserAttachment.attach(
            profile_for(fake.port, str(default), family="chrome"),
            cdp_factory=steps.factory,
            http_get=steps.http_get(),
            proc_net=steps.proc_net(),
            home=str(home),
        )
    assert caught.value.code == "profile_dir_is_default"
    assert steps.calls == ["proc_net"]


@pytest.mark.parametrize(
    ("http_behaviour", "expected"),
    [
        ({"error": ConnectionRefusedError()}, "browser_unreachable"),
        ({"error": TimeoutError()}, "browser_unreachable"),
        ({"error": http.client.BadStatusLine("x")}, "browser_unreachable"),
        ({"payload": {"Browser": "Chrome/1"}}, "browser_unreachable"),
        ({"payload": {"webSocketDebuggerUrl": "ws://127.0.0.1:1/x"}, "status": 404}, "browser_unreachable"),
        ({"payload": {"webSocketDebuggerUrl": "ws://203.0.113.5:{port}/devtools/browser/x"}}, "debug_endpoint_not_loopback"),
        ({"payload": {"webSocketDebuggerUrl": "ws://localhost:{port}/devtools/browser/x"}}, "debug_endpoint_not_loopback"),
        ({"payload": {"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/x"}}, "debug_endpoint_undetermined"),
    ],
)
async def test_attach_checks_the_announced_endpoint_before_connecting(
    fake: FakeCdp, tmp_path: Path, http_behaviour: dict[str, Any], expected: str
) -> None:
    steps = Steps(fake, fake.port)
    payload = http_behaviour.get("payload")
    if payload is not None:
        payload = {k: v.replace("{port}", str(fake.port)) for k, v in payload.items()}
    with pytest.raises(ToolError) as caught:
        await BrowserAttachment.attach(
            profile_for(fake.port, str(tmp_path / "p")),
            cdp_factory=steps.factory,
            http_get=steps.http_get(payload, http_behaviour.get("error"), http_behaviour.get("status", 200)),
            proc_net=steps.proc_net(),
            home=str(tmp_path),
        )
    assert caught.value.code == expected
    assert steps.calls == ["proc_net", "http_get"]


async def test_attach_reports_an_endpoint_that_does_not_speak_cdp(fake: FakeCdp, tmp_path: Path) -> None:
    fake.on("Browser.getVersion", handler=lambda p, s: (_ for _ in ()).throw(FakeCdpError(-32601, "no")))
    with pytest.raises(ToolError) as caught:
        await BrowserAttachment.attach(profile_for(fake.port, str(tmp_path / "p")), home=str(tmp_path))
    assert caught.value.code == "browser_unreachable"


async def test_closing_the_attachment_never_closes_the_browser(fake: FakeCdp, fake_browser: FakeBrowser, tmp_path: Path) -> None:
    attachment = await BrowserAttachment.attach(profile_for(fake.port, str(tmp_path / "p")), home=str(tmp_path))
    await attachment.close()
    await settle()
    assert "Browser.close" not in fake.methods()
    assert attachment.connection.closed


# --- tabs ----------------------------------------------------------------------------------

FOREIGN_TITLES = ("Bank - Accounts", "Webmail - Inbox", "Private note")
FOREIGN_URLS = ("https://bank.example.test/accounts", "https://mail.example.test/inbox", "https://notes.example.test/p/1")


@pytest.fixture
async def attached(fake: FakeCdp, fake_browser: FakeBrowser, tmp_path: Path) -> AsyncIterator[BrowserAttachment]:
    attachment = await BrowserAttachment.attach(profile_for(fake.port, str(tmp_path / "p")), home=str(tmp_path))
    yield attachment
    await attachment.close()


@pytest.fixture
def registry(attached: BrowserAttachment) -> TabRegistry:
    return TabRegistry(attached.connection)


async def test_open_tab_creates_attaches_enables_then_navigates(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    tab = await registry.open_tab("https://example.test/start")
    methods = fake.methods()[2:]
    assert methods == ["Target.createTarget", "Target.attachToTarget", "Page.enable", "Inspector.enable", "Page.getFrameTree", "Page.navigate"]
    assert fake.received[2]["params"] == {"url": "about:blank"}
    assert fake.received[3]["params"]["flatten"] is True
    assert len(tab.tab_id) == 16 and int(tab.tab_id, 16) >= 0
    assert tab.tab_id != tab.target_id
    [info] = await registry.list_tabs()
    assert asdict(info) == {"tab_id": tab.tab_id, "origin": "https://example.test", "adopted": False}


async def test_open_tab_without_address_stays_blank(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab()
    assert "Page.navigate" not in fake.methods()
    [info] = await registry.list_tabs()
    assert info.origin == "about:blank"
    frame = await registry.main_frame_info(tab)
    assert frame.url == "about:blank" and frame.origin is None


def leak_search_text(*values: Any) -> str:
    return json.dumps([repr(value) for value in values], ensure_ascii=False)


TabRegistryFactory = Callable[[CdpConnection], TabRegistry]


async def foreign_tabs_stay_invisible(
    fake: FakeCdp,
    fake_browser: FakeBrowser,
    attached: BrowserAttachment,
    factory: TabRegistryFactory,
    caplog: pytest.LogCaptureFixture,
) -> bool:
    """True when nothing about three foreign tabs shows in any output, error or log."""
    caplog.set_level(logging.DEBUG)
    registry = factory(attached.connection)
    tab = await registry.open_tab("https://example.test/")
    for title, url in zip(FOREIGN_TITLES, FOREIGN_URLS, strict=True):
        await fake_browser.announce(title, url)
    await settle()
    outputs: list[Any] = [await registry.list_tabs(), attached.describe(), registry.drain_notices()]
    for title in FOREIGN_TITLES:
        try:
            registry.get(title)
        except ToolError as exc:
            outputs.append((exc.code, exc.message, exc.extra_server))
    outputs.append([record.getMessage() for record in caplog.records])
    outputs.append(vars(registry))
    outputs.append(await registry.main_frame_info(tab))
    text = leak_search_text(*outputs)
    found = [needle for needle in FOREIGN_TITLES + FOREIGN_URLS if needle in text]
    return not found and len(outputs[0]) == 1


class KeepEverythingRegistry(TabRegistry):
    """Defective: registers every target the browser announces."""

    def _on_target_created(self, params: dict[str, Any]) -> Any:
        info = params["targetInfo"]
        tab = self._register(info["targetId"], "S-" + info["targetId"], adopted=True)
        tab.main_frame_id = info["targetId"]
        self._notices.append(f"nouvel onglet : {info['title']}")
        return None


async def test_foreign_tabs_never_appear_anywhere(
    fake: FakeCdp, fake_browser: FakeBrowser, attached: BrowserAttachment, caplog: pytest.LogCaptureFixture
) -> None:
    assert await foreign_tabs_stay_invisible(fake, fake_browser, attached, TabRegistry, caplog)


async def test_foreign_tab_check_catches_a_registry_that_keeps_every_target(
    fake: FakeCdp, fake_browser: FakeBrowser, attached: BrowserAttachment, caplog: pytest.LogCaptureFixture
) -> None:
    fake_browser.sessions.update({f"S-TARGET{n:04d}": f"TARGET{n:04d}" for n in range(1, 10)})
    assert not await foreign_tabs_stay_invisible(fake, fake_browser, attached, KeepEverythingRegistry, caplog)
    registry = KeepEverythingRegistry(attached.connection)
    await fake_browser.announce("Bank - Accounts", FOREIGN_URLS[0])
    await settle()
    assert any(info.origin == "https://bank.example.test" for info in await registry.list_tabs())
    assert "Bank - Accounts" in leak_search_text(registry.drain_notices())


async def test_a_window_opened_by_an_owned_page_is_adopted(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    tab = await registry.open_tab("https://example.test/")
    registry.drain_notices()
    popup_target = await fake_browser.announce("Popup", "https://example.test/popup", opener_id=tab.target_id)
    notices: list[str] = []
    await until(lambda: bool(notices.extend(registry.drain_notices()) or notices))
    infos = await registry.list_tabs()
    adopted = [info for info in infos if info.adopted]
    assert len(adopted) == 1 and adopted[0].origin == "https://example.test"
    [notice] = notices
    assert tab.tab_id in notice and adopted[0].tab_id in notice
    assert "Popup" not in notice and "popup" not in notice
    attach_index = [m["params"] for m in fake.received].index({"targetId": popup_target, "flatten": True})
    adopted_session = fake_browser.session_of(popup_target)
    prepared = [m["method"] for m in fake.received[attach_index + 1 :] if m.get("sessionId") == adopted_session]
    assert prepared[:3] == ["Page.enable", "Inspector.enable", "Page.getFrameTree"]


async def test_a_window_opened_by_a_foreign_page_is_ignored(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    await registry.open_tab("https://example.test/")
    foreign = await fake_browser.announce("Bank - Accounts", FOREIGN_URLS[0])
    sent_before = len(fake.received)
    await fake_browser.announce("Bank popup", "https://bank.example.test/popup", opener_id=foreign)
    await settle()
    assert len(fake.received) == sent_before
    assert len(await registry.list_tabs()) == 1
    assert registry.drain_notices() == []


async def test_a_window_that_closes_while_being_adopted_is_not_kept(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    tab = await registry.open_tab("https://example.test/")
    gate = asyncio.Event()
    original = fake.handlers["Target.attachToTarget"]

    async def slow_attach(params: dict[str, Any], session_id: str | None) -> Any:
        await gate.wait()
        return original(params, session_id)

    fake.on("Target.attachToTarget", handler=slow_attach)
    popup = await fake_browser.announce("Popup", "https://example.test/p", opener_id=tab.target_id)
    await settle()
    await fake.emit("Target.targetDestroyed", {"targetId": popup})
    await settle()
    gate.set()
    await settle(10)
    assert len(await registry.list_tabs()) == 1


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/hostname",
        "javascript:alert(1)",
        "data:text/html,hello",
        "chrome://settings",
        "view-source:https://example.test/",
        "about:blank",
        "ftp://example.test/",
    ],
)
async def test_forbidden_schemes_are_refused_before_any_call(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, url: str
) -> None:
    sent_before = len(fake.received)
    with pytest.raises(ToolError) as caught:
        await registry.open_tab(url)
    assert caught.value.code == "forbidden_scheme"
    assert len(fake.received) == sent_before
    tab = await registry.open_tab()
    sent_before = len(fake.received)
    with pytest.raises(ToolError) as caught:
        await registry.navigate(tab, url)
    assert caught.value.code == "forbidden_scheme"
    assert len(fake.received) == sent_before
    assert "Page.navigate" not in fake.methods()


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://user:pw@example.test/", "userinfo"),
        ("https://exa mple.test/", "malformed"),
        ("https:///path", "empty_host"),
    ],
)
async def test_malformed_addresses_are_refused_before_any_call(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, url: str, reason: str
) -> None:
    tab = await registry.open_tab()
    sent_before = len(fake.received)
    with pytest.raises(ToolError) as caught:
        await registry.navigate(tab, url)
    assert caught.value.code == "navigation_failed"
    assert caught.value.extra_server == {"reason": reason}
    assert len(fake.received) == sent_before


async def test_navigation_is_free_outside_the_allowed_sites(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab("https://anywhere.example.test/")
    result = await registry.navigate(tab, "http://elsewhere.example.test:8080/x")
    assert result.loaded is True
    frame = await registry.main_frame_info(tab)
    assert frame.origin is not None and frame.origin.canonical == "http://elsewhere.example.test:8080"


async def test_error_text_gives_navigation_failed(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab()
    fake_browser.navigation_error = "net::ERR_NAME_NOT_RESOLVED"
    with pytest.raises(ToolError) as caught:
        await registry.navigate(tab, "https://missing.example.test/")
    assert caught.value.code == "navigation_failed"
    assert "net::ERR_NAME_NOT_RESOLVED" in caught.value.message
    assert caught.value.extra_server == {"tab_id": tab.tab_id}


async def test_open_tab_with_a_failed_navigation_keeps_the_tab(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    fake_browser.navigation_error = "net::ERR_CONNECTION_REFUSED"
    with pytest.raises(ToolError) as caught:
        await registry.open_tab("https://refused.example.test/")
    assert caught.value.code == "navigation_failed"
    [info] = await registry.list_tabs()
    assert caught.value.extra_server["tab_id"] == info.tab_id


async def test_a_page_that_never_finishes_returns_at_the_deadline(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    tab = await registry.open_tab()
    fake_browser.emit_load_events = False
    started = asyncio.get_running_loop().time()
    result = await registry.navigate(tab, "https://polling.example.test/", timeout_s=0.3)
    elapsed = asyncio.get_running_loop().time() - started
    assert result.loaded is False
    assert 0.25 <= elapsed < 2
    [notice] = registry.drain_notices()
    assert tab.tab_id in notice


async def test_a_navigation_that_never_commits_returns_at_the_deadline(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    tab = await registry.open_tab()
    fake.on("Page.navigate", NO_REPLY)
    result = await registry.navigate(tab, "https://hanging.example.test/", timeout_s=0.3)
    assert result.loaded is False
    assert len(registry.drain_notices()) == 1


async def test_frame_stopped_loading_of_the_main_frame_ends_the_wait(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    tab = await registry.open_tab()
    fake_browser.emit_load_events = False
    navigation = asyncio.create_task(registry.navigate(tab, "https://example.test/", timeout_s=5))
    await until(lambda: "Page.navigate" in fake.methods())
    await settle()
    await fake.emit("Page.frameStoppedLoading", {"frameId": "SUBFRAME"}, session_id=tab.session_id)
    await fake.emit("Page.loadEventFired", {}, session_id="ANOTHER-SESSION")
    await settle()
    assert not navigation.done()
    await fake.emit("Page.frameStoppedLoading", {"frameId": tab.main_frame_id}, session_id=tab.session_id)
    assert (await asyncio.wait_for(navigation, 2)).loaded is True


async def test_same_document_navigation_does_not_wait(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab("https://example.test/")
    fake_browser.same_document = True
    result = await asyncio.wait_for(registry.navigate(tab, "https://example.test/#part"), 2)
    assert result.loaded is True


@pytest.mark.parametrize("timeout_s", [-1, float("nan"), "15", True])
async def test_invalid_timeout_is_refused(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, timeout_s: Any) -> None:
    tab = await registry.open_tab()
    with pytest.raises(ValueError):
        await registry.navigate(tab, "https://example.test/", timeout_s=timeout_s)


def test_navigation_timeout_is_capped_at_sixty_seconds() -> None:
    assert browser._navigation_timeout(600) == 60.0
    assert browser._navigation_timeout(15) == 15.0


async def test_unknown_and_closed_tabs_are_not_owned(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    with pytest.raises(ToolError) as caught:
        registry.get("0123456789abcdef")
    assert caught.value.code == "tab_not_owned"
    tab = await registry.open_tab()
    assert registry.get(tab.tab_id) is tab
    await registry.close_tab(tab.tab_id)
    assert "Target.closeTarget" in fake.methods()
    assert tab.closed
    with pytest.raises(ToolError) as caught:
        registry.get(tab.tab_id)
    assert caught.value.code == "tab_not_owned"
    for operation in (
        registry.close_tab(tab.tab_id),
        registry.navigate(tab, "https://example.test/"),
        registry.main_frame_info(tab),
        registry.handle_dialog(tab, accept=True),
    ):
        with pytest.raises(ToolError) as caught:
            await operation
        assert caught.value.code == "tab_not_owned"
    assert await registry.list_tabs() == []


@pytest.mark.parametrize(
    ("method", "params_of"),
    [
        ("Target.targetDestroyed", lambda tab: ({"targetId": tab.target_id}, None)),
        ("Target.targetCrashed", lambda tab: ({"targetId": tab.target_id, "status": "crashed", "errorCode": 1}, None)),
        ("Target.detachedFromTarget", lambda tab: ({"sessionId": tab.session_id, "targetId": tab.target_id}, None)),
        ("Inspector.targetCrashed", lambda tab: ({}, tab.session_id)),
    ],
)
async def test_events_mark_a_tab_closed(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, method: str, params_of: Callable[[Tab], tuple[dict[str, Any], str | None]]
) -> None:
    tab = await registry.open_tab()
    params, session_id = params_of(tab)
    await fake.emit(method, params, session_id=session_id)
    await until(lambda: tab.closed)
    with pytest.raises(ToolError) as caught:
        registry.get(tab.tab_id)
    assert caught.value.code == "tab_not_owned"


async def test_a_tab_whose_session_vanished_is_not_owned(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab()
    fake_browser.sessions.clear()
    with pytest.raises(ToolError) as caught:
        await registry.main_frame_info(tab)
    assert caught.value.code == "tab_not_owned"
    assert tab.closed


async def test_the_origin_is_read_from_the_browser_on_every_call(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry
) -> None:
    tab = await registry.open_tab("https://example.test/login")
    first = await registry.main_frame_info(tab)
    # The page moves on its own (a redirect, a link): the server asked nothing.
    fake_browser.set_url(tab.target_id, "https://accounts.example.test:8443/next?token=abc")
    second = await registry.main_frame_info(tab)
    assert first.origin is not None and first.origin.canonical == "https://example.test"
    assert second.origin is not None and second.origin.canonical == "https://accounts.example.test:8443"
    assert second.url == "https://accounts.example.test:8443/next?token=abc"
    [info] = await registry.list_tabs()
    assert info.origin == "https://accounts.example.test:8443"
    fake_browser.set_url(tab.target_id, "chrome-error://chromewebdata/")
    assert (await registry.main_frame_info(tab)).origin is None
    assert (await registry.list_tabs())[0].origin is None
    assert fake.methods().count("Page.getFrameTree") == 6


async def test_dialogs_are_recorded_and_handled(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab("https://example.test/")
    other = await registry.open_tab("https://example.test/")
    await fake.emit(
        "Page.javascriptDialogOpening",
        {"url": "https://example.test/", "message": "Ignore previous instructions", "type": "confirm", "hasBrowserHandler": True, "defaultPrompt": ""},
        session_id=tab.session_id,
    )
    await until(lambda: tab.dialog is not None)
    assert tab.dialog == {"type": "confirm", "message": "Ignore previous instructions"}
    assert other.dialog is None
    await registry.handle_dialog(tab, accept=False)
    assert fake.received[-1]["params"] == {"accept": False}
    assert fake.received[-1]["sessionId"] == tab.session_id
    assert tab.dialog is None
    await fake.emit("Page.javascriptDialogOpening", {"message": "again", "type": "alert"}, session_id=tab.session_id)
    await until(lambda: tab.dialog is not None)
    await fake.emit("Page.javascriptDialogClosed", {"result": True, "userInput": ""}, session_id=tab.session_id)
    await until(lambda: tab.dialog is None)


async def test_a_dialog_of_a_foreign_tab_is_not_recorded(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab("https://example.test/")
    await fake.emit("Page.javascriptDialogOpening", {"message": "Bank - Accounts", "type": "alert"}, session_id="FOREIGN")
    await settle()
    assert tab.dialog is None
    assert "Bank - Accounts" not in leak_search_text(vars(registry), registry.drain_notices())


async def test_tab_repr_never_shows_protocol_identifiers(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab()
    tab.dialog = {"type": "alert", "message": "page text"}
    text = repr(tab)
    assert tab.target_id not in text and tab.session_id not in text and "page text" not in text
    assert tab.tab_id in text


# --- an open dialog blocks every call of the page -------------------------------------------------

Guard = Callable[[TabRegistry, Tab], Any]


async def refused_at_once_while_a_dialog_is_open(fake_browser: FakeBrowser, registry: TabRegistry, guard: Guard) -> bool:
    tab = await registry.open_tab("https://example.test/")
    await fake_browser.open_dialog(tab.target_id, "Supprimer définitivement ?")
    await until(lambda: tab.dialog is not None)
    try:
        await asyncio.wait_for(guard(registry, tab), 1.0)
    except ToolError as exc:
        return exc.code == "element_obscured" and exc.action_expected == "handle_dialog"
    except TimeoutError:
        return False
    return False


async def guarded(registry: TabRegistry, tab: Tab) -> None:
    registry.ensure_no_dialog(tab)
    await registry.main_frame_info(tab, stop_on_dialog=True)


async def reads_the_frame_directly(registry: TabRegistry, tab: Tab) -> None:
    # Defective: asks the page for its frame tree as if no dialog were open.
    await registry.main_frame_info(tab)


async def test_an_action_while_a_dialog_is_open_is_refused_at_once(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    assert await refused_at_once_while_a_dialog_is_open(fake_browser, registry, guarded)


async def test_dialog_check_catches_a_direct_frame_read(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    assert not await refused_at_once_while_a_dialog_is_open(fake_browser, registry, reads_the_frame_directly)


async def test_a_read_gives_up_and_the_dialog_can_still_be_answered(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await registry.open_tab("https://example.test/")
    await fake_browser.open_dialog(tab.target_id, "Continue?")
    await until(lambda: tab.dialog is not None)
    assert tab.dialog_url == "https://example.test/"
    with pytest.raises(browser.DialogBlocking):
        await asyncio.wait_for(registry.read(tab, "Page.getFrameTree"), 1.0)
    await asyncio.wait_for(registry.handle_dialog(tab, accept=False), 1.0)
    assert fake_browser.accepted == [False] and tab.dialog is None
    await until(lambda: not fake_browser.dialogs)
    registry.ensure_no_dialog(tab)
    assert (await asyncio.wait_for(registry.main_frame_info(tab), 1.0)).origin is not None
