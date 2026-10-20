"""Le serveur par ses outils, en mémoire : un client du SDK connecté à create_server."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import FAKE_PASSWORD, FAKE_USERNAME, ROOT_LINE, FakeMount, VolumePaths, mounted_volume, write_credential, write_profile
from fixtures.action_pages import HOSTILE, drift_page, hostile_page, login_page, neutral_page, payment_page
from fixtures.action_support import FakeClock, FakeWindow
from fixtures.fake_cdp import FakeBrowser, FakeCdp
from fixtures.page_model import button, text
from mcp import types
from mcp.client.session import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_server_diwall_browser.browser import default_http_get, read_proc_net_tcp
from mcp_server_diwall_browser.cdp import CdpConnection
from mcp_server_diwall_browser.journal import FIELDS, Journal
from mcp_server_diwall_browser.operator_profile import load_profile
from mcp_server_diwall_browser.output import EXIT_POINT_ATTRIBUTE, ToolOutput
from mcp_server_diwall_browser.perception import draw_mask
from mcp_server_diwall_browser.server import TOOLS, Dependencies, ToolArguments, create_server, registered_tool

ORIGIN = "https://shop.example.test"
URL = ORIGIN + "/page"
PAY_URL = ORIGIN + "/checkout"
LOGIN_URL = ORIGIN + "/login"
NEIGHBOUR = "https://shop.example.test:8443"
OTHER = "https://other.example.test"
SETUP = "mcp-server-diwall-browser-setup"
SOCLE_TOOLS = [
    "status", "list_tabs", "open_tab", "navigate", "close_tab", "observe", "scroll",
    "wait", "click", "fill", "select_option", "press_key", "handle_dialog", "save_evidence",
]
FORBIDDEN_WORDS = ("eval", "script", "cdp", "javascript", "upload", "clipboard")
CONTENT_READS = ("Accessibility.getFullAXTree", "DOMSnapshot.captureSnapshot", "Page.captureScreenshot")


# --- le montage -------------------------------------------------------------------------------------


@dataclass
class Rig:
    tmp: Path
    fake: FakeCdp
    browser: FakeBrowser
    paths: VolumePaths
    mount: FakeMount
    crypt: Path
    window: FakeWindow
    clock: FakeClock
    environ: dict[str, str]
    profile_settings: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    def write_profile(self, **overrides: Any) -> None:
        settings = {
            "secrets_dir": str(self.paths.mount_point),
            "secrets_crypt_dir": str(self.crypt),
            "allowed_origins": [ORIGIN],
            "browser": {"family": "chromium", "profile_dir": str(self.tmp / "dedicated-profile"), "debug_port": self.fake.port},
        }
        settings.update(overrides)
        self.profile_settings = settings
        write_profile(self.tmp, **settings)

    def remove_profile(self) -> None:
        (self.tmp / "config" / "mcp-server-diwall-browser" / "profile.json").unlink()

    def close_volume(self) -> None:
        self.mount.rewrite(ROOT_LINE + "\n")

    def deps(self) -> Dependencies:
        return Dependencies(
            load_profile=lambda: load_profile(self.environ),
            mount_checker=self.mount.checker,
            cdp_factory=CdpConnection,
            http_get=default_http_get,
            proc_net=read_proc_net_tcp,
            which=self.window.which,
            environ={"DISPLAY": ":0"},
            clock=self.clock,
            sleep=self.clock.sleep,
            mask_drawer=draw_mask,
            journal=Journal,
            **{"settle_start_s": 0.05, "settle_download_start_s": 0.05, "settle_load_s": 2.0, "confirmation_timeout_s": 10.0, **self.timings},
        )

    def journal_lines(self) -> list[dict[str, Any]]:
        path = self.paths.project / "journal" / "operations.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def gestures(self) -> list[str]:
        return [m for m in self.fake.methods() if m.startswith("Input.")]

    def page_of(self, url: str) -> Any:
        return next(target["page"] for target in self.browser.targets.values() if target.get("url") == url and "page" in target)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    fake = FakeCdp()
    await fake.start()
    browser = FakeBrowser(fake)
    paths, mount, _volume = mounted_volume(tmp_path)
    crypt = tmp_path / "Main.enc"
    crypt.mkdir(mode=0o700)
    fd = os.open(crypt / "gocryptfs.conf", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    clock = FakeClock()
    environ = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config")}
    built = Rig(tmp_path, fake, browser, paths, mount, crypt, FakeWindow(tmp_path / "bin"), clock, environ)
    built.write_profile()
    for url, page in ((URL, neutral_page), (PAY_URL, payment_page), (LOGIN_URL, login_page)):
        browser.site[url] = page
    yield built
    await fake.stop()


class Client:
    """Un client du SDK, avec l'enveloppe de chaque résultat relue."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self.image: bytes | None = None
        self.results: list[types.CallToolResult] = []

    async def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments)
        self.results.append(result)
        assert result.content and result.content[0].type == "text"
        envelope = json.loads(result.content[0].text)
        assert set(envelope) == {"ok", "server", "page_content"}
        # Un refus est marqué comme résultat d'erreur, un succès ne l'est pas.
        assert result.isError is (envelope["ok"] is not True)
        self.image = None
        if len(result.content) > 1:
            assert result.content[1].type == "image" and result.content[1].mimeType == "image/png"
            self.image = base64.b64decode(result.content[1].data)
        return envelope


Elicit = Callable[[Any, types.ElicitRequestParams], Awaitable[types.ElicitResult]]


@asynccontextmanager
async def connected(rig: Rig, *, name: str = "test-client", elicitation: Elicit | None = None) -> AsyncIterator[Client]:
    server = create_server(rig.deps())
    info = types.Implementation(name=name, version="1.0")
    async with create_connected_server_and_client_session(server, client_info=info, elicitation_callback=elicitation) as session:
        yield Client(session)


def mark_of(observed: dict[str, Any], name: str) -> int:
    return next(element["mark"] for element in observed["page_content"]["elements"] if element["name"] == name)


async def open_and_observe(client: Client, url: str) -> tuple[str, dict[str, Any]]:
    opened = await client.call("open_tab", url=url, intent="look at the page")
    assert opened["ok"], opened
    tab = opened["server"]["tab_id"]
    observed = await client.call("observe", tab_id=tab)
    assert observed["ok"], observed
    return tab, observed


def code_of(envelope: dict[str, Any]) -> str:
    return envelope["server"]["error"]["code"]


# --- surface ----------------------------------------------------------------------------------------


async def test_the_fourteen_tools_and_nothing_else(rig: Rig) -> None:
    async with connected(rig) as client:
        tools = (await client.session.list_tools()).tools
    assert [tool.name for tool in tools] == SOCLE_TOOLS
    for tool in tools:
        surface = (tool.name + " " + " ".join(tool.inputSchema.get("properties", {}))).lower()
        assert not any(word in surface for word in FORBIDDEN_WORDS), tool.name
        assert tool.description and not any(word in tool.description.lower() for word in FORBIDDEN_WORDS), tool.name
        assert tool.inputSchema.get("additionalProperties") is False, tool.name


async def test_the_parameters_are_those_of_the_operators_table(rig: Rig) -> None:
    expected = {
        "status": set(),
        "list_tabs": set(),
        "open_tab": {"url", "intent"},
        "navigate": {"tab_id", "url", "intent"},
        "close_tab": {"tab_id"},
        "observe": {"tab_id", "include", "region", "contains"},
        "scroll": {"tab_id", "dy", "to", "mark", "snapshot_id"},
        "wait": {"tab_id", "for", "value", "timeout_s"},
        "click": {"tab_id", "snapshot_id", "mark", "intent", "intent_category"},
        "fill": {"tab_id", "snapshot_id", "mark", "text", "credential_ref", "intent"},
        "select_option": {"tab_id", "snapshot_id", "mark", "option", "intent"},
        "press_key": {"tab_id", "key", "snapshot_id", "mark", "intent"},
        "handle_dialog": {"tab_id", "accept"},
        "save_evidence": {"tab_id", "label"},
    }
    async with connected(rig) as client:
        tools = (await client.session.list_tools()).tools
    assert {tool.name: set(tool.inputSchema.get("properties", {})) for tool in tools} == expected


def undecorated(server: FastMCP) -> list[str]:
    """Outils enregistrés qui ne passent pas par le point de sortie unique."""
    return [
        tool.name
        for tool in server._tool_manager.list_tools()
        if getattr(getattr(tool, "entry", None), EXIT_POINT_ATTRIBUTE, None) != tool.name
    ]


async def test_every_registered_tool_goes_through_the_exit_point(rig: Rig) -> None:
    assert undecorated(create_server(rig.deps())) == []


async def test_exit_point_check_catches_a_tool_registered_without_it(rig: Rig) -> None:
    server = create_server(rig.deps())

    def extra() -> str:
        """Un outil écrit hors du point de sortie."""
        return "{}"

    server.add_tool(extra, name="extra")
    assert undecorated(server) == ["extra"]


async def test_a_tool_module_without_the_exit_point_is_never_registered(rig: Rig) -> None:
    async def run(ctx: Any) -> ToolOutput:
        return ToolOutput()

    module = SimpleNamespace(run=run, Arguments=ToolArguments)
    with pytest.raises(TypeError):
        registered_tool(None, "status", False, module)  # type: ignore[arg-type]


async def test_the_guide_is_the_servers_instruction(rig: Rig) -> None:
    from mcp_server_diwall_browser import guide

    server = create_server(rig.deps())
    assert server.instructions == guide.INSTRUCTIONS and server.name == "mcp-server-diwall-browser"


# --- chemin nominal ---------------------------------------------------------------------------------


async def test_status_open_observe_fill_click_observe(rig: Rig) -> None:
    async with connected(rig) as client:
        status = await client.call("status")
        assert status["ok"] and status["server"]["configured"] is True
        assert status["server"]["browser"]["attached"] is True
        tab, observed = await open_and_observe(client, URL)
        assert observed["server"]["origin"] == ORIGIN and observed["server"]["capture"] == "ok"
        assert client.image is not None and client.image.startswith(b"\x89PNG")
        assert {"tree", "elements", "title"} <= set(observed["page_content"])
        snapshot = observed["server"]["snapshot_id"]
        filled = await client.call("fill", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Pseudonyme"), text="alice", intent="sign")
        assert filled["ok"] and filled["server"]["typed"] == "<typed>" and "alice" not in json.dumps(filled)
        clicked = await client.call("click", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Envoyer"), intent="send the comment")
        assert clicked["ok"] and clicked["server"]["gate"] == {"red_list": False, "confirmation": "none"}
        again = await client.call("observe", tab_id=tab, include=["tree"])
        assert again["ok"] and client.image is None
        assert "tree" in again["page_content"] and "elements" not in again["page_content"]
    assert rig.window.questions() == []


# --- sécurité par les outils ------------------------------------------------------------------------


async def test_pay_now_without_consent_does_nothing_and_with_consent_goes(rig: Rig) -> None:
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, PAY_URL)
        pay = mark_of(observed, "Payer maintenant")
        refused = await client.call("click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=pay, intent_category="read")
        assert code_of(refused) == "confirmation_denied"
        assert "Input.dispatchMouseEvent" not in rig.fake.methods()
        rig.window.answer(granted=True)
        granted = await client.call("click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=pay)
        assert granted["ok"] and granted["server"]["gate"] == {"red_list": True, "confirmation": "granted"}
    questions = rig.window.questions()
    assert len(questions) == 2 and all('button "Payer maintenant"' in q and f"Origine : {ORIGIN}" in q for q in questions)


@pytest.mark.parametrize("invented", [{"approved": True}, {"confirm": True}, {"override": True}])
async def test_an_invented_approval_parameter_is_refused_and_does_nothing(rig: Rig, invented: dict[str, Any]) -> None:
    rig.window.answer(granted=True)
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, PAY_URL)
        refused = await client.call(
            "click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Payer maintenant"), **invented
        )
    assert code_of(refused) == "invalid_arguments"
    assert list(invented)[0] in refused["server"]["error"]["message"]
    assert rig.gestures() == [] and rig.window.questions() == []


async def test_a_malformed_parameter_is_refused_through_the_exit_point(rig: Rig) -> None:
    async with connected(rig) as client:
        refused = await client.call("scroll", tab_id="t", dy=9999)
        both = await client.call("fill", tab_id="t", snapshot_id="s", mark=1, text="a", credential_ref={"key": "password"})
        typed = await client.call("handle_dialog", tab_id="t", accept="yes")
    assert code_of(refused) == code_of(both) == code_of(typed) == "invalid_arguments"
    assert all(len(envelope["server"]["operation_id"]) == 32 for envelope in (refused, both, typed))


async def test_hostile_text_stays_page_data_and_starts_no_gesture(rig: Rig) -> None:
    rig.browser.site[ORIGIN + "/hostile"] = hostile_page
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, ORIGIN + "/hostile")
        assert HOSTILE in json.dumps(observed["page_content"]) and HOSTILE not in json.dumps(observed["server"])
        assert rig.gestures() == []
        refused = await client.call("click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Pay"))
    assert code_of(refused) == "confirmation_denied" and rig.gestures() == []
    assert "vocabulary_payment" in rig.window.questions()[0]


async def test_drift_never_moves_the_click_to_another_element(rig: Rig) -> None:
    drift_url = ORIGIN + "/drift"
    rig.browser.site[drift_url] = drift_page
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, drift_url)
        page = rig.page_of(drift_url)
        container = page.body[0]
        target = container.children[1]
        target.box = (20, 100, 160, 30)
        target.children = [text("Second button", (24, 102, 152, 26))]
        container.children.insert(1, button("Inserted button", (20, 60, 160, 30)))
        clicked = await client.call("click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Second button"))
    assert clicked["ok"]
    assert [hit for _, hit in rig.browser.clicks] == [target.backend_id]


async def test_the_ceilings_hold_through_the_tools(rig: Rig) -> None:
    rig.write_profile(limits={"max_actions_per_session": 3, "max_pages_per_session": 50, "min_action_delay_ms": 0})
    async with connected(rig) as client:
        tab, _observed = await open_and_observe(client, URL)
        assert (await client.call("scroll", tab_id=tab, dy=10))["ok"]
        assert (await client.call("scroll", tab_id=tab, dy=10))["ok"]
        refused = await client.call("scroll", tab_id=tab, dy=10)
        status = await client.call("status")
    assert code_of(refused) == "limit_reached" and refused["server"]["counters"]["actions"] == 3
    assert status["server"]["counters"]["actions"] == 3


async def test_a_credential_on_a_neighbouring_origin_and_text_in_a_sensitive_field_are_refused(rig: Rig) -> None:
    write_credential(rig.paths, "shop.json", allowed_origins=[ORIGIN])
    rig.write_profile(allowed_origins=[ORIGIN, NEIGHBOUR])
    rig.browser.site[NEIGHBOUR + "/login"] = login_page
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, NEIGHBOUR + "/login")
        snapshot = observed["server"]["snapshot_id"]
        password = mark_of(observed, "Mot de passe")
        wrong = await client.call("fill", tab_id=tab, snapshot_id=snapshot, mark=password, credential_ref={"key": "password"})
        plain = await client.call("fill", tab_id=tab, snapshot_id=snapshot, mark=password, text="hunter2")
    assert code_of(wrong) == "credential_not_bound" and code_of(plain) == "sensitive_value_refused"
    assert rig.browser.typed == [] and rig.window.questions() == []


async def test_an_origin_outside_the_list_is_named_and_never_read(rig: Rig) -> None:
    rig.browser.site[OTHER + "/page"] = neutral_page
    async with connected(rig) as client:
        opened = await client.call("open_tab", url=OTHER + "/page")
        assert opened["ok"] and opened["server"]["origin"] == OTHER
        before = len(rig.fake.methods())
        refused = await client.call("observe", tab_id=opened["server"]["tab_id"])
    assert code_of(refused) == "origin_not_allowed" and refused["server"]["origin"] == OTHER
    assert refused["page_content"] is None
    assert not any(method in CONTENT_READS for method in rig.fake.methods()[before:])


async def test_a_closed_volume_waits_for_the_operator(rig: Rig) -> None:
    write_credential(rig.paths, "shop.json", allowed_origins=[ORIGIN])
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, LOGIN_URL)
        _pay_tab, pay_observed = await open_and_observe(client, PAY_URL)
        rig.close_volume()
        refused = await client.call(
            "fill", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Mot de passe"), credential_ref={"key": "password"}
        )
        armed = await client.call(
            "click", tab_id=_pay_tab, snapshot_id=pay_observed["server"]["snapshot_id"], mark=mark_of(pay_observed, "Payer maintenant")
        )
        reading = await client.call("observe", tab_id=tab)
        status = await client.call("status")
    assert code_of(refused) == "encrypted_dir_closed"
    assert refused["server"]["error"]["action_expected"] == "operator_opens_directory"
    message = refused["server"]["error"]["message"]
    assert "n'existe pas" not in message and "introuvable" not in message
    assert code_of(armed) == "encrypted_dir_closed" and rig.gestures() == []
    assert reading["ok"]
    assert status["server"]["encrypted_directory"] == {
        "state": "closed", "code": "encrypted_dir_closed", "action_expected": "operator_opens_directory", "next_command": f"{SETUP} open",
    }


async def test_without_a_profile_status_says_what_is_missing_and_the_rest_waits(rig: Rig) -> None:
    rig.remove_profile()
    calls = {
        "list_tabs": {},
        "open_tab": {"url": URL},
        "navigate": {"tab_id": "t", "url": URL},
        "close_tab": {"tab_id": "t"},
        "observe": {"tab_id": "t"},
        "scroll": {"tab_id": "t", "dy": 1},
        "wait": {"tab_id": "t", "for": "load"},
        "click": {"tab_id": "t", "snapshot_id": "s", "mark": 1},
        "fill": {"tab_id": "t", "snapshot_id": "s", "mark": 1, "text": "x"},
        "select_option": {"tab_id": "t", "snapshot_id": "s", "mark": 1, "option": "x"},
        "press_key": {"tab_id": "t", "key": "Tab"},
        "handle_dialog": {"tab_id": "t", "accept": False},
        "save_evidence": {"tab_id": "t", "label": "x"},
    }
    async with connected(rig) as client:
        status = await client.call("status")
        refusals = {name: code_of(await client.call(name, **arguments)) for name, arguments in calls.items()}
    assert status["ok"] and status["server"]["configured"] is False and status["server"]["missing"] == "profile"
    assert status["server"]["how_to_provide"]["environment_variable"] == "MCP_DIWALL_BROWSER_PROFILE"
    assert "standard_location" in status["server"]["how_to_provide"]
    assert status["server"]["how_to_provide"]["setup_command"] == f"{SETUP} create"
    assert status["server"]["next_command"] == f"{SETUP} check"
    assert refusals == {name: "not_configured" for name in calls}
    assert "Browser.getVersion" not in rig.fake.methods()


async def test_a_foreign_tab_never_shows_its_title_nor_its_address(rig: Rig) -> None:
    async with connected(rig) as client:
        tab, _observed = await open_and_observe(client, URL)
        await rig.browser.announce("Banque — relevé", "https://bank.example.test/account?token=abc")
        listed = await client.call("list_tabs")
        status = await client.call("status")
    assert [entry["tab_id"] for entry in listed["server"]["tabs"]] == [tab]
    assert listed["server"]["tabs"] == [{"tab_id": tab, "origin": ORIGIN, "adopted": False}]
    everything = json.dumps([result.model_dump() for result in client.results])
    assert "Banque" not in everything and "bank.example.test" not in everything


async def test_a_dialog_open_in_the_tab_refuses_every_other_action_at_once(rig: Rig) -> None:
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, URL)
        target = next(t for t, info in rig.browser.targets.items() if info.get("url") == URL)
        await rig.browser.open_dialog(target, "Continuer ?")
        snapshot = observed["server"]["snapshot_id"]
        refusals = [
            await client.call("click", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Envoyer")),
            await client.call("fill", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Pseudonyme"), text="x"),
            await client.call("select_option", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Pays"), option="France"),
            await client.call("press_key", tab_id=tab, key="Tab"),
            await client.call("scroll", tab_id=tab, dy=100),
        ]
        dismissed = await client.call("handle_dialog", tab_id=tab, accept=False)
    for refusal in refusals:
        assert code_of(refusal) == "element_obscured"
        assert refusal["server"]["error"]["action_expected"] == "handle_dialog"
    assert dismissed["ok"] and rig.gestures() == []


# --- le journal, écrit une fois pour chaque appel ---------------------------------------------------


async def test_a_session_of_ten_tools_writes_ten_lines_without_any_credential(rig: Rig) -> None:
    write_credential(rig.paths, "shop.json", allowed_origins=[ORIGIN])
    async with connected(rig) as client:
        await client.call("status")
        await client.call("list_tabs")
        tab, observed = await open_and_observe(client, LOGIN_URL)
        snapshot = observed["server"]["snapshot_id"]
        await client.call("fill", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Mot de passe"), credential_ref={"key": "password"}, intent="sign in")
        await client.call("click", tab_id=tab, snapshot_id=snapshot, mark=mark_of(observed, "Se connecter"), intent="sign in")
        await client.call("scroll", tab_id=tab, dy=50)
        await client.call("wait", tab_id=tab, **{"for": "text"}, value="Connexion", timeout_s=1.0)
        await client.call("save_evidence", tab_id=tab, label="signed in")
        await client.call("close_tab", tab_id=tab)
    lines = rig.journal_lines()
    assert [line["tool"] for line in lines] == [
        "status", "list_tabs", "open_tab", "observe", "fill", "click", "scroll", "wait", "save_evidence", "close_tab"
    ]
    assert all(tuple(line) == FIELDS for line in lines)
    raw = (rig.paths.project / "journal" / "operations.jsonl").read_text()
    leaked = FAKE_PASSWORD in raw or FAKE_USERNAME in raw
    assert not leaked, "a credential value is in the journal"
    fill_line = lines[4]
    assert fill_line["intent"] == "sign in [input: <secret:password>]" and fill_line["mutating"] is True
    assert fill_line["origin"] == ORIGIN and fill_line["result"] == "ok"
    assert [line["mutating"] for line in lines] == [False, False, True, False, True, True, False, False, False, False]
    assert all(line["client"] == {"name": "test-client", "version": "1.0"} for line in lines)


async def test_with_the_volume_closed_calls_are_counted_as_not_journaled(rig: Rig) -> None:
    async with connected(rig) as client:
        first = await client.call("status")
        rig.close_volume()
        listed = await client.call("list_tabs")
        second = await client.call("status")
        third = await client.call("status")
    # Un appel est journalisé après la construction de son résultat : status compte les appels qui le précèdent.
    assert listed["ok"]
    assert first["server"]["operations_not_journaled"] == 0
    assert second["server"]["operations_not_journaled"] == 1
    assert third["server"]["operations_not_journaled"] == 2
    assert [line["tool"] for line in rig.journal_lines()] == ["status"]


async def test_the_client_name_cannot_break_a_journal_line(rig: Rig) -> None:
    async with connected(rig, name='evil\n"}{"x') as client:
        await client.call("status")
    [line] = rig.journal_lines()
    assert line["client"]["name"] == "evilx"


async def test_an_armed_action_writes_its_request_then_its_result_and_every_state_is_journaled(rig: Rig) -> None:
    rig.timings = {"confirmation_timeout_s": 1.0}
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, PAY_URL)
        snapshot = observed["server"]["snapshot_id"]
        pay = mark_of(observed, "Payer maintenant")
        rig.window.answer(granted=True)
        await client.call("click", tab_id=tab, snapshot_id=snapshot, mark=pay)
        snapshot = (await client.call("observe", tab_id=tab))["server"]["snapshot_id"]
        rig.window.answer(granted=False)
        await client.call("click", tab_id=tab, snapshot_id=snapshot, mark=pay)
        rig.window.answer(wait=True)
        await client.call("click", tab_id=tab, snapshot_id=snapshot, mark=pay)
        rig.window.present = False
        await client.call("click", tab_id=tab, snapshot_id=snapshot, mark=pay)
    lines = rig.journal_lines()
    clicks = [line for line in lines if line["tool"] == "click"]
    first_request, first_result = clicks[0], clicks[1]
    assert first_request["operation_id"] == first_result["operation_id"]
    assert (first_request["result"], first_request["gates"]) == ("requested", {"red_list": True, "confirmation": "requested", "origin_policy": "strict"})
    assert (first_result["result"], first_result["gates"]["confirmation"]) == ("ok", "granted")
    finals = [line["gates"]["confirmation"] for line in clicks if line["result"] != "requested"]
    assert finals == ["granted", "denied", "expired", "unavailable"]
    seen = {line["gates"]["confirmation"] for line in lines}
    assert seen == {"none", "requested", "granted", "denied", "expired", "unavailable"}


# --- la confirmation propre du client ---------------------------------------------------------------


def answering(content: dict[str, Any] | None, action: str = "accept") -> Elicit:
    async def elicit(context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        answering.asked.append(params.message)  # type: ignore[attr-defined]
        return types.ElicitResult(action=action, content=content)  # type: ignore[arg-type]

    return elicit


answering.asked = []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("content", "action", "expected"),
    [
        ({"approve": True}, "accept", "granted"),
        ({"approve": False}, "accept", "denied"),
        (None, "decline", "denied"),
        (None, "cancel", "denied"),
    ],
)
async def test_the_clients_confirmation_is_used_on_the_operators_declaration(
    rig: Rig, content: dict[str, Any] | None, action: str, expected: str
) -> None:
    rig.window.present = False
    rig.write_profile(declarations={"client_does_not_auto_confirm": True})
    async with connected(rig, elicitation=answering(content, action)) as client:
        status = await client.call("status")
        tab, observed = await open_and_observe(client, PAY_URL)
        result = await client.call(
            "click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Payer maintenant")
        )
    assert status["server"]["confirmation_channel"] == "elicitation"
    if expected == "granted":
        assert result["ok"] and result["server"]["gate"]["confirmation"] == "granted"
    else:
        assert code_of(result) == "confirmation_denied" and rig.gestures() == []


async def test_without_the_declaration_the_clients_confirmation_is_never_asked(rig: Rig) -> None:
    rig.window.present = False
    answering.asked.clear()  # type: ignore[attr-defined]
    async with connected(rig, elicitation=answering({"approve": True})) as client:
        status = await client.call("status")
        tab, observed = await open_and_observe(client, PAY_URL)
        result = await client.call(
            "click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Payer maintenant")
        )
    assert status["server"]["confirmation_channel"] == "none"
    assert code_of(result) == "confirmation_unavailable" and rig.gestures() == []
    assert answering.asked == []  # type: ignore[attr-defined]


async def test_a_client_that_cannot_ask_its_user_gets_no_question(rig: Rig) -> None:
    rig.window.present = False
    rig.write_profile(declarations={"client_does_not_auto_confirm": True})
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["server"]["confirmation_channel"] == "none"


# --- status -----------------------------------------------------------------------------------------


async def test_status_gives_the_entries_as_written_and_does_not_expand_a_wildcard(rig: Rig) -> None:
    rig.write_profile(
        allowed_origins=[ORIGIN, "https://*.example.test"],
        confirm_each_change=["https://*.example.test"],
    )
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["server"]["allowed_origins"] == [
        {"origin": ORIGIN, "confirm_each_change": True, "local_model_only": False},
        {"origin": "https://*.example.test", "confirm_each_change": True, "local_model_only": False},
    ]


async def test_status_says_the_sites_the_declarations_and_the_channel(rig: Rig) -> None:
    rig.write_profile(
        allowed_origins=[ORIGIN, OTHER],
        confirm_each_change=[OTHER],
        local_model_origins=[OTHER],
        declarations={"local_model_client": True},
    )
    async with connected(rig) as client:
        status = await client.call("status")
    server = status["server"]
    assert server["allowed_origins"] == [
        {"origin": ORIGIN, "confirm_each_change": False, "local_model_only": False},
        {"origin": OTHER, "confirm_each_change": True, "local_model_only": True},
    ]
    assert server["declarations"] == {"local_model_client": True, "client_does_not_auto_confirm": False}
    assert server["confirmation_channel"] == "native"
    assert server["encrypted_directory"] == {"state": "mounted"}
    assert server["profile_encrypted"] is False and "profile_encrypted_note" in server
    assert server["counters"]["max_actions"] == 200 and server["server_version"]
    assert "secrets_dir" not in json.dumps(server) and str(rig.paths.mount_point) not in json.dumps(server)


async def test_status_sees_a_browser_profile_kept_in_the_volume(rig: Rig) -> None:
    profile_dir = rig.paths.project / "browser-profile" / "chromium"
    rig.write_profile(browser={"family": "chromium", "profile_dir": str(profile_dir), "debug_port": rig.fake.port})
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["server"]["profile_encrypted"] is True


async def test_status_reports_a_browser_that_does_not_answer(rig: Rig) -> None:
    await rig.fake.stop()
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["ok"] and status["server"]["browser"]["attached"] is False
    assert status["server"]["browser"]["code"] in ("browser_unreachable", "debug_endpoint_undetermined")


async def test_status_names_the_assistant_command_that_lifts_each_state_of_the_volume(rig: Rig) -> None:
    async with connected(rig) as client:
        mounted = (await client.call("status"))["server"]["encrypted_directory"]
        rig.close_volume()
        closed = (await client.call("status"))["server"]["encrypted_directory"]
        (rig.crypt / "gocryptfs.conf").unlink()
        never = (await client.call("status"))["server"]["encrypted_directory"]
        rig.write_profile(secrets_crypt_dir=None)
        absent = (await client.call("status"))["server"]["encrypted_directory"]
    assert mounted == {"state": "mounted"} and "next_command" not in mounted
    assert closed["next_command"] == f"{SETUP} open"
    assert never["state"] == "never_initialized" and never["next_command"] == f"{SETUP} check"
    assert absent["state"] == "closed_or_absent" and absent["next_command"] == f"{SETUP} check"


async def test_status_of_a_browser_that_does_not_answer_adds_the_browser_command(rig: Rig) -> None:
    await rig.fake.stop()
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["server"]["browser"]["next_command"] == f"{SETUP} browser"
    assert status["server"]["encrypted_directory"] == {"state": "mounted"}


async def test_status_adds_no_command_to_an_attached_browser(rig: Rig) -> None:
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["server"]["browser"]["attached"] is True and "next_command" not in status["server"]["browser"]


async def test_the_commands_status_gives_name_no_file_of_the_volume_and_no_key(rig: Rig) -> None:
    texts = []
    async with connected(rig) as client:
        rig.close_volume()
        texts.append(json.dumps((await client.call("status"))["server"]))
        (rig.crypt / "gocryptfs.conf").unlink()
        texts.append(json.dumps((await client.call("status"))["server"]))
        rig.remove_profile()
        texts.append(json.dumps((await client.call("status"))["server"]))
    for text in texts:
        assert SETUP in text
        for forbidden in ("gocryptfs.conf", "gocryptfs.diriv", "credentials", "secrets_crypt_dir"):
            assert forbidden not in text


# --- boîtes de dialogue et avis par les outils ------------------------------------------------------


async def test_accepting_an_armed_dialog_is_refused_and_dismissing_it_works(rig: Rig) -> None:
    async with connected(rig) as client:
        tab, _observed = await open_and_observe(client, URL)
        target = next(t for t, info in rig.browser.targets.items() if info.get("url") == URL)
        await rig.browser.open_dialog(target, "Supprimer définitivement ?")
        seen = await client.call("observe", tab_id=tab)
        accepted = await client.call("handle_dialog", tab_id=tab, accept=True)
        dismissed = await client.call("handle_dialog", tab_id=tab, accept=False)
    assert seen["ok"] and seen["server"]["dialog_open"] is True
    assert seen["page_content"] == {"dialog": {"type": "confirm", "message": "Supprimer définitivement ?"}}
    assert code_of(accepted) == "capture_unavailable"
    assert accepted["server"]["gate"] == {"red_list": True, "confirmation": "requested"}
    assert dismissed["ok"] and rig.browser.accepted == [False]
    assert rig.window.questions() == []


async def test_the_notices_of_an_action_reach_the_model_as_the_action_wrote_them(rig: Rig) -> None:
    rig.timings["settle_start_s"] = 0.3
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, URL)

        async def open_window(target_id: str, hit: int | None) -> None:
            await rig.browser.announce("Reçu", ORIGIN + "/receipt", opener_id=target_id)

        rig.browser.on_click = open_window
        clicked = await client.call("click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Envoyer"))
        listed = await client.call("list_tabs")
    assert clicked["ok"] and clicked["server"]["notices"], clicked["server"]
    assert len(listed["server"]["tabs"]) == 2 and any(entry["adopted"] for entry in listed["server"]["tabs"])


# --- ce que fait le SDK de lui-même -----------------------------------------------------------------


async def test_the_sdk_alone_would_ignore_an_unknown_parameter() -> None:
    # Mesuré sur le SDK employé : un outil enregistré de la façon ordinaire laisse tomber un paramètre qu'il
    # ne connaît pas et s'exécute. Le serveur contrôle lui-même les arguments, ce que les tests ci-dessus montrent le refuser.
    plain = FastMCP(name="plain")
    seen: list[dict[str, Any]] = []

    @plain.tool()
    def click(mark: int) -> str:
        """Un outil simple."""
        seen.append({"mark": mark})
        return "done"

    async with create_connected_server_and_client_session(plain) as session:
        result = await session.call_tool("click", {"mark": 1, "approved": True})
    assert result.isError is False and seen == [{"mark": 1}]


# --- les autres outils, et ce que disent leurs descriptions -----------------------------------------


async def test_navigate_moves_the_tab_and_makes_its_marks_stale(rig: Rig) -> None:
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, URL)
        moved = await client.call("navigate", tab_id=tab, url=PAY_URL, intent="go to the checkout")
        stale = await client.call("click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Envoyer"))
        scheme = await client.call("navigate", tab_id=tab, url="file:///etc/passwd")
    assert moved["ok"] and moved["server"]["origin"] == ORIGIN and moved["server"]["loaded"] is True
    assert code_of(stale) == "snapshot_stale" and rig.gestures() == []
    assert code_of(scheme) == "forbidden_scheme"


async def test_save_evidence_gives_a_path_inside_the_volume_never_the_volume_itself(rig: Rig) -> None:
    async with connected(rig) as client:
        tab, _observed = await open_and_observe(client, URL)
        saved = await client.call("save_evidence", tab_id=tab, label="../../reçu")
    path = saved["server"]["evidence_path"]
    assert saved["ok"] and path.startswith("evidence/") and not path.startswith("/")
    assert str(rig.paths.mount_point) not in json.dumps(saved)
    assert (rig.paths.project / path).is_file()


async def test_the_descriptions_say_what_a_dialog_and_a_late_download_mean(rig: Rig) -> None:
    async with connected(rig) as client:
        tools = {tool.name: tool.description or "" for tool in (await client.session.list_tools()).tools}
    for name in ("click", "fill", "select_option", "press_key", "scroll"):
        assert "element_obscured" in tools[name] and "handle_dialog" in tools[name], name
    assert "capture_unavailable" in tools["handle_dialog"] and "answer the dialog themselves" in tools["handle_dialog"]
    assert "half a" in tools["click"] and "second after a load ends" in tools["click"]
