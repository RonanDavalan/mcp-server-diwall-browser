"""Les actions rejouées contre un vrai navigateur de la famille Chromium, avec un faux canal de confirmation."""

from __future__ import annotations

import asyncio
import json
import shutil
import stat
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import FAKE_PASSWORD, FakeMount, VolumePaths, mounted_volume, write_credential
from fixtures.action_support import FakeChannel, FakeClock, make_profile
from fixtures.browser_launcher import SKIP_REASON, LaunchedBrowser, launch_browser
from fixtures.download_probe import holds
from fixtures.page_server import LATE_FILE_BODY, SLOW_FILE_SIZE, PageServer
from fixtures.raw_cdp import RawSession

from mcp_server_diwall_browser.actions import ActionContext, Actions
from mcp_server_diwall_browser.browser import BrowserAttachment, Tab, TabRegistry
from mcp_server_diwall_browser.confirm import ConfirmationDecision
from mcp_server_diwall_browser.evidence import DownloadManager, EvidenceWriter
from mcp_server_diwall_browser.gate import Pacer
from mcp_server_diwall_browser.journal import Journal
from mcp_server_diwall_browser.mount import Volume
from mcp_server_diwall_browser.operator_profile import Profile
from mcp_server_diwall_browser.output import ToolError, ToolOutput, tool
from mcp_server_diwall_browser.perception import Perception, Snapshot

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("browser-home")


@pytest.fixture(scope="module")
def launched(browser_home: Path) -> Iterator[LaunchedBrowser]:
    browser = launch_browser(home=str(browser_home))
    if browser is None:
        pytest.skip(SKIP_REASON)
    yield browser
    browser.stop()


@pytest.fixture(scope="module")
def pages() -> Iterator[PageServer]:
    with PageServer() as server:
        yield server


@pytest.fixture(scope="module")
def other_pages() -> Iterator[PageServer]:
    with PageServer() as server:
        yield server


@dataclass
class Rig:
    launched: LaunchedBrowser
    pages: PageServer
    attachment: BrowserAttachment
    registry: TabRegistry
    perception: Perception
    channel: FakeChannel
    paths: VolumePaths
    mount: FakeMount
    volume: Volume
    profile: Profile
    context: ActionContext
    actions: Actions

    async def open(self, name: str, pages: PageServer | None = None) -> tuple[Tab, Snapshot]:
        tab = await self.registry.open_tab((pages or self.pages).url(name))
        return tab, await self.observe(tab)

    async def observe(self, tab: Tab) -> Snapshot:
        observation = await self.perception.observe(tab, image=False)
        assert observation.snapshot is not None
        return observation.snapshot

    async def tree(self, tab: Tab) -> list[str]:
        observation = await self.perception.observe(tab, image=False)
        assert observation.page_content is not None
        return list(observation.page_content["tree"])

    def raw(self, tab: Tab) -> RawSession:
        return RawSession(self.launched.version_info["webSocketDebuggerUrl"], tab.target_id)


async def attach(launched: LaunchedBrowser) -> BrowserAttachment:
    profile = SimpleNamespace(browser=SimpleNamespace(family="chromium", profile_dir=launched.profile_dir, debug_port=launched.port))
    return await BrowserAttachment.attach(profile)


@pytest.fixture
async def rig(launched: LaunchedBrowser, pages: PageServer, other_pages: PageServer, tmp_path: Path) -> AsyncIterator[Rig]:
    attachment = await attach(launched)
    registry = TabRegistry(attachment.connection)
    paths, mount, volume = mounted_volume(tmp_path)
    clock = FakeClock()
    holder: dict[str, Rig] = {}

    def profile() -> Profile:
        return holder["rig"].profile

    perception = Perception(registry, allowed_origins=lambda: profile().allowed_origins)
    channel = FakeChannel()
    context = ActionContext(
        profile=profile,
        registry=registry,
        perception=perception,
        pacer=Pacer(lambda: profile().limits, clock=clock, sleep=clock.sleep),
        channel=channel,
        journal=Journal(volume, lambda: ("test-client", "1.0")),
        volume=volume,
        evidence=EvidenceWriter(volume),
        downloads=DownloadManager(volume, attachment.connection),
        settle_start_s=0.3,
    )
    holder["rig"] = Rig(
        launched=launched,
        pages=pages,
        attachment=attachment,
        registry=registry,
        perception=perception,
        channel=channel,
        paths=paths,
        mount=mount,
        volume=volume,
        profile=make_profile(tmp_path, allowed=(pages.origin, other_pages.origin)),
        context=context,
        actions=Actions(context),
    )
    yield holder["rig"]
    for info in await holder["rig"].registry.list_tabs():
        await holder["rig"].registry.close_tab(info.tab_id)
    await holder["rig"].attachment.close()


def mark_of(snapshot: Snapshot, name: str) -> int:
    for element in snapshot.elements:
        if element.name == name:
            return element.mark
    raise AssertionError(f"no mark named {name!r}")


async def until_true(probe: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not await probe():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


# --- identité et dérive -----------------------------------------------------------------------------


async def test_an_insertion_before_the_target_does_not_move_the_click(rig: Rig) -> None:
    tab, snapshot = await rig.open("drift.html")
    async with rig.raw(tab) as raw:
        await raw.evaluate("insertBefore()")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Second button"))
    assert '  StaticText "second clicked"' in await rig.tree(tab)


async def test_a_target_replaced_by_a_clone_is_stale_and_nothing_is_clicked(rig: Rig) -> None:
    tab, snapshot = await rig.open("drift.html")
    async with rig.raw(tab) as raw:
        await raw.evaluate("var s = document.getElementById('second'); s.replaceWith(s.cloneNode(true));")
    with pytest.raises(ToolError) as caught:
        await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Second button"))
    assert caught.value.code == "element_stale"
    assert '  StaticText "Nothing clicked"' in await rig.tree(tab)


async def test_a_covered_button_is_not_clicked(rig: Rig) -> None:
    tab, snapshot = await rig.open("obscured.html")
    with pytest.raises(ToolError) as caught:
        await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Envoyer"))
    assert caught.value.code == "element_obscured"
    assert '  StaticText "Nothing clicked"' in await rig.tree(tab)


# --- la porte ---------------------------------------------------------------------------------------


async def test_pay_now_is_asked_and_done_only_with_consent(rig: Rig) -> None:
    tab, snapshot = await rig.open("payment.html")
    rig.channel.decision = ConfirmationDecision.DENIED
    with pytest.raises(ToolError) as caught:
        await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Payer maintenant"))
    assert caught.value.code == "confirmation_denied"
    assert '  StaticText "Nothing done"' in await rig.tree(tab)

    rig.channel.decision = ConfirmationDecision.GRANTED
    snapshot = await rig.observe(tab)
    outcome = await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Payer maintenant"))
    assert '  StaticText "Payment submitted"' in await rig.tree(tab)
    question = rig.channel.texts[-1]
    assert f"Origine : {rig.pages.origin}" in question and 'button "Payer maintenant"' in question
    assert "42,00 €" in question and "card_field_on_page" in question
    assert outcome.gate == {"red_list": True, "confirmation": "granted"}
    evidence = sorted((rig.paths.project / "evidence").rglob("*.png"))
    assert {path.name for path in evidence} == {"before.png", "after.png"}
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in evidence)


async def test_enter_in_the_payment_form_is_armed(rig: Rig) -> None:
    tab, snapshot = await rig.open("payment.html")
    rig.channel.decision = ConfirmationDecision.DENIED
    with pytest.raises(ToolError) as caught:
        await rig.actions.press_key(tab.tab_id, "Enter", snapshot_id=snapshot.snapshot_id, mark=mark_of(snapshot, "Numéro de carte"))
    assert caught.value.code == "confirmation_denied"
    assert '  StaticText "Nothing done"' in await rig.tree(tab)


@pytest.mark.parametrize(
    ("page", "name", "done"),
    [("delete.html", "Supprimer mon compte", "Account deleted"), ("secret.html", "Changer le mot de passe", "Password changed")],
)
async def test_deletion_and_secret_change_are_asked(rig: Rig, page: str, name: str, done: str) -> None:
    tab, snapshot = await rig.open(page)
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, name))
    assert len(rig.channel.texts) == 1
    assert f'  StaticText "{done}"' in await rig.tree(tab)


async def test_the_pay_button_of_the_hostile_page_is_asked(rig: Rig) -> None:
    tab, snapshot = await rig.open("hostile.html")
    rig.channel.decision = ConfirmationDecision.DENIED
    with pytest.raises(ToolError):
        await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Pay"))
    assert '  StaticText "Not paid"' in await rig.tree(tab)


# --- actions ordinaires -----------------------------------------------------------------------------


async def test_click_fill_and_enter_on_a_comment_form_ask_nothing(rig: Rig) -> None:
    tab, snapshot = await rig.open("neutral.html")
    outcome = await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Pseudonyme"), text="alice")
    assert outcome.typed_marker == "<typed>"
    fresh = await rig.observe(tab)
    nickname = next(e for e in fresh.elements if e.name == "Pseudonyme")
    assert nickname.public["value"] == "alice"
    await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Commentaire"), text="Bonjour")
    await rig.actions.press_key(tab.tab_id, "Enter", snapshot_id=snapshot.snapshot_id, mark=mark_of(snapshot, "Pseudonyme"))
    assert '  StaticText "Sent by alice"' in await rig.tree(tab)
    await rig.actions.fill(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Pseudonyme"), text="bob")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Envoyer"))
    assert '  StaticText "Sent by bob"' in await rig.tree(tab)
    assert rig.channel.texts == []


async def test_an_option_is_chosen_with_its_change_event(rig: Rig) -> None:
    tab, snapshot = await rig.open("neutral.html")
    await rig.actions.select_option(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Pays"), "Belgique")
    assert '  StaticText "Country changed to be"' in await rig.tree(tab)
    with pytest.raises(ToolError) as caught:
        await rig.actions.select_option(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Pays"), "Atlantide")
    assert caught.value.code == "option_not_found"


async def test_scrolling_moves_the_viewport_and_stops_at_the_bottom(rig: Rig) -> None:
    tab, _snapshot = await rig.open("long.html")

    async def scroll_y() -> float:
        metrics = await rig.registry.read(tab, "Page.getLayoutMetrics")
        return float(metrics["cssVisualViewport"]["pageY"])

    await rig.actions.scroll(tab.tab_id, dy=400)
    await until_true(lambda: _above(scroll_y, 0))
    await rig.actions.scroll(tab.tab_id, to="bottom")
    metrics = await rig.registry.read(tab, "Page.getLayoutMetrics")
    bottom = metrics["cssContentSize"]["height"] - metrics["cssVisualViewport"]["clientHeight"]
    assert abs(await scroll_y() - bottom) <= 2


async def _above(read: Any, value: float) -> bool:
    return bool(await read() > value)


# --- boîtes de dialogue -----------------------------------------------------------------------------


async def test_dialogs_opened_by_a_click_are_answered_through_the_gate(rig: Rig) -> None:
    tab, snapshot = await rig.open("dialog.html")
    outcome = await asyncio.wait_for(rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Ask for confirmation")), 10)
    assert outcome.server["dialog_open"] is True
    await rig.actions.handle_dialog(tab.tab_id, True)
    assert '  StaticText "Accepted"' in await rig.tree(tab)
    assert rig.channel.texts == []

    snapshot = await rig.observe(tab)
    outcome = await asyncio.wait_for(rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Archive settings")), 10)
    assert outcome.server["dialog_open"] is True
    with pytest.raises(ToolError) as caught:
        await rig.actions.handle_dialog(tab.tab_id, True)
    # Armé par son message ; tant que la boîte de dialogue est ouverte la page ne peut pas être capturée,
    # et sans preuve rien d'irréversible n'est accepté.
    assert caught.value.code == "capture_unavailable"
    assert caught.value.extra_server["gate"] == {"red_list": True, "confirmation": "requested"}
    with pytest.raises(ToolError) as blocked:
        await asyncio.wait_for(rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Ask for confirmation")), 2)
    assert blocked.value.code == "element_obscured" and blocked.value.action_expected == "handle_dialog"
    await rig.actions.handle_dialog(tab.tab_id, False)
    assert '  StaticText "Kept"' in await rig.tree(tab)


# --- identifiants -----------------------------------------------------------------------------------


@tool("fill")
async def fill_tool(actions: Actions, *args: Any, **kwargs: Any) -> ToolOutput:
    outcome = await actions.fill(*args, **kwargs)
    return ToolOutput(server={**outcome.server, "typed": outcome.typed_marker})


async def test_a_credential_is_typed_on_its_origin_only(rig: Rig, other_pages: PageServer) -> None:
    write_credential(rig.paths, "local.json", allowed_origins=[rig.pages.origin])
    tab, snapshot = await rig.open("login.html")
    envelope, _image = await fill_tool(rig.actions, tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Mot de passe"), credential_ref={"key": "password"})
    async with rig.raw(tab) as raw:
        typed = await raw.evaluate("document.getElementById('password').value")
    typed_right = typed == FAKE_PASSWORD
    assert typed_right, "the password was not typed"
    leaked = FAKE_PASSWORD in json.dumps(envelope)
    assert envelope["ok"] is True and not leaked, "the password appears in the result"

    other, other_snapshot = await rig.open("login.html", other_pages)
    with pytest.raises(ToolError) as caught:
        await rig.actions.fill(other.tab_id, other_snapshot.snapshot_id, mark_of(other_snapshot, "Mot de passe"), credential_ref={"key": "password"})
    assert caught.value.code == "credential_not_bound"
    async with rig.raw(other) as raw:
        assert await raw.evaluate("document.getElementById('password').value") == ""


# --- téléchargements --------------------------------------------------------------------------------


async def test_a_downloaded_file_lands_in_the_volume_under_a_safe_name(rig: Rig) -> None:
    tab, snapshot = await rig.open("download.html")
    outcome = await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Télécharger la facture"))
    downloads = rig.paths.project / "downloads"

    async def settled() -> bool:
        return any(path.suffix == ".txt" for path in downloads.rglob("*"))

    await until_true(settled)
    [saved] = [path for path in downloads.rglob("*") if path.is_file()]
    assert saved.suffix == ".txt" and "/" not in saved.name and not saved.name.startswith(".")
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600 and stat.S_IMODE(saved.parent.stat().st_mode) == 0o700
    notices = outcome.server["notices"] + rig.context.downloads.drain_notices()
    assert f"fichier téléchargé dans le répertoire chiffré : {saved.name}" in notices


async def test_a_download_begun_by_the_click_ends_in_the_volume_after_the_setting_is_closed(rig: Rig) -> None:
    # Fait mesuré figé ici : fermer le réglage avec ``deny`` une fois l'action terminée n'annule pas un
    # téléchargement déjà commencé, mais un téléchargement qui n'a pas encore commencé est refusé, si bien
    # que l'action attend un instant celui qu'un chargement peut produire.
    tab, snapshot = await rig.open("download-slow.html")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Télécharger l'archive"))
    downloads = rig.paths.project / "downloads"

    async def complete() -> bool:
        return any(holds(path, lambda file: file.stat().st_size == SLOW_FILE_SIZE) for path in downloads.rglob("*"))

    await until_true(complete, timeout=10)


async def test_a_download_that_begins_a_moment_after_the_click_ends_in_the_volume(rig: Rig) -> None:
    # La réponse commence après le retour du geste : l'attente du début d'un téléchargement est ce qui
    # garde le réglage ouvert assez longtemps pour lui.
    tab, snapshot = await rig.open("download-late.html")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Télécharger le rapport"))
    downloads = rig.paths.project / "downloads"

    async def complete() -> bool:
        return any(holds(path, lambda file: file.read_bytes() == LATE_FILE_BODY) for path in downloads.rglob("*"))

    await until_true(complete, timeout=10)


async def test_a_page_download_between_two_actions_goes_nowhere(rig: Rig, browser_home: Path) -> None:
    tab, snapshot = await rig.open("download.html")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Télécharger la facture"))
    downloads = rig.paths.project / "downloads"

    async def saved() -> bool:
        return len([p for p in downloads.rglob("*") if p.is_file()]) == 1

    await until_true(saved)
    # Le volume se ferme après l'action : ses répertoires n'y sont plus.
    shutil.rmtree(downloads)
    default_dir = browser_home / "Downloads"
    before = set(default_dir.iterdir()) if default_dir.exists() else set()
    async with rig.raw(tab) as raw:
        await raw.evaluate("document.getElementById('invoice').click()", gesture=True)
    await asyncio.sleep(1.5)
    assert not downloads.exists(), "the browser created the download directory outside the volume"
    assert (set(default_dir.iterdir()) if default_dir.exists() else set()) == before


async def test_counter_proof_a_setting_left_open_writes_outside_the_volume(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def keep_open(self: DownloadManager) -> None:
        return None

    monkeypatch.setattr(DownloadManager, "forbid", keep_open)
    tab, snapshot = await rig.open("download.html")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Télécharger la facture"))
    downloads = rig.paths.project / "downloads"

    async def saved() -> bool:
        return len([p for p in downloads.rglob("*") if p.is_file()]) == 1

    await until_true(saved)
    shutil.rmtree(downloads)
    async with rig.raw(tab) as raw:
        await raw.evaluate("document.getElementById('invoice').click()", gesture=True)

    async def recreated() -> bool:
        return downloads.exists() and any(p.is_file() for p in downloads.rglob("*"))

    await until_true(recreated)


async def test_the_download_setting_ends_with_the_connection_that_set_it(
    rig: Rig, launched: LaunchedBrowser, browser_home: Path
) -> None:
    # Fait mesuré figé ici : une fois fermée la connexion qui a posé le comportement de téléchargement,
    # le navigateur enregistre de nouveau avec ses valeurs par défaut. Le serveur le pose donc avant chaque geste, jamais une fois pour toutes.
    tab, snapshot = await rig.open("download.html")
    await rig.actions.click(tab.tab_id, snapshot.snapshot_id, mark_of(snapshot, "Télécharger la facture"))
    directory = rig.paths.project / "downloads"

    async def files_in_volume() -> bool:
        return len([p for p in directory.rglob("*") if p.is_file()]) == 1

    await until_true(files_in_volume)
    target_id = tab.target_id
    default_dir = browser_home / "Downloads"
    before = set(default_dir.iterdir()) if default_dir.exists() else set()
    await rig.attachment.close()

    second = await attach(launched)
    try:
        async with RawSession(launched.version_info["webSocketDebuggerUrl"], target_id) as raw:
            await raw.evaluate("document.getElementById('invoice').click()", gesture=True)

        async def in_default_dir() -> bool:
            return default_dir.exists() and len(set(default_dir.iterdir()) - before) == 1

        await until_true(in_default_dir)
        assert len([p for p in directory.rglob("*") if p.is_file()]) == 1
    finally:
        await second.close()
        rig.attachment = await attach(launched)
        rig.registry = TabRegistry(rig.attachment.connection)
