"""Un changement des listes d'origines du profil laisse une trace au journal et dans ``status``."""

from __future__ import annotations

import json
import os
import stat
from typing import Any

from test_server import ORIGIN, Rig, connected, rig  # noqa: F401 - rig is a fixture

from mcp_server_diwall_browser.origin import parse_origin_pattern
from mcp_server_diwall_browser.profile_watch import OriginLists, compare

ADDED = "https://added.example.test"
OTHER_SITE = "https://second.example.test"
FINGERPRINT = ("journal", "profile-origins.json")


def changes_in(rig: Rig) -> list[dict[str, Any]]:
    return [line for line in rig.journal_lines() if line.get("event") == "profile_changed"]


def notices_of(status: dict[str, Any]) -> list[str]:
    return [text for text in status["server"]["notices"] if text.startswith("profile_changed")]


# --- le contrôle -------------------------------------------------------------------------


async def test_an_edited_profile_then_status_writes_the_line_with_the_added_origin_and_nothing_else(rig: Rig) -> None:
    async with connected(rig) as client:
        before = await client.call("status")
        assert before["ok"] and notices_of(before) == []
        rig.write_profile(allowed_origins=[ORIGIN, ADDED])
        status = await client.call("status")

    [line] = changes_in(rig)
    assert line["changes"] == {"allowed_origins": {"added": [ADDED], "removed": []}}
    assert set(line) == {"ts", "event", "changes", "client", "host", "user"}
    raw = json.dumps(line)
    # Aucun autre champ du profil n'est journalisé : ni les chemins, ni le port, ni les plafonds.
    for other in ("secrets_dir", "secrets_crypt_dir", "profile_dir", "debug_port", "limits", "declarations", str(rig.tmp)):
        assert other not in raw
    [notice] = notices_of(status)
    assert ADDED in notice and ORIGIN not in notice


async def test_a_second_status_without_change_writes_no_new_line(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(allowed_origins=[ORIGIN, ADDED])
        first = await client.call("status")
        second = await client.call("status")
        third = await client.call("status")

    assert len(changes_in(rig)) == 1
    assert len(notices_of(first)) == 1
    assert notices_of(second) == [] and notices_of(third) == []


async def test_a_restart_after_an_edit_made_while_the_process_was_stopped_writes_the_line(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
    assert changes_in(rig) == []

    rig.write_profile(allowed_origins=[ORIGIN, ADDED])
    async with connected(rig) as client:
        status = await client.call("status")

    [line] = changes_in(rig)
    assert line["changes"] == {"allowed_origins": {"added": [ADDED], "removed": []}}
    assert len(notices_of(status)) == 1

    async with connected(rig) as client:
        again = await client.call("status")
    assert len(changes_in(rig)) == 1 and notices_of(again) == []


# --- ce qui est comparé ----------------------------------------------------------------------------


async def test_a_removed_origin_is_named_as_removed(rig: Rig) -> None:
    rig.write_profile(allowed_origins=[ORIGIN, ADDED])
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(allowed_origins=[ORIGIN])
        status = await client.call("status")

    [line] = changes_in(rig)
    assert line["changes"] == {"allowed_origins": {"added": [], "removed": [ADDED]}}
    assert len(notices_of(status)) == 1


async def test_confirm_each_change_and_local_model_origins_are_compared_too(rig: Rig) -> None:
    rig.write_profile(allowed_origins=[ORIGIN, ADDED])
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(allowed_origins=[ORIGIN, ADDED], confirm_each_change=[ADDED], local_model_origins=[ORIGIN])
        await client.call("status")

    [line] = changes_in(rig)
    assert line["changes"] == {
        "confirm_each_change": {"added": [ADDED], "removed": []},
        "local_model_origins": {"added": [ORIGIN], "removed": []},
    }


async def test_a_wildcard_entry_is_named_as_written(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(allowed_origins=[ORIGIN, "https://*.example.test"])
        await client.call("status")

    [line] = changes_in(rig)
    assert line["changes"] == {"allowed_origins": {"added": ["https://*.example.test"], "removed": []}}


async def test_the_order_of_the_entries_is_not_a_change(rig: Rig) -> None:
    rig.write_profile(allowed_origins=[ORIGIN, ADDED])
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(allowed_origins=[ADDED, ORIGIN])
        status = await client.call("status")

    assert changes_in(rig) == [] and notices_of(status) == []


async def test_a_change_of_another_field_writes_no_line(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(limits={"min_action_delay_ms": 400, "max_actions_per_session": 50, "max_pages_per_session": 10})
        status = await client.call("status")

    assert status["ok"] and changes_in(rig) == [] and notices_of(status) == []


async def test_a_change_seen_by_another_tool_is_reported_by_the_next_status(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(allowed_origins=[ORIGIN, ADDED])
        await client.call("list_tabs")
        assert len(changes_in(rig)) == 1
        status = await client.call("status")

    assert len(changes_in(rig)) == 1
    assert len(notices_of(status)) == 1


# --- où vit l'empreinte ----------------------------------------------------------------------------


async def test_the_first_run_records_the_fingerprint_without_a_line(rig: Rig) -> None:
    async with connected(rig) as client:
        status = await client.call("status")

    assert status["ok"] and changes_in(rig) == []
    stored = json.loads((rig.paths.project / "journal" / "profile-origins.json").read_text())
    assert stored["allowed_origins"] == [ORIGIN]


async def test_the_fingerprint_sits_next_to_the_journal_in_mode_600_in_a_directory_in_700(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
        rig.write_profile(allowed_origins=[ORIGIN, ADDED])
        await client.call("status")

    directory = rig.paths.project / "journal"
    fingerprint = directory / "profile-origins.json"
    assert stat.S_IMODE(os.lstat(fingerprint).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(directory).st_mode) == 0o700
    assert (directory / "operations.jsonl").exists()
    assert json.loads(fingerprint.read_text())["allowed_origins"] == sorted([ORIGIN, ADDED])
    assert sorted(entry.name for entry in directory.iterdir()) == ["operations.jsonl", "profile-origins.json"]


async def test_with_the_volume_closed_nothing_is_read_or_written_and_the_rest_works(rig: Rig) -> None:
    rig.close_volume()
    async with connected(rig) as client:
        first = await client.call("status")
        rig.write_profile(allowed_origins=[ORIGIN, ADDED])
        second = await client.call("status")

    assert first["ok"] and second["ok"]
    assert notices_of(first) == [] and notices_of(second) == []
    assert not (rig.paths.project / "journal").exists()


async def test_a_change_made_while_the_volume_is_closed_is_reported_once_it_opens(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
        open_text = rig.mount.text
        rig.close_volume()
        rig.write_profile(allowed_origins=[ORIGIN, ADDED])
        closed = await client.call("status")
        assert notices_of(closed) == [] and changes_in(rig) == []
        rig.mount.rewrite(open_text)
        opened = await client.call("status")

    [line] = changes_in(rig)
    assert line["changes"] == {"allowed_origins": {"added": [ADDED], "removed": []}}
    assert len(notices_of(opened)) == 1


# --- la comparaison, à un seul endroit -------------------------------------------------------------


def lists(allowed: tuple[str, ...], confirm: tuple[str, ...] = (), local: tuple[str, ...] = ()) -> OriginLists:
    return OriginLists(allowed_origins=allowed, confirm_each_change=confirm, local_model_origins=local)


def test_compare_names_what_was_added_and_removed_per_list() -> None:
    previous = lists((ORIGIN, OTHER_SITE))
    current = lists((ORIGIN, ADDED), confirm=(ADDED,))
    assert compare(previous, current) == {
        "allowed_origins": {"added": [ADDED], "removed": [OTHER_SITE]},
        "confirm_each_change": {"added": [ADDED], "removed": []},
    }


def test_compare_of_equal_lists_is_empty() -> None:
    assert compare(lists((ORIGIN, ADDED)), lists((ADDED, ORIGIN))) == {}


def test_the_lists_are_read_from_a_profile_as_canonical_text() -> None:
    entry = parse_origin_pattern("https://*.example.test")
    assert entry.canonical == "https://*.example.test"


def test_a_fingerprint_is_replaced_whole_and_stays_private(tmp_path: Any) -> None:
    from conftest import mounted_volume

    paths, _fake, volume = mounted_volume(tmp_path)
    volume.ensure_dir("journal")
    volume.replace_file(*FINGERPRINT, data=b"first")
    volume.replace_file(*FINGERPRINT, data=b"second")
    target = paths.project / "journal" / "profile-origins.json"
    assert target.read_bytes() == b"second"
    assert stat.S_IMODE(os.lstat(target).st_mode) == 0o600
    assert [entry.name for entry in target.parent.iterdir()] == ["profile-origins.json"]


# --- the origin policy is compared like the lists ---------------------------------------------------------------------


async def test_a_change_of_the_origin_policy_leaves_the_same_trace_as_a_change_of_a_list(rig: Rig) -> None:
    async with connected(rig) as client:
        before = await client.call("status")
        assert notices_of(before) == []
        rig.write_profile(origin_policy="open")
        status = await client.call("status")
    [line] = changes_in(rig)
    assert line["changes"] == {"origin_policy": {"added": ["open"], "removed": ["strict"]}}
    [notice] = notices_of(status)
    assert "origin_policy" in notice and "+open" in notice and "-strict" in notice


async def test_a_fingerprint_written_before_the_policy_existed_is_read_as_strict(rig: Rig) -> None:
    async with connected(rig) as client:
        await client.call("status")
    path = rig.paths.project.joinpath(*FINGERPRINT)
    document = json.loads(path.read_text())
    assert document.pop("origin_policy") == ["strict"]
    path.write_text(json.dumps(document))
    rig.write_profile(origin_policy="open")
    async with connected(rig) as client:
        status = await client.call("status")
    [line] = changes_in(rig)
    assert line["changes"] == {"origin_policy": {"added": ["open"], "removed": ["strict"]}}
    assert len(notices_of(status)) == 1


def test_the_lists_of_an_old_document_carry_the_default_policy() -> None:
    old = {"allowed_origins": [], "confirm_each_change": [], "local_model_origins": []}
    lists = OriginLists.of_document(old)
    assert lists is not None and lists.origin_policy == ("strict",)
    assert compare(lists, OriginLists(origin_policy=("discovery",))) == {"origin_policy": {"added": ["discovery"], "removed": ["strict"]}}


async def test_the_server_adding_an_origin_after_the_window_leaves_its_trace_too(rig: Rig) -> None:
    rig.write_profile(origin_policy="discovery")
    rig.browser.site["https://dropped.example.test/page"] = rig.browser.site[ORIGIN + "/page"]
    rig.window.answer(choice="add")
    async with connected(rig) as client:
        await client.call("status")
        opened = await client.call("open_tab", url="https://dropped.example.test/page")
        await client.call("observe", tab_id=opened["server"]["tab_id"])
        status = await client.call("status")
    [line] = changes_in(rig)
    assert line["changes"] == {"allowed_origins": {"added": ["https://dropped.example.test"], "removed": []}}
    assert len(notices_of(status)) == 1
