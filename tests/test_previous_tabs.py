"""Les onglets qu'un processus arrêté a laissés ouverts se listent et se ferment ; ils ne se pilotent pas."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_cdp import settle
from test_server import OTHER, PAY_URL, URL, ORIGIN, Rig, code_of, connected, rig  # noqa: F401 - rig is a fixture

from mcp_server_diwall_browser.tab_state import process_start_time

OTHER_URL = OTHER + "/page"
STATE = ("run", "tabs.json")


def state_path(rig: Rig) -> Path:
    return rig.paths.project.joinpath(*STATE)


def state_targets(rig: Rig) -> list[str]:
    return [entry["target_id"] for entry in json.loads(state_path(rig).read_text())["tabs"]]


def everything_said(rig: Rig, *runs: list[Any]) -> str:
    """Toutes les sorties d'un scénario : les résultats des outils et le journal."""
    parts = [json.dumps([result.model_dump() for result in results]) for results in runs]
    journal = rig.paths.project / "journal" / "operations.jsonl"
    if journal.exists():
        parts.append(journal.read_text())
    return "\n".join(parts)


async def first_process_opens_three(rig: Rig) -> tuple[list[str], list[Any]]:
    """Un processus ouvre trois onglets et s'arrête sans les fermer."""
    ids = []
    async with connected(rig) as client:
        for url in (URL, PAY_URL, OTHER_URL):
            opened = await client.call("open_tab", url=url, intent="look at the page")
            assert opened["ok"], opened
            ids.append(opened["server"]["tab_id"])
    return ids, client.results


# --- le contrôle -------------------------------------------------------------------------


async def test_a_second_process_lists_the_three_tabs_of_the_first_and_closes_one(rig: Rig) -> None:
    first_ids, first_results = await first_process_opens_three(rig)
    targets = set(rig.browser.targets)
    assert len(targets) == 3

    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        tabs = listed["server"]["tabs"]
        assert len(tabs) == 3
        assert all(tab["previous_session"] is True for tab in tabs)
        assert sorted(tab["origin"] for tab in tabs) == sorted([ORIGIN, ORIGIN, OTHER])
        assert not {tab["tab_id"] for tab in tabs} & set(first_ids)
        assert len({tab["tab_id"] for tab in tabs}) == 3
        assert all(set(tab) == {"tab_id", "origin", "previous_session"} for tab in tabs)

        closed_id, other_id, last_id = (tab["tab_id"] for tab in tabs)
        closed = await client.call("close_tab", tab_id=closed_id)
        assert closed["ok"] and closed["server"]["tab_id"] == closed_id and closed["server"]["closed"] is True
        refused = await client.call("observe", tab_id=other_id)
        assert not refused["ok"] and code_of(refused) == "tab_not_owned"
        again = await client.call("close_tab", tab_id=closed_id)
        assert code_of(again) == "tab_not_owned"
        remaining = await client.call("list_tabs")

    assert [tab["tab_id"] for tab in remaining["server"]["tabs"]] == [other_id, last_id]
    assert len(rig.browser.targets) == 2
    said = everything_said(rig, first_results, client.results)
    assert [target for target in targets if target in said] == []


async def test_the_target_identifier_leaves_in_no_output_not_even_the_state_file_name_of_a_tab(rig: Rig) -> None:
    _ids, first_results = await first_process_opens_three(rig)
    targets = list(rig.browser.targets)
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        await client.call("close_tab", tab_id=listed["server"]["tabs"][0]["tab_id"])
        await client.call("status")
        await client.call("open_tab", url=URL, intent="look at the page")
        await client.call("list_tabs")
    said = everything_said(rig, first_results, client.results)
    assert said and [target for target in targets if target in said] == []


# --- fermer n'est pas piloter ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("observe", {}),
        ("scroll", {"dy": 100}),
        ("wait", {"for": "load"}),
        ("navigate", {"url": URL, "intent": "go"}),
        ("click", {"snapshot_id": "s1", "mark": 1, "intent": "press"}),
        ("fill", {"snapshot_id": "s1", "mark": 1, "text": "x", "intent": "type"}),
        ("select_option", {"snapshot_id": "s1", "mark": 1, "option": "France", "intent": "choose"}),
        ("press_key", {"key": "Tab", "intent": "move"}),
        ("handle_dialog", {"accept": False}),
        ("save_evidence", {"label": "proof"}),
    ],
)
async def test_every_other_tool_refuses_a_previous_tab_as_not_owned(
    rig: Rig, tool: str, arguments: dict[str, Any]
) -> None:
    await first_process_opens_three(rig)
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        previous = listed["server"]["tabs"][0]["tab_id"]
        before = len(rig.fake.methods())
        refused = await client.call(tool, tab_id=previous, **arguments)
        calls_after = rig.fake.methods()[before:]
        unknown = await client.call(tool, tab_id="0123456789abcdef", **arguments)
    assert not refused["ok"] and code_of(refused) == "tab_not_owned"
    assert code_of(unknown) == "tab_not_owned"
    assert rig.gestures() == []
    assert not [m for m in calls_after if m.startswith(("Input.", "Page.navigate", "DOM.", "Runtime.", "Accessibility.", "DOMSnapshot."))]


async def test_a_previous_tab_is_never_attached_to_more_than_to_read_its_origin(rig: Rig) -> None:
    await first_process_opens_three(rig)
    async with connected(rig) as client:
        await client.call("list_tabs")
    methods = rig.fake.methods()
    started = len(methods) - methods[::-1].index("Browser.getVersion") - 1
    after = methods[started:]
    assert "Page.enable" not in after and "Inspector.enable" not in after
    assert not [m for m in after if m.startswith(("Input.", "Runtime.", "DOM.", "Accessibility.", "DOMSnapshot.", "Page.capture"))]


async def test_the_ceilings_do_not_count_the_closing_of_a_previous_tab(rig: Rig) -> None:
    await first_process_opens_three(rig)
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        for tab in listed["server"]["tabs"]:
            assert (await client.call("close_tab", tab_id=tab["tab_id"]))["ok"]
        status = await client.call("status")
    assert rig.browser.targets == {}
    counters = status["server"]["counters"]
    assert counters["actions"] == 0 and counters["pages"] == 0


# --- ce que le serveur écrit --------------------------------------------------------------------


async def test_the_state_file_is_private_and_holds_target_identifiers_only(rig: Rig) -> None:
    await first_process_opens_three(rig)
    path = state_path(rig)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.stat().st_uid == os.getuid()
    document = json.loads(path.read_text())
    assert set(document) == {"schema_version", "tabs"} and document["schema_version"] == 1
    assert sorted(state_targets(rig)) == sorted(rig.browser.targets)
    text = path.read_text()
    for forbidden in (URL, PAY_URL, OTHER_URL, ORIGIN, "example", "title", "url"):
        assert forbidden not in text


async def test_closing_a_tab_takes_it_out_of_the_file(rig: Rig) -> None:
    async with connected(rig) as client:
        opened = [await client.call("open_tab", url=url, intent="look") for url in (URL, PAY_URL)]
        assert len(state_targets(rig)) == 2
        await client.call("close_tab", tab_id=opened[0]["server"]["tab_id"])
        assert state_targets(rig) == list(rig.browser.targets)
        assert len(state_targets(rig)) == 1


async def test_a_window_a_page_opened_is_recorded_too(rig: Rig) -> None:
    async with connected(rig) as client:
        opened = await client.call("open_tab", url=URL, intent="look")
        parent = next(iter(rig.browser.targets))
        child = await rig.browser.announce("Popup", ORIGIN + "/popup", opener_id=parent)
        await settle()
        listed = await client.call("list_tabs")
        assert len(listed["server"]["tabs"]) == 2
        assert sorted(state_targets(rig)) == sorted([parent, child])
    assert opened["ok"]

    async with connected(rig) as client:
        later = await client.call("list_tabs")
    assert len(later["server"]["tabs"]) == 2 and all(tab["previous_session"] for tab in later["server"]["tabs"])


async def test_a_tab_the_operator_closed_meanwhile_is_dropped_from_the_file(rig: Rig) -> None:
    await first_process_opens_three(rig)
    gone = next(iter(rig.browser.targets))
    del rig.browser.targets[gone]
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
    assert len(listed["server"]["tabs"]) == 2
    assert sorted(state_targets(rig)) == sorted(rig.browser.targets) and gone not in state_targets(rig)


async def test_a_tab_that_navigated_since_gives_its_origin_of_now(rig: Rig) -> None:
    await first_process_opens_three(rig)
    moved = next(t for t, info in rig.browser.targets.items() if info["url"] == OTHER_URL)
    rig.browser.set_url(moved, "https://moved.example.test/elsewhere?token=abc")
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
    origins = sorted(tab["origin"] for tab in listed["server"]["tabs"])
    assert origins == sorted([ORIGIN, ORIGIN, "https://moved.example.test"])
    assert "token" not in json.dumps(listed) and "elsewhere" not in json.dumps(listed)


async def test_tabs_left_open_by_the_second_process_are_still_there_for_a_third(rig: Rig) -> None:
    await first_process_opens_three(rig)
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        await client.call("close_tab", tab_id=listed["server"]["tabs"][0]["tab_id"])
        await client.call("open_tab", url=URL, intent="look")
    async with connected(rig) as client:
        third = await client.call("list_tabs")
    assert len(third["server"]["tabs"]) == 3
    assert all(tab["previous_session"] is True for tab in third["server"]["tabs"])


# --- ce qui n'est pas à lui ---------------------------------------------------------------------


async def test_a_tab_of_the_operator_is_never_listed_nor_attached(rig: Rig) -> None:
    await first_process_opens_three(rig)
    foreign = await rig.browser.announce("Banque — relevé", "https://bank.example.test/account?token=abc")
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        status = await client.call("status")
    assert len(listed["server"]["tabs"]) == 3
    assert foreign not in rig.browser.sessions.values()
    assert foreign not in state_targets(rig)
    said = everything_said(rig, client.results)
    assert "Banque" not in said and "bank.example.test" not in said and foreign not in said
    assert status["ok"]


async def test_the_tabs_of_a_process_that_is_still_running_are_not_offered(rig: Rig) -> None:
    await first_process_opens_three(rig)
    document = json.loads(state_path(rig).read_text())
    parent = os.getppid()
    started = process_start_time(parent)
    assert started is not None
    for entry in document["tabs"]:
        entry["pid"], entry["started"], entry["instance"] = parent, started, "another-live-process"
    state_path(rig).write_text(json.dumps(document))

    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        opened = await client.call("open_tab", url=URL, intent="look")
    assert listed["ok"] and listed["server"]["tabs"] == []
    assert opened["ok"]
    assert len(state_targets(rig)) == 4
    assert len(rig.browser.targets) == 4


async def test_a_process_number_taken_again_by_another_program_does_not_hide_the_tabs(rig: Rig) -> None:
    await first_process_opens_three(rig)
    document = json.loads(state_path(rig).read_text())
    parent = os.getppid()
    started = process_start_time(parent)
    assert started is not None
    for entry in document["tabs"]:
        entry["pid"], entry["started"], entry["instance"] = parent, started + 1, "a-process-that-ended"
    state_path(rig).write_text(json.dumps(document))
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
    assert len(listed["server"]["tabs"]) == 3


# --- le volume fermé, le fichier abîmé ---------------------------------------------------------------


async def test_with_the_volume_closed_nothing_is_read_nor_written_and_the_rest_works(rig: Rig) -> None:
    await first_process_opens_three(rig)
    before = state_path(rig).read_bytes()
    rig.close_volume()
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        opened = await client.call("open_tab", url=URL, intent="look")
        reading = await client.call("observe", tab_id=opened["server"]["tab_id"])
        closed = await client.call("close_tab", tab_id=opened["server"]["tab_id"])
        status = await client.call("status")
    assert listed["ok"] and listed["server"]["tabs"] == []
    assert opened["ok"] and reading["ok"] and closed["ok"] and status["ok"]
    assert state_path(rig).read_bytes() == before


async def test_a_volume_opened_later_gives_the_previous_tabs_at_the_next_listing(rig: Rig) -> None:
    await first_process_opens_three(rig)
    opened_text = rig.mount.text
    rig.close_volume()
    async with connected(rig) as client:
        closed_listing = await client.call("list_tabs")
        rig.mount.rewrite(opened_text)
        listing = await client.call("list_tabs")
    assert closed_listing["server"]["tabs"] == []
    assert len(listing["server"]["tabs"]) == 3


async def test_a_process_that_opens_tabs_with_the_volume_closed_leaves_no_file(rig: Rig) -> None:
    rig.close_volume()
    async with connected(rig) as client:
        opened = await client.call("open_tab", url=URL, intent="look")
    assert opened["ok"]
    assert not state_path(rig).exists()


@pytest.mark.parametrize("damage", ["not json", '{"schema_version": 1, "tabs": "x"}', '{"tabs": [1, 2]}', "[]", ""])
async def test_a_damaged_file_is_ignored_and_written_again_at_the_next_change(rig: Rig, damage: str) -> None:
    await first_process_opens_three(rig)
    state_path(rig).write_text(damage)
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
        opened = await client.call("open_tab", url=URL, intent="look")
    assert listed["ok"] and listed["server"]["tabs"] == []
    assert opened["ok"]
    assert len(state_targets(rig)) == 1


async def test_an_oversized_file_is_ignored(rig: Rig) -> None:
    await first_process_opens_three(rig)
    state_path(rig).write_text(json.dumps({"schema_version": 1, "tabs": [], "padding": "x" * 70_000}))
    async with connected(rig) as client:
        listed = await client.call("list_tabs")
    assert listed["ok"] and listed["server"]["tabs"] == []


def test_the_start_time_of_this_process_is_a_number() -> None:
    started = process_start_time(os.getpid())
    assert isinstance(started, int) and started > 0
    assert process_start_time(2**22 + 12345) is None
