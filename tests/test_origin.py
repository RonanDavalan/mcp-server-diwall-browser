from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from mcp_server_diwall_browser.origin import (
    Origin,
    OriginError,
    OriginPattern,
    form_target_origin,
    is_blank,
    origin_allowed,
    origin_of_url,
    parse_origin,
    parse_origin_pattern,
    split_host_port,
)

# (address, expected canonical origin or the expected refusal reason)
URL_CASES: list[tuple[str, str]] = [
    ("HTTPS://EXAMPLE.test", "https://example.test"),
    ("https://example.test.", "https://example.test"),
    ("https://example.test:443", "https://example.test"),
    ("https://example.test:8443", "https://example.test:8443"),
    ("http://example.test", "http://example.test"),
    ("http://example.test:80", "http://example.test"),
    ("https://example.test@evil.test", "error:userinfo"),
    ("https://evil.test@example.test", "error:userinfo"),
    ("https://alice:pw@example.test/", "error:userinfo"),
    ("https://example.test.evil.test", "https://example.test.evil.test"),
    ("https://account.example.test", "https://account.example.test"),
    ("https://bücher.test", "https://xn--bcher-kva.test"),
    ("https://xn--bcher-kva.test", "https://xn--bcher-kva.test"),
    ("https:\\\\example.test", "error:malformed"),
    ("https://example.test\\@evil.test", "error:malformed"),
    ("https://example.test/\tpath", "error:control_character"),
    ("https://example.test/\npath", "error:control_character"),
    ("https://exa\nmple.test", "error:control_character"),
    ("https://example.test/a b", "error:malformed"),
    (" https://example.test", "error:malformed"),
    ("//example.test", "error:malformed"),
    ("https://", "error:empty_host"),
    ("https://:443", "error:empty_host"),
    ("https://example.test:0", "error:invalid_port"),
    ("https://example.test:65536", "error:invalid_port"),
    ("https://example.test:", "error:invalid_port"),
    ("https://example.test:+443", "error:invalid_port"),
    ("https://[::1]:8080", "https://[::1]:8080"),
    ("https://[0:0:0:0:0:0:0:1]", "https://[::1]"),
    ("https://[fe80::1%25eth0]", "error:invalid_host"),
    ("https://127.0.0.1", "https://127.0.0.1"),
    ("https://0x7f.1", "error:invalid_host"),
    ("https://2130706433", "error:invalid_host"),
    ("https://0177.0.0.1", "error:invalid_host"),
    ("https://127.1", "error:invalid_host"),
    ("https://256.0.0.1", "error:invalid_host"),
    ("https://exa%6dple.test", "error:invalid_host"),
    ("https://*.example.test", "error:wildcard"),
    ("file:///etc/passwd", "error:forbidden_scheme"),
    ("javascript:alert(1)", "error:forbidden_scheme"),
    ("data:text/html,x", "error:forbidden_scheme"),
    ("blob:https://example.test/uuid", "error:forbidden_scheme"),
    ("chrome://settings", "error:forbidden_scheme"),
    ("chrome-extension://abcdef/page.html", "error:forbidden_scheme"),
    ("view-source:https://example.test", "error:forbidden_scheme"),
    ("about:blank", "error:forbidden_scheme"),
    ("about:config", "error:forbidden_scheme"),
    ("https://example.test/path?q=1#frag", "https://example.test"),
    ("https://example.test?q=@evil.test", "https://example.test"),
    ("https://example.test#@evil.test", "https://example.test"),
]

# (left, right, same origin?)
SAME_ORIGIN_CASES: list[tuple[str, str, bool]] = [
    ("https://example.test", "https://example.test:443", True),
    ("https://example.test", "HTTPS://Example.Test/", True),
    ("https://example.test", "https://example.test./login", True),
    ("https://bücher.test", "https://xn--bcher-kva.test", True),
    ("https://example.test", "https://example.test:8443", False),
    ("https://example.test", "http://example.test", False),
    ("https://example.test", "https://account.example.test", False),
    ("https://example.test", "https://example.test.evil.test", False),
    ("https://example.test", "https://example.test@evil.test", False),
    ("https://example.test", "https://evil.test@example.test", False),
]


@pytest.mark.parametrize(("url", "expected"), URL_CASES)
def test_origin_of_url_normalizes_or_refuses(url: str, expected: str) -> None:
    if expected.startswith("error:"):
        with pytest.raises(OriginError) as caught:
            origin_of_url(url)
        assert caught.value.reason == expected.removeprefix("error:")
    else:
        assert origin_of_url(url).canonical == expected


Comparator = Callable[[str, str], bool]


def real_same_origin(left: str, right: str) -> bool:
    try:
        return parse_origin(left) == origin_of_url(right)
    except OriginError:
        return False


def raw_text_same_origin(left: str, right: str) -> bool:
    # Defective: compares the addresses as text.
    return left == right


def prefix_same_origin(left: str, right: str) -> bool:
    # Defective: a site "starts with" the allowed origin.
    return right.startswith(left)


def origin_table_holds(compare: Comparator) -> bool:
    return all(compare(left, right) is expected for left, right, expected in SAME_ORIGIN_CASES)


def test_origin_comparison_table_holds_for_the_real_parser() -> None:
    assert origin_table_holds(real_same_origin)


@pytest.mark.parametrize("defective", [raw_text_same_origin, prefix_same_origin])
def test_origin_comparison_table_fails_on_text_comparisons(defective: Comparator) -> None:
    assert not origin_table_holds(defective)


def test_default_port_is_part_of_equality_and_canonical_form() -> None:
    assert origin_of_url("https://example.test:443") == origin_of_url("https://example.test")
    assert origin_of_url("https://example.test:443") == Origin("https", "example.test", 443)
    assert origin_of_url("https://example.test:8443") != origin_of_url("https://example.test")
    assert str(Origin("http", "example.test", 8080)) == "http://example.test:8080"
    assert Origin("https", "::1", 443).canonical == "https://[::1]"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("https://*.example.test", "wildcard"),
        ("https://example.test/", "trailing_data"),
        ("https://example.test/path", "trailing_data"),
        ("https://example.test?x=1", "trailing_data"),
        ("https://example.test#x", "trailing_data"),
        ("https://alice@example.test", "userinfo"),
        ("example.test", "malformed"),
        ("ftp://example.test", "forbidden_scheme"),
    ],
)
def test_parse_origin_accepts_only_scheme_host_and_port(text: str, reason: str) -> None:
    with pytest.raises(OriginError) as caught:
        parse_origin(text)
    assert caught.value.reason == reason


def test_parse_origin_accepts_a_port() -> None:
    assert parse_origin("https://account.example.invalid:8443") == Origin("https", "account.example.invalid", 8443)


def test_about_blank_is_blank_but_has_no_origin() -> None:
    assert is_blank("about:blank")
    assert not is_blank("about:blank#x")
    assert not is_blank("https://example.test")
    with pytest.raises(OriginError):
        origin_of_url("about:blank")


@pytest.mark.parametrize(
    "fields",
    [
        ("ftp", "example.test", 21),
        ("https", "EXAMPLE.test", 443),
        ("https", "example.test", 0),
        ("https", "example.test", True),
        ("https", "", 443),
        ("https", "0x7f.1", 443),
    ],
)
def test_hand_built_origin_cannot_hold_a_value_the_parser_refuses(fields: tuple[str, str, int]) -> None:
    with pytest.raises(OriginError):
        Origin(*fields)


HAND_BUILT = [
    ("ftp", "example.test", 21),
    ("https", "EXAMPLE.test", 443),
    ("https", "example.test", 0),
    ("https", "example.test", True),
    ("https", "", 443),
    ("https", "0x7f.1", 443),
]


def hand_built_origins_refused(origin_class: Callable[..., object]) -> bool:
    for fields in HAND_BUILT:
        try:
            origin_class(*fields)
        except OriginError:
            continue
        return False
    return True


@dataclass(frozen=True)
class _UncheckedOrigin:
    """Defective: the same fields, without the checks of the real class."""

    scheme: str
    host: str
    port: int


def test_hand_built_origins_are_checked() -> None:
    assert hand_built_origins_refused(Origin)


def test_origin_check_catches_a_class_without_validation() -> None:
    assert not hand_built_origins_refused(_UncheckedOrigin)


# --- non-Latin host names -------------------------------------------------------------------

# Hosts on which IDNA 2003 (Python) and IDNA 2008 (browsers) disagree, or that
# the conversion maps silently: each must be refused, never turned into
# another domain.
DIVERGENT_HOSTS = [
    "https://faß.test",
    "https://straße.example.test",
    "https://ς.test",
    "https://a\u200db.test",
    "https://a\u200cb.test",
    "https://\uff45xample.test",
    "https://xn--fa-hia.test",
]

UNAMBIGUOUS_HOSTS = [
    ("https://café.test", "https://xn--caf-dma.test"),
    ("https://cafe\u0301.test", "https://xn--caf-dma.test"),
    ("https://MÜNCHEN.test", "https://xn--mnchen-3ya.test"),
    ("https://bücher.example.test", "https://xn--bcher-kva.example.test"),
]

HostParser = Callable[[str], Origin]


def divergent_hosts_are_refused(parse: HostParser) -> bool:
    for url in DIVERGENT_HOSTS:
        try:
            parse(url)
        except OriginError as error:
            if error.reason != "invalid_host":
                return False
        else:
            return False
    return all(parse(url).canonical == canonical for url, canonical in UNAMBIGUOUS_HOSTS)


def idna_2003_only(url: str) -> Origin:
    # Defective: converts with IDNA 2003 and keeps whatever comes out.
    host = url.split("://", 1)[1]
    try:
        converted = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise OriginError("invalid_host") from None
    return Origin("https", converted, 443)


def test_a_host_the_two_idna_versions_read_differently_is_refused() -> None:
    assert divergent_hosts_are_refused(parse_origin)
    with pytest.raises(OriginError) as caught:
        parse_origin("https://faß.test")
    assert caught.value.reason == "invalid_host"


def test_host_check_catches_a_parser_that_trusts_idna_2003() -> None:
    assert not divergent_hosts_are_refused(idna_2003_only)
    assert idna_2003_only("https://faß.test").canonical == "https://fass.test"


# --- allowance of an origin -------------------------------------------------------------------

ALLOWED = [parse_origin("https://example.test"), parse_origin("https://shop.example.test:8443")]
ALLOWANCE_CASES: list[tuple[Origin | None, list[Origin], bool]] = [
    (parse_origin("https://example.test"), ALLOWED, True),
    (parse_origin("https://shop.example.test:8443"), ALLOWED, True),
    (parse_origin("https://example.test:8443"), ALLOWED, False),
    (parse_origin("http://example.test"), ALLOWED, False),
    (parse_origin("https://example.test.evil.test"), ALLOWED, False),
    (parse_origin("https://evil.example.test"), ALLOWED, False),
    (None, ALLOWED, False),
    (parse_origin("https://example.test"), [], False),
]

Allowance = Callable[[Origin | None, list[Origin]], bool]


def allowance_table_holds(allows: Allowance) -> bool:
    return all(allows(origin, allowed) is expected for origin, allowed, expected in ALLOWANCE_CASES)


def prefix_allowance(origin: Origin | None, allowed: list[Origin]) -> bool:
    # Defective: compares the beginning of the texts.
    return origin is not None and any(str(origin).startswith(str(item)) for item in allowed)


def test_an_origin_is_allowed_only_when_equal_to_one_of_the_list() -> None:
    assert allowance_table_holds(origin_allowed)


def test_allowance_check_catches_a_prefix_comparison() -> None:
    assert not allowance_table_holds(prefix_allowance)


# --- le joker de sous-domaine ---------------------------------------------------------------

WILDCARD = "https://*.example.test"


@pytest.mark.parametrize("url", ["https://a.example.test", "https://a.b.example.test", "HTTPS://A.Example.TEST/page?x=1"])
def test_a_wildcard_covers_a_subdomain_of_one_or_several_levels(url: str) -> None:
    assert origin_allowed(origin_of_url(url), [parse_origin_pattern(WILDCARD)])


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test",
        "https://example.test.autre.test",
        "https://notexample.test",
        "http://a.example.test",
        "https://a.example.test:8443",
        "https://a.example.test.autre.test",
    ],
)
def test_a_wildcard_covers_nothing_else(url: str) -> None:
    assert not origin_allowed(origin_of_url(url), [parse_origin_pattern(WILDCARD)])


def test_a_wildcard_keeps_its_port_and_scheme() -> None:
    pattern = parse_origin_pattern("http://*.example.test:8080")
    assert origin_allowed(origin_of_url("http://a.example.test:8080"), [pattern])
    assert not origin_allowed(origin_of_url("http://a.example.test"), [pattern])
    assert not origin_allowed(origin_of_url("https://a.example.test:8080"), [pattern])
    assert pattern.canonical == "http://*.example.test:8080"


def test_a_wildcard_is_written_as_it_was_given() -> None:
    assert parse_origin_pattern("HTTPS://*.Example.TEST:443").canonical == WILDCARD
    assert str(parse_origin_pattern(WILDCARD)) == WILDCARD


def test_an_entry_without_wildcard_is_the_exact_origin() -> None:
    assert parse_origin_pattern("https://example.test:443") == Origin("https", "example.test", 443)


def test_a_list_mixes_exact_entries_and_wildcards() -> None:
    entries = [parse_origin_pattern("https://example.test"), parse_origin_pattern(WILDCARD)]
    assert origin_allowed(origin_of_url("https://example.test"), entries)
    assert origin_allowed(origin_of_url("https://a.example.test"), entries)
    assert not origin_allowed(None, entries)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("*", "malformed"),
        ("https://*", "wildcard"),
        ("https://*.test", "wildcard"),
        ("https://a.*.test", "wildcard"),
        ("https://*.*.example.test", "wildcard"),
        ("https://*example.test", "wildcard"),
        ("https://a.example.*", "wildcard"),
        ("*.example.test", "malformed"),
        ("https://*.example.test/", "trailing_data"),
        ("https://*.example.test/path", "trailing_data"),
        ("https://alice@*.example.test", "userinfo"),
        ("https://*.1.2.3.4", "invalid_host"),
        ("https://*.[::1]", "invalid_host"),
        ("ftp://*.example.test", "forbidden_scheme"),
        ("https://*.example.test:0", "invalid_port"),
    ],
)
def test_a_wildcard_in_a_forbidden_place_is_refused(text: str, reason: str) -> None:
    with pytest.raises(OriginError) as caught:
        parse_origin_pattern(text)
    assert caught.value.reason == reason


def test_an_exact_origin_is_never_a_wildcard() -> None:
    with pytest.raises(OriginError) as caught:
        parse_origin(WILDCARD)
    assert caught.value.reason == "wildcard"
    with pytest.raises(OriginError):
        origin_of_url("https://*.example.test/page")


def test_a_pattern_built_by_hand_is_checked_as_the_parsed_one() -> None:
    assert OriginPattern("https", "example.test", 443) == parse_origin_pattern(WILDCARD)
    for fields in (("https", "test", 443), ("ftp", "example.test", 443), ("https", "*.example.test", 443), ("https", "Example.test", 443), ("https", "example.test", 0)):
        with pytest.raises(OriginError):
            OriginPattern(*fields)


@pytest.mark.parametrize(
    ("hostport", "expected"),
    [("127.0.0.1:9222", ("127.0.0.1", "9222")), ("[::1]:9222", ("[::1]", "9222")), ("example.test", ("example.test", None))],
)
def test_split_host_port(hostport: str, expected: tuple[str, str | None]) -> None:
    assert split_host_port(hostport) == expected


@pytest.mark.parametrize("hostport", ["[::1:9222", "[::1]x9222", "a[b]:1"])
def test_split_host_port_refuses_what_a_browser_could_read_otherwise(hostport: str) -> None:
    with pytest.raises(OriginError):
        split_host_port(hostport)


# --- where a form sends ------------------------------------------------------------------------------

PAGE = "https://shop.example.test/cart/checkout?step=3"
FORM_TARGETS: list[tuple[str | None, str | None]] = [
    (None, "https://shop.example.test"),
    ("", "https://shop.example.test"),
    ("pay", "https://shop.example.test"),
    ("/pay?x=1", "https://shop.example.test"),
    ("?step=4", "https://shop.example.test"),
    ("https://pay.example.test/session", "https://pay.example.test"),
    ("HTTPS://PAY.EXAMPLE.TEST:443/", "https://pay.example.test"),
    ("//bank.example.test/3ds", "https://bank.example.test"),
    ("http://shop.example.test/pay", "http://shop.example.test"),
    ("javascript:submit()", None),
    ("https://user:pw@pay.example.test/", None),
]

TargetReader = Callable[[str, str | None], Origin | None]


def form_target_table_holds(read: TargetReader) -> bool:
    for action, expected in FORM_TARGETS:
        found = read(PAGE, action)
        if (None if found is None else found.canonical) != expected:
            return False
    return True


def page_origin_always(page_url: str, action: str | None) -> Origin | None:
    # Defective: assumes a form always sends to the page's origin.
    return origin_of_url(page_url)


def test_the_target_of_a_form_is_read_against_the_page() -> None:
    assert form_target_table_holds(form_target_origin)
    assert form_target_origin("about:blank", "/pay") is None


def test_form_target_check_catches_a_reader_that_ignores_the_action() -> None:
    assert not form_target_table_holds(page_origin_always)
