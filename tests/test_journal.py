from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import FAKE_PASSWORD, FAKE_USERNAME, ROOT_LINE, mounted_volume, write_credential

from mcp_server_diwall_browser import journal as journal_module
from mcp_server_diwall_browser.credentials import resolve
from mcp_server_diwall_browser.journal import FIELDS, Journal, JournalEntry
from mcp_server_diwall_browser.mount import Volume
from mcp_server_diwall_browser.origin import parse_origin
from mcp_server_diwall_browser.output import ToolError

SITE = parse_origin("https://example.invalid")


def client() -> tuple[str, str]:
    return ("Test Client/é<script>", "1.2.3")


def read_lines(volume_project: Path) -> list[dict[str, object]]:
    text = (volume_project / "journal" / "operations.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


def operation_with_a_resolved_credential(volume: Volume, journal: Journal) -> None:
    secret = resolve(volume, SITE, "password")
    user = resolve(volume, SITE, "username")
    journal.write(
        JournalEntry(
            operation_id="op1",
            tool="fill",
            origin=f"https://{user.reveal()}:{secret.reveal()}@example.invalid/login?token={secret.reveal()}#frag",
            result="ok",
            mutating=True,
            intent=f"type {secret.reveal()} for {user.reveal()} then {secret}",
        )
    )


def test_operation_produces_one_json_line_with_only_the_listed_fields(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    journal = Journal(volume, client)
    operation_with_a_resolved_credential(volume, journal)

    raw = (paths.project / "journal" / "operations.jsonl").read_text()
    assert raw.count("\n") == 1 and raw.endswith("\n")
    [entry] = read_lines(paths.project)
    assert tuple(entry) == FIELDS
    assert FAKE_PASSWORD not in raw and FAKE_USERNAME not in raw
    assert entry["origin"] == "https://example.invalid"
    assert entry["intent"] == "type <redacted> for <redacted> then <secret:password>"
    assert entry["gates"] == {"red_list": False, "confirmation": "none"}
    assert entry["client"] == {"name": "Test Clientscript", "version": "1.2.3"}
    assert entry["mutating"] is True
    assert "+" in str(entry["ts"]) or "-" in str(entry["ts"])[19:]


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://example.invalid:443/a/b?c=d#e", "https://example.invalid"),
        ("https://example.invalid:8443/x", "https://example.invalid:8443"),
        ("about:blank", "about:blank"),
        ("data:text/html,<p>page-text</p>", "data:"),
        ("data:text/html,<p>page text</p>", "<invalid-url>"),
        ("https://alice:pw@example.invalid/a", "https://example.invalid"),
        ("chrome://settings/passwords?search=x", "chrome://settings/passwords"),
    ],
)
def test_origin_field_holds_no_path_query_or_page_content(tmp_path: Path, origin: str, expected: str) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    Journal(volume, client).write(JournalEntry("op", "navigate", "ok", False, origin=origin))
    assert read_lines(paths.project)[0]["origin"] == expected


def test_intent_is_bounded(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    Journal(volume, client).write(JournalEntry("op", "click", "ok", True, intent="x" * 900))
    assert len(str(read_lines(paths.project)[0]["intent"])) == 500


def test_missing_client_information_is_unknown(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)

    def broken() -> tuple[str, str]:
        raise RuntimeError("no client")

    Journal(volume, lambda: None).write(JournalEntry("op", "status", "ok", False))
    Journal(volume, broken).write(JournalEntry("op", "status", "ok", False))
    for entry in read_lines(paths.project):
        assert entry["client"] == {"name": "unknown", "version": "unknown"}


def test_unknown_confirmation_state_is_a_programming_error(tmp_path: Path) -> None:
    _paths, _fake, volume = mounted_volume(tmp_path)
    with pytest.raises(ValueError):
        Journal(volume, client).write(JournalEntry("op", "click", "ok", True, confirmation="maybe"))


# --- unavailable journal -------------------------------------------------------------


def test_closed_volume_does_not_fail_an_ordinary_operation(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    paths, fake, volume = mounted_volume(tmp_path)
    journal = Journal(volume, client)
    fake.rewrite(ROOT_LINE + "\n")
    with caplog.at_level(logging.WARNING, logger="mcp_server_diwall_browser.journal"):
        assert journal.write(JournalEntry("op1", "observe", "ok", False)) is False
        assert journal.write(JournalEntry("op2", "observe", "ok", False)) is False
    assert journal.operations_not_journaled == 2
    warnings = [r for r in caplog.records if r.name == "mcp_server_diwall_browser.journal"]
    assert len(warnings) == 1
    assert not (paths.project / "journal").exists()


def test_closed_volume_makes_a_required_entry_fail(tmp_path: Path) -> None:
    _paths, fake, volume = mounted_volume(tmp_path)
    journal = Journal(volume, client)
    fake.rewrite(ROOT_LINE + "\n")
    with pytest.raises(ToolError) as caught:
        journal.write_required(JournalEntry("op", "click", "requested", True, red_list=True, confirmation="requested"))
    assert caught.value.code == "encrypted_dir_undetermined"
    assert journal.operations_not_journaled == 1


def test_write_failure_makes_a_required_entry_fail_as_journal_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    journal = Journal(volume, client)

    def failing_write(_fd: int, _data: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(journal_module.os, "write", failing_write)
    with pytest.raises(ToolError) as caught:
        journal.write_required(JournalEntry("op", "click", "requested", True, red_list=True))
    assert caught.value.code == "journal_unavailable"
    assert journal.write(JournalEntry("op", "observe", "ok", False)) is False
    assert journal.operations_not_journaled == 2


def test_required_entry_is_written_when_the_volume_is_open(tmp_path: Path) -> None:
    paths, _fake, volume = mounted_volume(tmp_path)
    Journal(volume, client).write_required(
        JournalEntry("op", "click", "requested", True, origin="https://example.invalid", red_list=True, confirmation="requested")
    )
    [entry] = read_lines(paths.project)
    assert entry["result"] == "requested"
    assert entry["gates"] == {"red_list": True, "confirmation": "requested"}


# --- redaction: the check and its counter-proof -------------------------------------


def intent_with_secret_is_redacted(write_line: Callable[[Journal, JournalEntry], None], tmp_path: Path) -> bool:
    paths, _fake, volume = mounted_volume(tmp_path)
    write_credential(paths, "service.json")
    secret = resolve(volume, SITE, "password")
    journal = Journal(volume, client)
    write_line(journal, JournalEntry("op", "fill", "ok", True, intent=f"typing {secret.reveal()}"))
    raw = (paths.project / "journal" / "operations.jsonl").read_text()
    return FAKE_PASSWORD not in raw


def real_write(journal: Journal, entry: JournalEntry) -> None:
    journal.write(entry)


def raw_intent_write(journal: Journal, entry: JournalEntry) -> None:
    # Defective: the intent argument is journaled as received.
    record = {name: None for name in FIELDS}
    record["intent"] = entry.intent
    journal._append((json.dumps(record) + "\n").encode())


def test_resolved_value_in_the_intent_is_redacted(tmp_path: Path) -> None:
    assert intent_with_secret_is_redacted(real_write, tmp_path)


def test_redaction_check_catches_a_journal_that_writes_the_raw_intent(tmp_path: Path) -> None:
    assert not intent_with_secret_is_redacted(raw_intent_write, tmp_path)
