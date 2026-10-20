"""The three origin policies as the operator lives them: the server started by its entry point, a real
Chromium-family browser on a throw-away profile, and fake window programs in place of kdialog.

The pages come from one local server; a second origin is the same server under a public-looking name that the
browser resolves to 127.0.0.1 (a rule of its own command line), so no test touches the network.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import mounted_volume, write_profile
from fixtures.action_support import FakeChannel, FakeClock, FakeOriginWindow, FakeWindow, make_profile
from fixtures.browser_launcher import SKIP_REASON, LaunchedBrowser, launch_browser
from fixtures.page_server import PageServer
from mcp import ClientSession
from test_server_process import MODULE, stdio_session

from mcp_server_diwall_browser.actions import ActionContext, Actions
from mcp_server_diwall_browser.browser import BrowserAttachment, TabRegistry
from mcp_server_diwall_browser.confirm import ConfirmationDecision
from mcp_server_diwall_browser.evidence import DownloadManager, EvidenceWriter
from mcp_server_diwall_browser.gate import Pacer
from mcp_server_diwall_browser.journal import Journal
from mcp_server_diwall_browser.origin_policy import OriginPolicy
from mcp_server_diwall_browser.output import ToolError
from mcp_server_diwall_browser.perception import Perception

pytestmark = pytest.mark.browser

SECOND_NAME = "public-one.test"
THIRD_NAME = "public-two.test"
LOCAL_NAME = "machine.local"
RESOLVER_RULES = ",".join(
    [f"MAP {name} 127.0.0.1" for name in (SECOND_NAME, THIRD_NAME, LOCAL_NAME, "localhost")]
    + ["MAP * ~NOTFOUND", "EXCLUDE 127.0.0.1"]
)
POLICIES = ("strict", "discovery", "open")


@pytest.fixture(scope="module")
def launched() -> Iterator[LaunchedBrowser]:
    browser = launch_browser(arguments=[f"--host-resolver-rules={RESOLVER_RULES}"])
    if browser is None:
        pytest.skip(SKIP_REASON)
    yield browser
    browser.stop()


@pytest.fixture(scope="module")
def pages() -> Iterator[PageServer]:
    with PageServer() as server:
        yield server


@dataclass
class Course:
    """What a test needs of one run: the pages, the profile file, the fake window, the session."""

    pages: PageServer
    profile: Path
    window: FakeWindow
    session: ClientSession

    @staticmethod
    def url_of(pages: PageServer, name: str, page: str = "neutral.html", query: str = "") -> str:
        return f"http://{name}:{pages.port}/{page}{query}"

    def at(self, name: str, page: str = "neutral.html", query: str = "") -> str:
        return self.url_of(self.pages, name, page, query)

    @property
    def second(self) -> str:
        return f"http://{SECOND_NAME}:{self.pages.port}"

    def document(self) -> dict[str, Any]:
        return json.loads(self.profile.read_text())

    def rewrite(self, tmp_path: Path, launched: LaunchedBrowser, **overrides: Any) -> None:
        write_profile(tmp_path, **profile_settings(tmp_path, launched, self.pages, **overrides))

    async def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments)
        envelope = json.loads(result.content[0].text)
        assert result.isError is (envelope["ok"] is not True)
        return envelope

    async def open(self, url: str) -> str:
        opened = await self.call("open_tab", url=url, intent="look at the page")
        assert opened["ok"], opened
        return str(opened["server"]["tab_id"])


def profile_settings(tmp_path: Path, launched: LaunchedBrowser, pages: PageServer, **overrides: Any) -> dict[str, Any]:
    volume = tmp_path / "vol"
    volume.mkdir(mode=0o700, exist_ok=True)
    settings: dict[str, Any] = {
        "secrets_dir": str(volume),
        "allowed_origins": [pages.origin],
        "browser": {"family": "chromium", "profile_dir": launched.profile_dir, "debug_port": launched.port},
    }
    settings.update(overrides)
    return settings


@asynccontextmanager
async def running(tmp_path: Path, launched: LaunchedBrowser, pages: PageServer, **overrides: Any) -> AsyncIterator[Course]:
    profile = write_profile(tmp_path, **profile_settings(tmp_path, launched, pages, **overrides))
    window = FakeWindow(tmp_path / "bin")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    environment = {
        "MCP_DIWALL_BROWSER_PROFILE": str(profile),
        "HOME": str(home),
        "PATH": f"{window.path.parent}:/usr/bin:/bin",
        "DISPLAY": ":0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    with open(tmp_path / "stderr.log", "w") as errlog:
        async with stdio_session([sys.executable, "-m", MODULE], environment, errlog) as (session, unreadable):
            yield Course(pages, profile, window, session)
    assert unreadable == []


def mark_of(observed: dict[str, Any], name: str) -> int:
    return next(element["mark"] for element in observed["page_content"]["elements"] if element["name"] == name)


def say(policy: str, step: str, envelope: dict[str, Any], windows: int) -> None:
    error = envelope["server"].get("error")
    print(f"[{policy}] {step}: ok={envelope['ok']} {error['code'] if error else ''} windows={windows}")  # noqa: T201


async def follow_the_link(course: Course, policy: str) -> tuple[str, dict[str, Any]]:
    """The listed portal, its link to a second origin, the click, and the first observation of the second page."""
    tab = await course.open(course.at("127.0.0.1", "portal.html", f"?to={course.at(SECOND_NAME)}"))
    portal = await course.call("observe", tab_id=tab)
    assert portal["ok"], portal
    say(policy, "the portal, listed", portal, len(course.window.questions()))
    clicked = await course.call(
        "click", tab_id=tab, snapshot_id=portal["server"]["snapshot_id"], mark=mark_of(portal, "Aller à la seconde origine")
    )
    assert clicked["ok"], clicked
    second = await course.call("observe", tab_id=tab)
    return tab, second


# --- the three policies on a portal that links to a second origin ----------------------------------------------


async def test_strict_refuses_the_second_origin_and_opens_no_window(tmp_path: Path, launched: LaunchedBrowser, pages: PageServer) -> None:
    async with running(tmp_path, launched, pages, origin_policy="strict") as course:
        _tab, second = await follow_the_link(course, "strict")
        say("strict", "the second origin", second, len(course.window.questions()))
        status = await course.call("status")
    error = second["server"]["error"]
    assert error["code"] == "origin_not_allowed" and second["server"]["origin"] == course.second
    assert course.second in error["message"] and "origin_policy" in error["message"]
    assert second["page_content"] is None and course.window.questions() == []
    assert status["server"]["origin_policy"] == "strict"


async def test_discovery_asks_once_and_a_refusal_is_final_for_the_session(tmp_path: Path, launched: LaunchedBrowser, pages: PageServer) -> None:
    async with running(tmp_path, launched, pages, origin_policy="discovery") as course:
        tab, second = await follow_the_link(course, "discovery")
        say("discovery", "the second origin, window answered Refuser", second, len(course.window.questions()))
        again = await course.call("observe", tab_id=tab)
        say("discovery", "the same origin again", again, len(course.window.questions()))
    assert second["server"]["error"]["code"] == again["server"]["error"]["code"] == "origin_not_allowed"
    [question] = course.window.questions()
    assert f"Origine : {course.second}" in question and "/neutral.html" not in question


async def test_discovery_this_session_reads_and_acts_and_the_profile_is_not_rewritten(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer
) -> None:
    async with running(tmp_path, launched, pages, origin_policy="discovery") as course:
        before = course.profile.read_bytes()
        course.window.answer(choice="session")
        tab, second = await follow_the_link(course, "discovery")
        say("discovery", "the second origin, window answered Cette séance", second, len(course.window.questions()))
        snapshot = second["server"]["snapshot_id"]
        filled = await course.call("fill", tab_id=tab, snapshot_id=snapshot, mark=mark_of(second, "Pseudonyme"), text="alice")
        clicked = await course.call("click", tab_id=tab, snapshot_id=snapshot, mark=mark_of(second, "Envoyer"))
        after = await course.call("observe", tab_id=tab, include=["tree"])
        status = await course.call("status")
    assert second["ok"] and filled["ok"] and clicked["ok"]
    assert '  StaticText "Sent by alice"' in after["page_content"]["tree"]
    assert len(course.window.questions()) == 1
    assert course.profile.read_bytes() == before
    assert status["server"]["session_origins"] == [course.second]


async def test_discovery_add_to_the_list_writes_the_exact_origin_in_the_profile(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer
) -> None:
    async with running(tmp_path, launched, pages, origin_policy="discovery") as course:
        before = course.document()
        course.window.answer(choice="add")
        _tab, second = await follow_the_link(course, "discovery")
        say("discovery", "the second origin, window answered Ajouter à la liste", second, len(course.window.questions()))
        after = course.document()
        status = await course.call("status")
    assert second["ok"] and len(course.window.questions()) == 1
    assert after["allowed_origins"] == [*before["allowed_origins"], course.second]
    assert {k: v for k, v in after.items() if k != "allowed_origins"} == {k: v for k, v in before.items() if k != "allowed_origins"}
    assert course.profile.stat().st_mode & 0o777 == 0o600
    assert [entry["origin"] for entry in status["server"]["allowed_origins"]] == [pages.origin, course.second]
    print(f"[discovery] profile read back: allowed_origins = {after['allowed_origins']}")  # noqa: T201


async def test_open_reads_the_second_origin_without_any_window(tmp_path: Path, launched: LaunchedBrowser, pages: PageServer) -> None:
    async with running(tmp_path, launched, pages, origin_policy="open") as course:
        _tab, second = await follow_the_link(course, "open")
        say("open", "the second origin", second, len(course.window.questions()))
        status = await course.call("status")
    assert second["ok"] and second["server"]["origin"] == course.second
    assert course.window.questions() == []
    assert status["server"]["origin_policy"] == "open" and "provider" in status["server"]["origin_policy_note"]


# --- a redirection to an unknown origin ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy", POLICIES)
async def test_a_redirection_to_an_unknown_origin_is_judged_on_the_new_origin(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer, policy: str
) -> None:
    async with running(tmp_path, launched, pages, origin_policy=policy) as course:
        tab = await course.open(course.at("127.0.0.1", "redirect.html", f"?to={course.at(THIRD_NAME)}"))
        await course.call("wait", tab_id=tab, **{"for": "load"})
        observed = await course.call("observe", tab_id=tab)
        say(policy, "after the redirection", observed, len(course.window.questions()))
    third = f"http://{THIRD_NAME}:{pages.port}"
    if policy == "open":
        assert observed["ok"] and observed["server"]["origin"] == third and course.window.questions() == []
        return
    assert observed["server"]["error"]["code"] == "origin_not_allowed" and observed["server"]["origin"] == third
    assert len(course.window.questions()) == (1 if policy == "discovery" else 0)


# --- open keeps the local names closed, and the red list keeps its window ------------------------------------------


@pytest.mark.parametrize("name", ["localhost", LOCAL_NAME])
async def test_open_refuses_a_local_name_until_the_operator_lists_it(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer, name: str
) -> None:
    async with running(tmp_path, launched, pages, origin_policy="open") as course:
        tab = await course.open(course.at(name))
        refused = await course.call("observe", tab_id=tab)
        say("open", f"the local name {name}", refused, len(course.window.questions()))
        course.rewrite(tmp_path, launched, origin_policy="open", allowed_origins=[pages.origin, f"http://{name}:{pages.port}"])
        listed = await course.call("observe", tab_id=tab)
        say("open", f"the local name {name}, listed", listed, len(course.window.questions()))
    assert refused["server"]["error"]["code"] == "origin_not_allowed" and course.window.questions() == []
    assert listed["ok"]


async def test_open_still_asks_the_window_for_a_payment_on_an_unknown_origin(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer
) -> None:
    # In process, with a simulated encrypted volume: the confirmation of a locked action needs the journal and the
    # evidence, which a mounted volume holds, and a process without one refuses before it asks.
    attachment = await BrowserAttachment.attach(
        SimpleNamespace(browser=SimpleNamespace(family="chromium", profile_dir=launched.profile_dir, debug_port=launched.port))
    )
    registry = TabRegistry(attachment.connection)
    paths, _mount, volume = mounted_volume(tmp_path)
    clock = FakeClock()
    profile = make_profile(tmp_path, allowed=(pages.origin,), origin_policy="open")
    policy = OriginPolicy(profile=lambda: profile, window=lambda: FakeOriginWindow(available=False))
    perception = Perception(registry, admit=policy.admit)
    channel = FakeChannel(ConfirmationDecision.DENIED)
    actions = Actions(
        ActionContext(
            profile=lambda: profile,
            registry=registry,
            perception=perception,
            origin_policy=policy,
            pacer=Pacer(lambda: profile.limits, clock=clock, sleep=clock.sleep),
            channel=channel,
            journal=Journal(volume, lambda: ("test-client", "1.0")),
            volume=volume,
            evidence=EvidenceWriter(volume),
            downloads=DownloadManager(volume, attachment.connection),
            settle_start_s=0.3,
        )
    )
    try:
        tab = await registry.open_tab(Course.url_of(pages, SECOND_NAME, "payment.html"))
        observation = await perception.observe(tab, image=False)
        assert observation.snapshot is not None and observation.page_content is not None
        mark = next(e.mark for e in observation.snapshot.elements if e.name == "Payer maintenant")
        with pytest.raises(ToolError) as caught:
            await actions.click(tab.tab_id, observation.snapshot.snapshot_id, mark)
        print(f"[open] a payment on an unknown origin: {caught.value.code} asked={len(channel.texts)}")  # noqa: T201
        again = await perception.observe(tab, image=False)
        assert again.page_content is not None
        assert caught.value.code == "confirmation_denied"
        assert len(channel.texts) == 1 and "Payer maintenant" in channel.texts[0] and f"Origine : http://{SECOND_NAME}" in channel.texts[0]
        assert '  StaticText "Nothing done"' in again.page_content["tree"]
        assert paths.project.exists()
    finally:
        for info in await registry.list_tabs():
            await registry.close_tab(info.tab_id)
        await attachment.close()


# --- the policy is changed in the profile, the server is not restarted -----------------------------------------------


async def test_the_policy_is_changed_in_the_profile_without_restarting_the_server(
    tmp_path: Path, launched: LaunchedBrowser, pages: PageServer
) -> None:
    async with running(tmp_path, launched, pages, origin_policy="strict") as course:
        tab = await course.open(course.at(SECOND_NAME))
        steps: list[tuple[str, bool, int]] = []
        for policy in ("strict", "open", "strict", "discovery", "strict"):
            course.rewrite(tmp_path, launched, origin_policy=policy)
            if policy == "discovery":
                course.window.answer(choice="session")
            observed = await course.call("observe", tab_id=tab)
            steps.append((policy, observed["ok"], len(course.window.questions())))
            say(policy, "switched in the profile", observed, len(course.window.questions()))
    assert steps == [
        ("strict", False, 0),
        ("open", True, 0),
        ("strict", False, 0),
        ("discovery", True, 1),
        ("strict", False, 1),
    ]
