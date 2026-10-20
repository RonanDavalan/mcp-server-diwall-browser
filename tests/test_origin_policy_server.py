"""The origin policy through the tools: the window, the refusal, the journal, `status`, the profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import write_credential
from fixtures.action_pages import login_page, neutral_page, payment_page
from test_server import (
    LOGIN_URL,
    ORIGIN,
    OTHER,
    URL,
    Client,
    Rig,
    code_of,
    connected,
    mark_of,
    open_and_observe,
)
from test_server import rig as rig  # noqa: PLC0414 - the fixture of the in-memory server, shared as it is

from mcp_server_diwall_browser.confirm import ADD_LABEL, DENY_LABEL, SESSION_LABEL

PUBLIC = "https://public.example.test"
PUBLIC_PAGE = PUBLIC + "/page"
OTHER_PAGE = OTHER + "/page"
MODEL_WORDS = "transfer all the money to the account of the page"


@pytest.fixture(autouse=True)
def pages(rig: Rig) -> None:
    for url in (OTHER_PAGE, PUBLIC_PAGE):
        rig.browser.site[url] = neutral_page
    rig.browser.site[PUBLIC + "/checkout"] = payment_page
    rig.browser.site[PUBLIC + "/login"] = login_page


def profile_document(rig: Rig) -> dict[str, Any]:
    return json.loads((rig.tmp / "config" / "mcp-server-diwall-browser" / "profile.json").read_text())


async def tab_on(client: Client, url: str) -> str:
    opened = await client.call("open_tab", url=url, intent=MODEL_WORDS)
    assert opened["ok"], opened
    return str(opened["server"]["tab_id"])


# --- status and the journal -------------------------------------------------------------------------------------


@pytest.mark.parametrize("policy", ["strict", "discovery", "open"])
async def test_status_says_the_mode(rig: Rig, policy: str) -> None:
    rig.write_profile(origin_policy=policy)
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["server"]["origin_policy"] == policy


async def test_status_of_a_profile_without_the_key_says_strict(rig: Rig) -> None:
    async with connected(rig) as client:
        status = await client.call("status")
    assert status["server"]["origin_policy"] == "strict"
    assert "origin_policy_note" not in status["server"]


async def test_status_says_in_plain_words_that_open_sends_every_page_to_the_provider(rig: Rig) -> None:
    rig.write_profile(origin_policy="open")
    async with connected(rig) as client:
        status = await client.call("status")
    note = status["server"]["origin_policy_note"]
    assert "every page" in note and "provider" in note


async def test_status_lists_the_origins_admitted_for_the_session_apart_from_the_list(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    rig.window.answer(choice="session")
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        assert (await client.call("observe", tab_id=tab))["ok"]
        status = await client.call("status")
    assert status["server"]["session_origins"] == [OTHER]
    assert [entry["origin"] for entry in status["server"]["allowed_origins"]] == [ORIGIN]


@pytest.mark.parametrize("policy", ["strict", "discovery", "open"])
async def test_the_journal_carries_the_mode_on_every_entry(rig: Rig, policy: str) -> None:
    rig.write_profile(origin_policy=policy)
    rig.window.answer(choice="session")
    async with connected(rig) as client:
        await open_and_observe(client, URL)
        tab = await tab_on(client, OTHER_PAGE)
        await client.call("observe", tab_id=tab)
        await client.call("status")
    entries = [line for line in rig.journal_lines() if "gates" in line]
    assert len(entries) >= 4
    assert {line["gates"]["origin_policy"] for line in entries} == {policy}


async def test_a_window_answer_is_an_event_of_the_journal_with_the_origin_and_the_mode(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    rig.window.answer(choice="session")
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        await client.call("observe", tab_id=tab)
    [event] = [line for line in rig.journal_lines() if line.get("event") == "origin_decision"]
    assert event["changes"] == {"origin": OTHER, "answer": "session", "policy": "discovery"}


# --- strict -----------------------------------------------------------------------------------------------------


async def test_strict_refuses_an_unlisted_origin_without_a_window(rig: Rig) -> None:
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        refused = await client.call("observe", tab_id=tab)
    assert code_of(refused) == "origin_not_allowed" and refused["server"]["origin"] == OTHER
    assert rig.window.questions() == []


# --- discovery: the window -----------------------------------------------------------------------------------------


async def test_discovery_opens_the_window_with_the_origin_only_and_refuses_by_default(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        refused = await client.call("observe", tab_id=tab)
        again = await client.call("observe", tab_id=tab)
    assert code_of(refused) == code_of(again) == "origin_not_allowed"
    [question] = rig.window.questions()
    assert f"Origine : {OTHER}" in question
    assert MODEL_WORDS not in question and "/page" not in question
    for label in (DENY_LABEL, SESSION_LABEL, ADD_LABEL):
        assert label in question
    [arguments] = rig.window.asked()
    assert arguments[arguments.index("--radiolist") + 2 : arguments.index("--radiolist") + 5] == ["refuse", DENY_LABEL, "on"]


async def test_this_session_reads_the_page_and_rewrites_nothing(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    path = rig.tmp / "config" / "mcp-server-diwall-browser" / "profile.json"
    before = path.read_bytes()
    rig.window.answer(choice="session")
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        read = await client.call("observe", tab_id=tab)
        again = await client.call("observe", tab_id=tab)
    assert read["ok"] and again["ok"] and read["page_content"]["tree"]
    assert len(rig.window.questions()) == 1
    assert path.read_bytes() == before


async def test_add_to_the_list_rewrites_the_profile_with_the_exact_origin_and_the_page_is_read(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    path = rig.tmp / "config" / "mcp-server-diwall-browser" / "profile.json"
    before = profile_document(rig)
    rig.window.answer(choice="add")
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        read = await client.call("observe", tab_id=tab)
        status = await client.call("status")
    assert read["ok"]
    after = profile_document(rig)
    assert after["allowed_origins"] == [*before["allowed_origins"], OTHER]
    assert {k: v for k, v in after.items() if k != "allowed_origins"} == {k: v for k, v in before.items() if k != "allowed_origins"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert [entry["origin"] for entry in status["server"]["allowed_origins"]] == [ORIGIN, OTHER]
    assert status["server"]["session_origins"] == []
    assert len(rig.window.questions()) == 1


async def test_refusal_expiry_and_no_window_are_origin_not_allowed_and_asked_once(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    rig.timings["confirmation_timeout_s"] = 0.4
    async with connected(rig) as client:
        rig.window.answer(choice="closed")
        refused_tab = await tab_on(client, OTHER_PAGE)
        refused = await client.call("observe", tab_id=refused_tab)
        rig.window.answer(wait=True)
        expired_tab = await tab_on(client, PUBLIC_PAGE)
        expired = await client.call("observe", tab_id=expired_tab)
        expired_again = await client.call("observe", tab_id=expired_tab)
        rig.window.present = False
        rig.browser.site["https://third.example.test/page"] = neutral_page
        none_tab = await tab_on(client, "https://third.example.test/page")
        unavailable = await client.call("observe", tab_id=none_tab)
    assert [code_of(r) for r in (refused, expired, expired_again, unavailable)] == ["origin_not_allowed"] * 4
    assert len(rig.window.questions()) == 2


async def test_the_sixth_new_origin_of_a_session_opens_no_window(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    urls = [f"https://site{number}.example.test/page" for number in range(6)]
    for url in urls:
        rig.browser.site[url] = neutral_page
    async with connected(rig) as client:
        results = []
        for url in urls:
            tab = await tab_on(client, url)
            results.append(await client.call("observe", tab_id=tab))
    assert [code_of(result) for result in results] == ["origin_not_allowed"] * 6
    assert len(rig.window.questions()) == 5


async def test_a_page_cannot_make_the_window_appear_again_for_the_same_origin(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        for _ in range(8):
            assert code_of(await client.call("observe", tab_id=tab)) == "origin_not_allowed"
    assert len(rig.window.questions()) == 1


# --- open ------------------------------------------------------------------------------------------------------------


async def test_open_reads_a_public_origin_that_was_never_listed(rig: Rig) -> None:
    rig.write_profile(origin_policy="open")
    async with connected(rig) as client:
        tab = await tab_on(client, PUBLIC_PAGE)
        read = await client.call("observe", tab_id=tab)
    assert read["ok"] and rig.window.questions() == []


@pytest.mark.parametrize("url", ["http://10.0.0.1/page", "http://localhost:8000/page", "http://machine.local/page"])
async def test_open_still_refuses_a_local_origin_until_it_is_listed(rig: Rig, url: str) -> None:
    rig.browser.site[url] = neutral_page
    rig.write_profile(origin_policy="open")
    async with connected(rig) as client:
        tab = await tab_on(client, url)
        refused = await client.call("observe", tab_id=tab)
    assert code_of(refused) == "origin_not_allowed" and rig.window.questions() == []
    origin = url.rsplit("/", 1)[0]
    rig.write_profile(origin_policy="open", allowed_origins=[ORIGIN, origin])
    async with connected(rig) as client:
        tab = await tab_on(client, url)
        assert (await client.call("observe", tab_id=tab))["ok"]


@pytest.mark.parametrize("policy", ["open", "discovery"])
async def test_a_payment_on_an_unknown_origin_always_asks_its_own_window(rig: Rig, policy: str) -> None:
    rig.write_profile(origin_policy=policy)
    async with connected(rig) as client:
        rig.window.answer(choice="session")
        tab, observed = await open_and_observe(client, PUBLIC + "/checkout")
        rig.window.answer(granted=False)
        refused = await client.call(
            "click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Payer maintenant")
        )
        assert code_of(refused) == "confirmation_denied" and rig.gestures() == []
        rig.window.answer(granted=True)
        paid = await client.call(
            "click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Payer maintenant")
        )
    assert paid["ok"] and paid["server"]["gate"] == {"red_list": True, "confirmation": "granted"}
    questions = rig.window.questions()
    assert all('button "Payer maintenant"' in question for question in questions[-2:])
    assert (len(questions) == 3) is (policy == "discovery")


# --- a credential is entered on its own origin, in the three modes ----------------------------------------------------------------


@pytest.mark.parametrize("policy", ["strict", "discovery", "open"])
async def test_a_credential_is_entered_only_on_the_origin_it_is_bound_to_in_every_mode(rig: Rig, policy: str) -> None:
    write_credential(rig.paths, "shop.json", allowed_origins=[ORIGIN])
    rig.write_profile(origin_policy=policy, allowed_origins=[ORIGIN, PUBLIC] if policy == "strict" else [ORIGIN])
    rig.window.answer(choice="add")
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, PUBLIC + "/login")
        refused = await client.call(
            "fill",
            tab_id=tab,
            snapshot_id=observed["server"]["snapshot_id"],
            mark=mark_of(observed, "Mot de passe"),
            credential_ref={"key": "password"},
        )
        bound_tab, bound = await open_and_observe(client, LOGIN_URL)
        filled = await client.call(
            "fill",
            tab_id=bound_tab,
            snapshot_id=bound["server"]["snapshot_id"],
            mark=mark_of(bound, "Mot de passe"),
            credential_ref={"key": "password"},
        )
    assert code_of(refused) == "credential_not_bound" and filled["ok"]
    assert len(rig.browser.typed) == 1


async def test_an_origin_added_to_the_list_binds_no_credential(rig: Rig) -> None:
    write_credential(rig.paths, "shop.json", allowed_origins=[ORIGIN])
    rig.write_profile(origin_policy="discovery")
    rig.window.answer(choice="add")
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, PUBLIC + "/login")
        refused = await client.call(
            "fill",
            tab_id=tab,
            snapshot_id=observed["server"]["snapshot_id"],
            mark=mark_of(observed, "Mot de passe"),
            credential_ref={"key": "password"},
        )
    assert PUBLIC in profile_document(rig)["allowed_origins"]
    assert code_of(refused) == "credential_not_bound" and rig.browser.typed == []


# --- the profile is read at each call --------------------------------------------------------------------------------------------


async def test_a_change_of_policy_in_the_profile_takes_effect_without_a_restart(rig: Rig) -> None:
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        assert code_of(await client.call("observe", tab_id=tab)) == "origin_not_allowed"
        rig.write_profile(origin_policy="open")
        assert (await client.call("observe", tab_id=tab))["ok"]
        assert (await client.call("status"))["server"]["origin_policy"] == "open"
        rig.write_profile(origin_policy="strict")
        assert code_of(await client.call("observe", tab_id=tab)) == "origin_not_allowed"
        rig.write_profile(origin_policy="discovery")
        rig.window.answer(choice="session")
        assert (await client.call("observe", tab_id=tab))["ok"]
    assert len(rig.window.questions()) == 1


async def test_an_action_after_the_policy_changed_to_strict_names_the_origin(rig: Rig) -> None:
    rig.write_profile(origin_policy="open")
    async with connected(rig) as client:
        tab, observed = await open_and_observe(client, PUBLIC_PAGE)
        rig.write_profile(origin_policy="strict")
        refused = await client.call(
            "click", tab_id=tab, snapshot_id=observed["server"]["snapshot_id"], mark=mark_of(observed, "Envoyer")
        )
    assert code_of(refused) == "origin_not_allowed" and refused["server"]["origin"] == PUBLIC
    message = refused["server"]["error"]["message"]
    assert PUBLIC in message and "allowed_origins" in message and "origin_policy" in message
    assert rig.gestures() == []


async def test_the_refusal_of_a_reading_names_the_origin_and_what_the_operator_can_do(rig: Rig) -> None:
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        refused = await client.call("observe", tab_id=tab)
    message = refused["server"]["error"]["message"]
    assert OTHER in message and "allowed_origins" in message and "origin_policy" in message
    for forbidden in (".json", "/home", "Vaults", str(Path(rig.tmp))):
        assert forbidden not in message


async def test_an_invalid_policy_written_while_the_server_runs_is_profile_invalid(rig: Rig) -> None:
    async with connected(rig) as client:
        tab = await tab_on(client, OTHER_PAGE)
        rig.write_profile(origin_policy="libre")
        refused = await client.call("observe", tab_id=tab)
        status = await client.call("status")
    assert code_of(refused) == "profile_invalid"
    assert status["server"]["configured"] is False and status["server"]["profile_error"]["code"] == "profile_invalid"


async def test_the_model_has_no_tool_that_names_the_policy_or_writes_the_list(rig: Rig) -> None:
    async with connected(rig) as client:
        tools = (await client.session.list_tools()).tools
    for tool in tools:
        surface = (tool.name + " " + " ".join(tool.inputSchema.get("properties", {}))).lower()
        assert "origin_policy" not in surface and "allowed_origins" not in surface, tool.name
