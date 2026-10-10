"""Des faits mesurés contre un vrai navigateur de la famille Chromium.

La perception et les actions sont conçues sur ce que le navigateur rend vraiment. Ces tests rejouent ces
mesures : quand une nouvelle version du navigateur en change une, le test échoue, et la conception doit être réexaminée.
"""

from __future__ import annotations

import base64
import json
import os
import struct
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fixtures.browser_launcher import SKIP_REASON, LaunchedBrowser, launch_browser
from fixtures.fake_cdp import until
from fixtures.page_server import PageServer
from websockets.asyncio.client import connect

from mcp_server_diwall_browser.browser import (
    BrowserAttachment,
    Tab,
    TabRegistry,
    check_debugger_url,
    check_profile_dir,
    parse_proc_net_tcp,
    read_proc_net_tcp,
    verify_loopback_listening,
)
from mcp_server_diwall_browser.cdp import CdpConnection
from mcp_server_diwall_browser.output import ToolError

pytestmark = pytest.mark.browser

TYPED_PASSWORD = "diwall-typed-value-7"
CHROMIUM_PRODUCTS = {"Chrome", "HeadlessChrome", "Chromium", "HeadlessChromium"}


@pytest.fixture(scope="module")
def launched() -> Iterator[LaunchedBrowser]:
    browser = launch_browser()
    if browser is None:
        pytest.skip(SKIP_REASON)
    yield browser
    browser.stop()


@pytest.fixture(scope="module")
def pages() -> Iterator[tuple[PageServer, PageServer]]:
    with PageServer() as first, PageServer() as second:
        yield first, second


class RecordingConnection(CdpConnection):
    """Le client de production, avec le relevé des méthodes qu'on lui a demandé d'envoyer."""

    def __init__(self) -> None:
        super().__init__()
        self.methods: list[str] = []

    async def send(self, method: str, params: Any = None, **kwargs: Any) -> dict[str, Any]:
        self.methods.append(method)
        return await super().send(method, params, **kwargs)


@pytest.fixture
async def attachment(launched: LaunchedBrowser) -> AsyncIterator[BrowserAttachment]:
    profile = SimpleNamespace(
        browser=SimpleNamespace(family="chromium", profile_dir=launched.profile_dir, debug_port=launched.port)
    )
    attached = await BrowserAttachment.attach(profile, cdp_factory=RecordingConnection)
    yield attached
    await attached.close()


@pytest.fixture
async def registry(attachment: BrowserAttachment) -> AsyncIterator[TabRegistry]:
    tabs = TabRegistry(attachment.connection)
    yield tabs
    for info in await tabs.list_tabs():
        await tabs.close_tab(info.tab_id)


@pytest.fixture
async def basic_tab(registry: TabRegistry, pages: tuple[PageServer, PageServer]) -> Tab:
    tab = await registry.open_tab(pages[0].url("basic.html"))
    assert registry.drain_notices() == []
    return tab


async def call(attachment: BrowserAttachment, tab: Tab, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return await attachment.connection.send(method, params, session_id=tab.session_id)


class Snapshot:
    """Assistants de recherche dans un résultat de DOMSnapshot.captureSnapshot."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.strings: list[str] = raw["strings"]
        self.documents: list[dict[str, Any]] = raw["documents"]

    def attributes(self, document: int, index: int) -> dict[str, str]:
        pairs = self.documents[document]["nodes"]["attributes"][index]
        return {self.strings[pairs[i]]: self.strings[pairs[i + 1]] for i in range(0, len(pairs), 2)}

    def index_of(self, element_id: str, document: int = 0) -> int:
        nodes = self.documents[document]["nodes"]
        for index in range(len(nodes["backendNodeId"])):
            if self.attributes(document, index).get("id") == element_id:
                return index
        raise LookupError(element_id)

    def backend_id(self, element_id: str) -> int:
        return self.documents[0]["nodes"]["backendNodeId"][self.index_of(element_id)]

    def bounds(self, element_id: str) -> list[float] | None:
        layout = self.documents[0]["layout"]
        index = self.index_of(element_id)
        if index not in layout["nodeIndex"]:
            return None
        return layout["bounds"][layout["nodeIndex"].index(index)]

    def rare(self, field: str, index: int, document: int = 0) -> Any:
        data = self.documents[document]["nodes"][field]
        if index not in data["index"]:
            return None
        return data["value"][data["index"].index(index)] if "value" in data else True


async def snapshot(attachment: BrowserAttachment, tab: Tab) -> Snapshot:
    raw = await call(
        attachment,
        tab,
        "DOMSnapshot.captureSnapshot",
        {
            "computedStyles": ["display", "visibility", "opacity", "font-size"],
            "includeDOMRects": True,
            "includePaintOrder": True,
        },
    )
    return Snapshot(raw)


# --- 1. le navigateur et son point d'accès de débogage ----------------------------------------------


async def test_fact_1_browser_is_chromium_and_endpoint_is_loopback(
    launched: LaunchedBrowser, attachment: BrowserAttachment
) -> None:
    described = attachment.describe()
    assert described["product"] in CHROMIUM_PRODUCTS
    assert described["version"].split(".")[0].isdigit()
    url = launched.version_info["webSocketDebuggerUrl"]
    assert check_debugger_url(url, launched.port) == url
    assert url.startswith(f"ws://127.0.0.1:{launched.port}/devtools/browser/")


# --- 2. l'arbre d'accessibilité ---------------------------------------------------------------------


async def test_fact_2_accessibility_tree_fields_and_hidden_buttons(
    attachment: BrowserAttachment, basic_tab: Tab
) -> None:
    nodes = (await call(attachment, basic_tab, "Accessibility.getFullAXTree"))["nodes"]
    for node in nodes:
        assert {"nodeId", "role", "ignored"} <= set(node), node.get("nodeId")
        if not node["ignored"]:
            assert {"name", "properties"} <= set(node), node["nodeId"]
        # Les séries de texte sont les seuls nœuds sans nœud DOM derrière eux.
        if node["role"].get("value") != "InlineTextBox":
            assert "backendDOMNodeId" in node, node["nodeId"]
    # Le cadre n'est nommé que sur la racine du document, pas sur chaque nœud.
    with_frame = [node for node in nodes if "frameId" in node]
    assert [node["role"]["value"] for node in with_frame] == ["RootWebArea"]
    assert with_frame[0]["frameId"] == basic_tab.main_frame_id

    buttons = {
        node["name"]["value"]: node
        for node in nodes
        if not node["ignored"] and node["role"].get("value") == "button"
    }
    assert "Hidden button" not in buttons
    # Fait à garder : un bouton entièrement transparent est dans l'arbre, avec son nom.
    # L'arbre seul ne dit pas ce que l'humain voit.
    assert "Transparent button" in buttons
    assert "Sign in" in buttons
    # L'arbre du cadre principal ne descend pas dans le cadre srcdoc.
    assert "Inner button" not in buttons


# --- 3. l'instantané du DOM -------------------------------------------------------------------------


async def test_fact_3_dom_snapshot_fields_layout_clickability_and_frames(
    attachment: BrowserAttachment, basic_tab: Tab
) -> None:
    snap = await snapshot(attachment, basic_tab)
    main = snap.documents[0]
    for field in ("backendNodeId", "attributes", "isClickable", "inputValue", "contentDocumentIndex"):
        assert field in main["nodes"], field
    assert {"bounds", "nodeIndex"} <= set(main["layout"])
    assert len(main["layout"]["bounds"]) == len(main["layout"]["nodeIndex"])

    assert snap.bounds("hidden-button") is None
    assert snap.bounds("transparent-button") is not None
    assert snap.rare("isClickable", snap.index_of("clickable-div")) is True
    assert snap.rare("isClickable", snap.index_of("submit-button")) is True

    assert len(snap.documents) == 2
    frame_index = snap.index_of("inner-frame")
    assert snap.rare("contentDocumentIndex", frame_index) == 1
    inner_texts = [snap.strings[i] for i in snap.documents[1]["nodes"]["nodeValue"] if i >= 0]
    assert "Inner document" in inner_texts


# --- 4. le mot de passe tapé est dans l'instantané --------------------------------------------------


async def test_fact_4_password_value_is_in_the_snapshot(attachment: BrowserAttachment, basic_tab: Tab) -> None:
    before = await snapshot(attachment, basic_tab)
    password = before.backend_id("password")
    await call(attachment, basic_tab, "DOM.focus", {"backendNodeId": password})
    await call(attachment, basic_tab, "Input.insertText", {"text": TYPED_PASSWORD})
    after = await snapshot(attachment, basic_tab)
    value_index = after.rare("inputValue", after.index_of("password"))
    # Fait à garder : la valeur d'un champ de mot de passe est en clair dans l'instantané ;
    # elle doit être neutralisée avant que quoi que ce soit quitte le serveur.
    assert after.strings[value_index] == TYPED_PASSWORD


# --- 5. une capture de la fenêtre d'affichage visuelle ----------------------------------------------


async def test_fact_5_screenshot_has_the_size_of_the_css_viewport(attachment: BrowserAttachment, basic_tab: Tab) -> None:
    metrics = await call(attachment, basic_tab, "Page.getLayoutMetrics")
    viewport = metrics["cssVisualViewport"]
    clip = {
        "x": viewport["pageX"],
        "y": viewport["pageY"],
        "width": viewport["clientWidth"],
        "height": viewport["clientHeight"],
        "scale": 1,
    }
    shot = await call(attachment, basic_tab, "Page.captureScreenshot", {"format": "png", "clip": clip})
    png = base64.b64decode(shot["data"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (viewport["clientWidth"], viewport["clientHeight"])


# --- 6. le nœud sous un point -----------------------------------------------------------------------


async def test_fact_6_node_at_the_centre_of_the_password_field(attachment: BrowserAttachment, basic_tab: Tab) -> None:
    snap = await snapshot(attachment, basic_tab)
    x, y, width, height = snap.bounds("password") or [0, 0, 0, 0]
    assert width > 0 and height > 0
    described = await call(attachment, basic_tab, "DOM.describeNode", {"backendNodeId": snap.backend_id("password")})
    located = await call(
        attachment, basic_tab, "DOM.getNodeForLocation", {"x": int(x + width / 2), "y": int(y + height / 2)}
    )
    assert located["backendNodeId"] == described["node"]["backendNodeId"] == snap.backend_id("password")


# --- 7. une session au niveau du navigateur voit tous les onglets -----------------------------------


async def raw_call(ws: Any, message_id: int, method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
    if session_id is not None:
        message["sessionId"] = session_id
    await ws.send(json.dumps(message))
    while True:
        answer = json.loads(await ws.recv())
        if answer.get("id") == message_id:
            return answer["result"]


async def test_fact_7_a_raw_browser_session_sees_more_targets_than_the_server_owns(
    launched: LaunchedBrowser, registry: TabRegistry, basic_tab: Tab
) -> None:
    # Target.getTargets n'est pas dans la liste de production : le lire hors de cdp.py.
    async with connect(launched.version_info["webSocketDebuggerUrl"], proxy=None, max_size=2**26) as ws:
        targets = (await raw_call(ws, 1, "Target.getTargets"))["targetInfos"]
    pages_seen = [target for target in targets if target["type"] == "page"]
    owned = await registry.list_tabs()
    assert len(owned) == 1
    assert len(pages_seen) >= 2
    assert len(pages_seen) > len(owned)


# --- 8. contrôles de rattachement contre le vrai navigateur -----------------------------------------


async def test_fact_8_attachment_checks_pass_against_the_test_browser(
    launched: LaunchedBrowser, attachment: BrowserAttachment
) -> None:
    check_profile_dir("chromium", launched.profile_dir, os.path.expanduser("~"))
    tcp_text, tcp6_text = read_proc_net_tcp()
    verify_loopback_listening(launched.port, tcp_text=tcp_text, tcp6_text=tcp6_text)
    assert tcp_text is not None
    listening = [entry for entry in parse_proc_net_tcp(tcp_text, ipv6=False) if entry.port == launched.port and entry.state == "LISTEN"]
    assert [str(entry.address) for entry in listening] == ["127.0.0.1"]
    assert attachment.connection.methods[:2] == ["Browser.getVersion", "Target.setDiscoverTargets"]


# --- 9. navigation et origine -----------------------------------------------------------------------


async def test_fact_9_origin_follows_navigation_and_local_files_are_refused(
    attachment: BrowserAttachment, registry: TabRegistry, basic_tab: Tab, pages: tuple[PageServer, PageServer]
) -> None:
    first, second = pages
    assert (await registry.main_frame_info(basic_tab)).origin.canonical == first.origin  # type: ignore[union-attr]
    result = await registry.navigate(basic_tab, second.url("basic.html"))
    assert result.loaded is True
    frame = await registry.main_frame_info(basic_tab)
    assert frame.origin is not None and frame.origin.canonical == second.origin
    assert frame.url == second.url("basic.html")
    assert [info.origin for info in await registry.list_tabs()] == [second.origin]

    connection = attachment.connection
    assert isinstance(connection, RecordingConnection)
    sent_before = list(connection.methods)
    with pytest.raises(ToolError) as caught:
        await registry.navigate(basic_tab, "file:///etc/hostname")
    assert caught.value.code == "forbidden_scheme"
    assert connection.methods == sent_before
    with pytest.raises(ToolError):
        await registry.open_tab("file:///etc/hostname")
    assert connection.methods == sent_before


async def test_unreachable_address_gives_navigation_failed(registry: TabRegistry, basic_tab: Tab) -> None:
    # Le port 1 est sur la liste des ports dangereux du navigateur : refusé sans réseau.
    with pytest.raises(ToolError) as caught:
        await registry.navigate(basic_tab, "http://127.0.0.1:1/")
    assert caught.value.code == "navigation_failed"
    assert "net::ERR_UNSAFE_PORT" in caught.value.message


# --- adoption d'une fenêtre ouverte par une page possédée -------------------------------------------


async def test_a_window_opened_by_the_page_of_an_owned_tab_is_adopted(
    launched: LaunchedBrowser, registry: TabRegistry, basic_tab: Tab
) -> None:
    # La page ouvre la fenêtre comme après un clic (un geste est nécessaire, sinon le bloqueur de
    # fenêtres l'arrête) ; le script s'exécute hors de cdp.py.
    async with connect(launched.version_info["webSocketDebuggerUrl"], proxy=None, max_size=2**26) as ws:
        session = (await raw_call(ws, 1, "Target.attachToTarget", {"targetId": basic_tab.target_id, "flatten": True}))["sessionId"]
        await raw_call(
            ws, 2, "Runtime.evaluate", {"expression": "window.open('/basic.html'); 1", "userGesture": True}, session
        )
        notices: list[str] = []
        # L'avis arrive une fois l'onglet adopté rattaché et préparé.
        await until(lambda: bool(notices.extend(registry.drain_notices()) or notices), timeout=10)
    infos = await registry.list_tabs()
    assert [info.adopted for info in infos] == [False, True]
    [notice] = notices
    assert basic_tab.tab_id in notice and infos[1].tab_id in notice
