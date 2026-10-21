from __future__ import annotations

import io
import json
import logging
import unicodedata
import urllib.parse
from collections.abc import Callable
from typing import Any

import pytest

from mcp_server_diwall_browser import output
from mcp_server_diwall_browser.cdp import CdpError
from mcp_server_diwall_browser.output import (
    EnvelopeError,
    ToolError,
    ToolOutput,
    finalize,
    redact_text,
    register_secret,
    sanitize_url,
    tool,
)

SECRET = "test-secret-value-1"


def envelope(server: dict[str, Any] | None = None, page: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "server": server or {}, "page_content": page}


def dumped(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# --- registry -----------------------------------------------------------------


def test_one_character_secret_is_redacted_everywhere() -> None:
    register_secret("q")
    result = finalize(envelope({"note": "quiq"}, {"title": "a q b", "items": [{"text": "qq"}]}))
    assert "q" not in dumped(result)
    assert result["server"]["note"] == "<redacted>ui<redacted>"


def test_redaction_does_not_damage_its_own_marker_when_applied_twice() -> None:
    register_secret("e")
    once = redact_text("see")
    assert once == "s<redacted><redacted>"
    assert redact_text(once) == once


def test_empty_value_is_never_registered() -> None:
    register_secret("")
    assert redact_text("unchanged text") == "unchanged text"


def test_percent_encoded_and_json_escaped_forms_are_redacted() -> None:
    value = 'p@ss "w/rd"&é'
    register_secret(value)
    percent = urllib.parse.quote(value, safe="")
    escaped = json.dumps(value)[1:-1]
    text = f"raw={value} percent={percent} json={escaped}"
    redacted = redact_text(text)
    assert value not in redacted
    assert percent not in redacted
    assert escaped not in redacted
    assert redacted.count("<redacted>") == 3


def test_longest_secret_wins_over_a_secret_it_contains() -> None:
    register_secret("abc")
    register_secret("abcdef")
    assert redact_text("xabcdefx") == "x<redacted>x"


def test_registry_exposes_no_way_to_list_values() -> None:
    public = {name for name in dir(output) if not name.startswith("_")}
    assert not {name for name in public if "secret" in name.lower()} - {"register_secret"}
    registry_public = {name for name in dir(output._registry) if not name.startswith("_")}
    assert registry_public == {"add", "redact"}


# --- URLs -----------------------------------------------------------------------


def test_url_for_the_model_drops_userinfo_and_secret_query_values() -> None:
    url = "https://alice:pw@example.test/a?token=abc&page=2#access_token=zzz"
    assert sanitize_url(url) == "https://example.test/a?token=<redacted>&page=2#<redacted>"


def test_url_for_the_journal_keeps_scheme_host_port_and_path_only() -> None:
    url = "https://alice:pw@example.test/a?token=abc&page=2#access_token=zzz"
    assert sanitize_url(url, for_journal=True) == "https://example.test/a"
    assert sanitize_url("https://example.test:8443/x/y?q=1", for_journal=True) == "https://example.test:8443/x/y"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.test/?code=1&state=2", "https://example.test/?code=<redacted>&state=<redacted>"),
        ("https://example.test/?X-Api_Key=1", "https://example.test/?X-Api_Key=<redacted>"),
        ("https://example.test/?SESSIONID=1", "https://example.test/?SESSIONID=<redacted>"),
        ("https://example.test/?%74oken=1", "https://example.test/?%74oken=<redacted>"),
        ("https://example.test/?postcode=75001", "https://example.test/?postcode=<redacted>"),
        ("https://example.test/?keyboard=fr", "https://example.test/?keyboard=<redacted>"),
        ("https://example.test/?page=2&lang=fr", "https://example.test/?page=2&lang=fr"),
        ("https://example.test/#", "https://example.test/#"),
        ("about:blank", "about:blank"),
        ("https:\\\\example.test", "<invalid-url>"),
        ("https://example.test/\nx", "<invalid-url>"),
        ("not a url", "<invalid-url>"),
        ("https:example.test", "<invalid-url>"),
    ],
)
def test_url_sanitizing_table(url: str, expected: str) -> None:
    assert sanitize_url(url) == expected


def test_url_keys_are_sanitized_by_finalize() -> None:
    result = finalize(
        envelope(
            {"url": "https://u:p@example.test/?token=t", "redirect_url": "https://example.test/#frag"},
            {"links": [{"target_url": "https://example.test/?auth=1"}], "url_label": "keep ?token=x"},
        )
    )
    assert result["server"]["url"] == "https://example.test/?token=<redacted>"
    assert result["server"]["redirect_url"] == "https://example.test/#<redacted>"
    assert result["page_content"]["links"][0]["target_url"] == "https://example.test/?auth=<redacted>"
    assert result["page_content"]["url_label"] == "keep ?token=x"


# --- envelope -------------------------------------------------------------------


def test_booleans_and_numbers_are_left_untouched() -> None:
    register_secret("1")
    result = finalize(envelope({"count": 1, "ratio": 1.5, "flag": True, "none": None}, {"n": 10}))
    assert result["server"] == {"count": 1, "ratio": 1.5, "flag": True, "none": None}
    assert result["page_content"] == {"n": 10}
    assert result["ok"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"ok": True, "server": {}, "page_content": None, "extra": 1},
        {"ok": True, "server": {}},
        {"ok": "yes", "server": {}, "page_content": None},
        {"ok": True, "server": [], "page_content": None},
        {"ok": True, "server": {"x": object()}, "page_content": None},
        {"ok": True, "server": {"x": b"bytes"}, "page_content": None},
        {"ok": True, "server": {1: "x"}, "page_content": None},
    ],
)
def test_malformed_envelope_is_a_programming_error(bad: dict[str, Any]) -> None:
    with pytest.raises(EnvelopeError):
        finalize(bad)


def test_strings_are_bounded_with_a_marker() -> None:
    result = finalize(envelope({"s": "a" * 5000}, {"p": "b" * 5000, "short": "ok"}))
    assert len(result["server"]["s"]) == 2000
    assert result["server"]["s"].endswith("…")
    assert len(result["page_content"]["p"]) == 4000
    assert result["page_content"]["p"].endswith("…")
    assert result["page_content"]["short"] == "ok"


def test_control_characters_are_removed_except_newline_and_tab_in_page_content() -> None:
    result = finalize(envelope({"s": "a\nb\tc\x00d\x1be"}, {"p": "a\nb\tc\x00d\x7fe\x85f"}))
    assert result["server"]["s"] == "abcde"
    assert result["page_content"]["p"] == "a\nb\tcdef"


def test_secret_split_by_a_control_character_is_still_redacted() -> None:
    register_secret(SECRET)
    split = SECRET[:4] + "\x00" + SECRET[4:]
    result = finalize(envelope({"s": split}, {"p": split}))
    assert SECRET not in dumped(result)


def test_unknown_error_code_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        ToolError("no_such_code", "message")


# --- nested values: the check and its counter-proof -----------------------------

Finalizer = Callable[[dict[str, Any]], dict[str, Any]]


def nested_secret_is_removed(finalize_impl: Finalizer) -> bool:
    register_secret(SECRET)
    result = finalize_impl(
        envelope(
            {"notices": [["deep", [SECRET]]]},
            {"elements": [{"name": "field", "children": [{"value": f"x{SECRET}x"}]}]},
        )
    )
    return SECRET not in dumped(result)


def finalize_without_list_descent(env: dict[str, Any]) -> dict[str, Any]:
    # Defective: cleans strings in objects but returns lists unchanged.
    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        return value

    return {"ok": env["ok"], "server": walk(env["server"]), "page_content": walk(env["page_content"])}


def test_secret_in_nested_lists_is_redacted() -> None:
    assert nested_secret_is_removed(finalize)


def test_nested_list_check_catches_a_finalize_that_skips_lists() -> None:
    assert not nested_secret_is_removed(finalize_without_list_descent)


# --- tool decorator --------------------------------------------------------------


async def test_successful_tool_returns_the_envelope_and_the_image() -> None:
    @tool("observe")
    async def observe() -> ToolOutput:
        assert output.current_operation_id() is not None
        return ToolOutput(server={"tab": 1}, page_content={"title": "Page"}, image_png=b"\x89PNG")

    result, image = await observe()
    assert result["ok"] is True
    assert set(result) == {"ok", "server", "page_content"}
    assert result["server"]["tool"] == "observe"
    assert len(result["server"]["operation_id"]) == 32
    assert result["server"]["notices"] == []
    assert result["page_content"] == {"title": "Page"}
    assert image == b"\x89PNG"


async def test_tool_error_becomes_a_failure_envelope_with_a_redacted_message() -> None:
    register_secret(SECRET)

    @tool("fill")
    async def fill() -> ToolOutput:
        raise ToolError(
            "credential_not_bound",
            f"refusé pour {SECRET}",
            action_expected="operator_opens_directory",
            extra_server={"detail": SECRET},
        )

    result, image = await fill()
    assert result["ok"] is False
    assert result["page_content"] is None
    assert image is None
    assert result["server"]["error"]["code"] == "credential_not_bound"
    assert result["server"]["error"]["action_expected"] == "operator_opens_directory"
    assert SECRET not in dumped(result)
    assert "<redacted>" in result["server"]["error"]["message"]


async def test_unexpected_exception_leaves_no_trace_and_no_raw_text(caplog: pytest.LogCaptureFixture) -> None:
    register_secret(SECRET)

    @tool("click")
    async def click() -> ToolOutput:
        raise RuntimeError(f"boom with {SECRET} and page text")

    with caplog.at_level(logging.DEBUG):
        result, _image = await click()
    text = dumped(result)
    assert result["server"]["error"] == {"code": "internal_error", "message": "erreur interne du serveur"}
    assert "boom" not in text and "Traceback" not in text and SECRET not in text
    assert "boom" not in caplog.text and SECRET not in caplog.text


async def test_a_protocol_error_that_escapes_is_logged_by_type_and_code_and_never_by_text(caplog: pytest.LogCaptureFixture) -> None:
    @tool("click")
    async def click() -> ToolOutput:
        raise CdpError(-32000, "texte témoin")

    with caplog.at_level(logging.DEBUG):
        result, _image = await click()
    assert result["server"]["error"] == {"code": "internal_error", "message": "erreur interne du serveur"}
    failed = [record.getMessage() for record in caplog.records if "failed" in record.getMessage()]
    assert len(failed) == 1 and "CdpError" in failed[0] and "-32000" in failed[0], failed
    assert "texte témoin" not in caplog.text and "Traceback" not in caplog.text
    assert "texte témoin" not in dumped(result)


async def test_tool_returning_a_bad_envelope_becomes_an_internal_error() -> None:
    @tool("status")
    async def status() -> ToolOutput:
        return ToolOutput(server={"x": object()})

    result, image = await status()
    assert result["server"]["error"]["code"] == "internal_error"
    assert image is None


# --- operating log ---------------------------------------------------------------


def test_operating_log_goes_to_stderr_only_and_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = io.StringIO()
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr)
    monkeypatch.setattr("sys.stdout", stdout)
    root = logging.getLogger()
    saved = list(root.handlers), root.level
    try:
        stray = logging.StreamHandler(stdout)
        root.addHandler(stray)
        handler = output.configure_logging()
        assert output.configure_logging() is handler
        register_secret(SECRET)
        logger = logging.getLogger("mcp_server_diwall_browser.test")
        logger.info("value %s", SECRET)
        try:
            raise ValueError(SECRET)
        except ValueError:
            logger.exception("failure")
        handler.flush()
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    assert stdout.getvalue() == ""
    assert "value <redacted>" in stderr.getvalue()
    assert SECRET not in stderr.getvalue()
    assert root.handlers == saved[0]


# --- the operation identifier seen inside a tool -------------------------------------------------

Decorator = Callable[[str], Callable[..., Any]]


async def operation_id_is_shared(decorate: Decorator) -> bool:
    seen: list[str | None] = []

    @decorate("observe")
    async def observe() -> ToolOutput:
        seen.append(output.current_operation_id())
        return ToolOutput()

    result, _image = await observe()
    return seen == [result["server"]["operation_id"]] and output.current_operation_id() is None


def two_identifiers(name: str) -> Callable[..., Any]:
    # Defective: the journal side and the result side get different identifiers.
    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            token = output._current_operation_id.set(output.new_operation_id())
            try:
                return await output._run(func, name, output.new_operation_id(), args, kwargs)
            finally:
                output._current_operation_id.reset(token)

        return wrapper

    return decorate


async def test_the_operation_identifier_inside_a_tool_is_the_one_returned() -> None:
    assert await operation_id_is_shared(tool)


async def test_operation_identifier_check_catches_two_identifiers() -> None:
    assert not await operation_id_is_shared(two_identifiers)


# --- page text made fit to show -------------------------------------------------------------------


def text_is_fit(bound: Callable[[object, int], str]) -> bool:
    cases = [
        ("Pay\u202enow\x00", 200, "Paynow"),
        ("a\tb\n\nc", 200, "a b c"),
        ("x" * 300, 200, "x" * 199 + "…"),
        (None, 200, ""),
        (42, 200, ""),
    ]
    return all(bound(text, limit) == expected for text, limit, expected in cases)


def keeps_format_characters(text: object, limit: int) -> str:
    # Defective: removes control characters only, and lets bidirectional overrides through.
    if not isinstance(text, str):
        return ""
    kept = "".join(" " if c.isspace() else c for c in text if c.isspace() or unicodedata.category(c) != "Cc")
    collapsed = " ".join(kept.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def test_page_text_is_cleaned_and_bounded() -> None:
    assert text_is_fit(output.bounded_text)


def test_text_check_catches_a_cleaner_that_keeps_format_characters() -> None:
    assert not text_is_fit(keeps_format_characters)


async def test_a_decorated_tool_carries_its_name_for_the_server_to_check() -> None:
    from mcp_server_diwall_browser.output import EXIT_POINT_ATTRIBUTE, ToolOutput, tool

    @tool("example")
    async def run() -> ToolOutput:
        return ToolOutput()

    async def plain() -> ToolOutput:
        return ToolOutput()

    assert getattr(run, EXIT_POINT_ATTRIBUTE) == "example"
    assert getattr(plain, EXIT_POINT_ATTRIBUTE, None) is None


async def test_refused_arguments_have_their_own_code() -> None:
    from mcp_server_diwall_browser.output import ToolError, ToolOutput, tool

    @tool("example")
    async def run() -> ToolOutput:
        raise ToolError("invalid_arguments", "paramètres refusés : approved (inconnu)")

    envelope, _image = await run()
    assert envelope["ok"] is False and envelope["server"]["error"]["code"] == "invalid_arguments"


def test_a_query_name_written_with_a_lookalike_letter_is_redacted() -> None:
    assert sanitize_url("https://example.test/a?t\u043eken=abc&page=2") == "https://example.test/a?t\u043eken=<redacted>&page=2"
