from __future__ import annotations

import asyncio
import base64
import io
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import FAKE_PASSWORD
from fixtures.fake_cdp import NO_REPLY, FakeBrowser, FakeCdp, settle
from fixtures.page_model import El, Page, button, div, field_input, hidden, link, text
from PIL import Image

from mcp_server_diwall_browser import perception
from mcp_server_diwall_browser.browser import BrowserAttachment, Tab, TabRegistry
from mcp_server_diwall_browser.origin import parse_origin
from mcp_server_diwall_browser.output import ToolError, finalize, register_secret
from mcp_server_diwall_browser.perception import (
    MASK_COLOUR,
    MASK_TEXT_COLOUR,
    MARK_COLOUR,
    Box,
    Dom,
    Perception,
    build,
    render,
)

ORIGIN = "https://example.test"
URL = "https://example.test/account/page?token=abc"
CARD_NUMBER = "4970101234567890"
SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "snapshots"
VIEWPORT = Box(0, 0, 800, 600)


@pytest.fixture
async def fake() -> AsyncIterator[FakeCdp]:
    server = FakeCdp()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def fake_browser(fake: FakeCdp) -> FakeBrowser:
    return FakeBrowser(fake)


@pytest.fixture
async def registry(fake: FakeCdp, fake_browser: FakeBrowser, tmp_path: Path) -> AsyncIterator[TabRegistry]:
    profile = SimpleNamespace(browser=SimpleNamespace(family="chromium", profile_dir=str(tmp_path / "p"), debug_port=fake.port))
    attachment = await BrowserAttachment.attach(profile, home=str(tmp_path))
    yield TabRegistry(attachment.connection)
    await attachment.close()


def perceiver(registry: TabRegistry, origins: list[str] | None = None, **kwargs: Any) -> Perception:
    allowed = [parse_origin(o) for o in ([ORIGIN] if origins is None else origins)]
    return Perception(registry, allowed_origins=lambda: allowed, **kwargs)


async def showing(fake_browser: FakeBrowser, registry: TabRegistry, page: Page, url: str = URL) -> Tab:
    tab = await registry.open_tab()
    fake_browser.show(tab.target_id, page, url)
    return tab


def built(page: Page, viewport: Box | None = None) -> perception.Built:
    x, y, width, height = page.viewport
    return build(page.ax_tree(), Dom(page.dom_snapshot()), viewport or Box(x, y, width, height), parse_origin(ORIGIN))


def names(result: perception.Built) -> list[str]:
    return [element.public["name"] for element in result.elements]


def serialized(observation: perception.Observation) -> str:
    """The whole output as it leaves the server, the image excepted."""
    envelope = finalize({"ok": True, "server": observation.server, "page_content": observation.page_content})
    return json.dumps(envelope, ensure_ascii=False)


def colours(png: bytes, box: Box, scale: float = 1.0, viewport: Box = VIEWPORT) -> set[tuple[int, int, int]]:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    left = int((box.x - viewport.x) * scale)
    top = int((box.y - viewport.y) * scale)
    right = int((box.right - viewport.x) * scale)
    bottom = int((box.bottom - viewport.y) * scale)
    return set(image.crop((left, top, right, bottom)).getdata())


# --- the hand-written snapshot ----------------------------------------------------------------


def test_the_hand_written_snapshot_is_read_as_the_browser_writes_it() -> None:
    data = json.loads((SNAPSHOTS / "login.json").read_text())
    viewport = Box(0, 0, 400, 200)
    result = build(data["ax"], Dom(data["snapshot"]), viewport, parse_origin(ORIGIN))
    assert result.tree_lines == ['RootWebArea "Login"', '  StaticText "Password"', '  textbox "Password"', '  button "Sign in"']
    password, sign_in = result.elements
    assert password.public == {"mark": 1, "role": "textbox", "name": "Password", "value": "", "state": ["required"], "in_viewport": True}
    assert password.sensitive and password.tag == "input" and password.input_type == "password"
    assert password.form_backend_id == 10 and sign_in.form_backend_id == 10
    assert sign_in.is_submit_like and not password.is_submit_like
    assert result.counters["redacted_fields"] == 1
    assert result.sensitive_boxes == [Box(78, 8, 200, 22)]
    assert FAKE_PASSWORD not in json.dumps([e.public for e in result.elements] + result.tree_lines)
    # The field's inner text, bullets here, is part of its value: never visited.
    assert not any("•" in line for line in result.tree_lines)


# --- visibility, one condition at a time -------------------------------------------------------

CONTROL = button("Control", (10, 10, 100, 20))


def single(failing: El, *, content_size: tuple[float, float] = (800, 600)) -> Page:
    return Page("Visibility", [CONTROL, failing], content_size=content_size)


CONDITIONS: list[tuple[str, El]] = [
    ("1 no box, in the tree", replace(button("No box", (10, 40, 100, 20)), box=None, children=[text("No box", None)])),
    ("1 display none, out of the tree", hidden(button("Display none", (10, 40, 100, 20)))),
    ("2 visibility hidden", button("Hidden", (10, 40, 100, 20), visibility="hidden")),
    ("2 visibility collapse", button("Collapse", (10, 40, 100, 20), visibility="collapse")),
    ("3 opacity 0", button("Transparent", (10, 40, 100, 20), opacity="0")),
    ("3 opacity 0.01", button("Nearly transparent", (10, 40, 100, 20), opacity="0.01")),
    ("3 opacity of a parent", div(button("Inherited", (10, 40, 100, 20)), box=(0, 40, 400, 30), opacity="0", role="generic")),
    ("3 product of opacities", div(button("Product", (10, 40, 100, 20), opacity="0.1"), box=(0, 40, 400, 30), opacity="0.1")),
    ("4 one pixel wide", button("Thin", (10, 40, 1, 20))),
    ("4 one pixel high", button("Flat", (10, 40, 100, 1))),
    ("5 left of the page", button("Left", (-9999, 40, 100, 20))),
    ("5 above the page", button("Above", (10, -500, 100, 20))),
    ("5 beyond the width", button("Beyond width", (900, 40, 100, 20))),
    ("5 beyond the height", button("Beyond height", (10, 700, 100, 20))),
    ("6 aria-hidden", replace(button("Aria hidden", (10, 40, 100, 20)), ignored=True, ignored_reasons=("ariaHiddenElement",))),
    ("6 inert", replace(button("Inert", (10, 40, 100, 20)), ignored=True, ignored_reasons=("inertElement",))),
]


@pytest.mark.parametrize(("case", "failing"), CONDITIONS, ids=[c[0] for c in CONDITIONS])
def test_each_visibility_condition_alone_hides_the_node(case: str, failing: El) -> None:
    result = built(single(failing))
    assert names(result) == ["Control"], case
    assert [line.strip() for line in result.tree_lines] == ['RootWebArea "Visibility"', 'button "Control"'], case
    assert result.counters["omitted_not_visible"] == 1, case


@pytest.mark.parametrize(
    "shown",
    [
        button("Half", (10, 40, 100, 20), opacity="0.5"),
        div(button("Child of translucent", (10, 40, 100, 20)), box=(0, 40, 400, 30), opacity="0.5"),
        El("BUTTON", role="button", name="Two pixels", box=(10, 40, 2, 2)),
        button("Partly left", (-50, 40, 100, 20)),
        button("Bottom of the page", (10, 590, 100, 10)),
    ],
)
def test_nodes_that_pass_every_condition_are_shown(shown: El) -> None:
    result = built(single(shown))
    assert len(result.elements) == 2
    assert result.counters["omitted_not_visible"] == 0


def test_text_carries_its_parents_style_and_is_not_counted_twice() -> None:
    # The browser gives a text node the style of its parent: an opacity of
    # 0.1 must not become 0.01 by counting the parent twice.
    page = Page("Text", [El("P", role="paragraph", box=(10, 10, 300, 20), opacity="0.1", children=[text("Faint text", (10, 10, 100, 16))])])
    assert 'StaticText "Faint text"' in [line.strip() for line in built(page).tree_lines]


def test_the_visibility_page_shows_only_the_visible_button() -> None:
    page = Page(
        "Visibility test page",
        [
            hidden(button("Display none button", (16, 0, 120, 21))),
            button("Transparent button", (16, 16, 128, 21), opacity="0"),
            div(button("Inherited transparent button", (16, 45, 180, 21)), box=(16, 45, 748, 21), opacity="0", role="generic"),
            button("Thin button", (16, 74, 1, 15)),
            button("Off page button", (-9999, 48, 107, 21)),
            replace(
                button("Aria hidden button", (16, 97, 124, 21)),
                ignored=True,
                ignored_reasons=("ariaHiddenElement",),
                children=[replace(text("Aria hidden button", (24, 100, 108, 15)), ignored=True, ignored_reasons=("ariaHiddenSubtree",))],
            ),
            button("Visible button", (16, 126, 96, 21)),
        ],
    )
    result = built(page)
    assert names(result) == ["Visible button"]
    assert result.tree_lines == ['RootWebArea "Visibility test page"', '  button "Visible button"']
    assert result.counters["omitted_not_visible"] == 6


def keep_every_tree_node(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defective: whatever the accessibility tree lists is shown.
    monkeypatch.setattr(Dom, "visible", lambda self, node: node.box is not None)


def only_visible_shown(page: Page) -> bool:
    result = built(page)
    return names(result) == ["Control"] and all("Transparent" not in line for line in result.tree_lines)


def test_visibility_check_catches_a_version_that_keeps_every_tree_node(monkeypatch: pytest.MonkeyPatch) -> None:
    page = single(button("Transparent", (10, 40, 100, 20), opacity="0"))
    assert only_visible_shown(page)
    keep_every_tree_node(monkeypatch)
    assert not only_visible_shown(page)


def test_a_box_without_its_own_layout_lets_its_children_speak() -> None:
    # display: contents gives no box to the element, only to its children.
    page = Page("Contents", [El("DIV", role="generic", box=None, display="contents", children=[button("Inside", (10, 10, 100, 20))])])
    result = built(page)
    assert names(result) == ["Inside"]
    assert result.counters["omitted_not_visible"] == 0


def test_never_rendered_elements_are_not_counted_as_hidden() -> None:
    page = Page("Scripts", [El("SCRIPT", box=None, in_ax=False), El("TEMPLATE", box=None, in_ax=False), CONTROL])
    assert built(page).counters["omitted_not_visible"] == 0


# --- marks ---------------------------------------------------------------------------------


def test_markable_roles_and_clickable_generic_components() -> None:
    page = Page(
        "Marks",
        [
            link("A link", (10, 10, 80, 16)),
            El("DIV", role="generic", name="", box=(10, 30, 120, 30), clickable=True, children=[text("Clickable div", (12, 32, 90, 16))]),
            El("DIV", role="generic", name="", box=(10, 70, 120, 30), children=[text("Plain div", (12, 72, 90, 16))]),
            El("LABEL", role="LabelText", box=(10, 110, 120, 20), clickable=True, children=[text("A label", (12, 112, 60, 16))]),
            field_input("Search", (10, 140, 200, 20), role="searchbox"),
            field_input("Agree", (10, 170, 20, 20), attrs={"type": "checkbox"}, role="checkbox", value="on", properties={"checked": "true", "focused": True, "disabled": False}),
            El("LI", role="option", name="First", box=(10, 200, 100, 20)),
        ],
    )
    result = built(page)
    assert [(e.mark, e.role) for e in result.elements] == [(1, "link"), (2, "generic"), (3, "searchbox"), (4, "checkbox"), (5, "option")]
    clickable = result.elements[1].public
    assert clickable == {"mark": 2, "role": "generic", "name": "", "value": "", "in_viewport": True, "clickable_generic": True}
    checkbox = result.elements[3].public
    assert checkbox["value"] == "" and checkbox["state"] == ["checked", "focused"]
    assert "state" not in result.elements[0].public and "clickable_generic" not in result.elements[0].public


def test_marks_follow_document_order_not_tree_order() -> None:
    first, second = button("First in document", (10, 10, 100, 20)), button("Second in document", (10, 40, 100, 20))
    page = Page("Order", [first, second])
    ax = page.ax_tree()
    body = next(node for node in ax["nodes"] if node["nodeId"] == "6")
    body["childIds"].reverse()  # aria-owns can reorder the tree
    result = build(ax, Dom(page.dom_snapshot()), VIEWPORT, parse_origin(ORIGIN))
    assert names(result) == ["First in document", "Second in document"]


def test_more_than_150_marks_keeps_the_viewport_first_then_document_order() -> None:
    links = [link(f"Link {i}", (10, 1000 + 20 * i, 80, 16)) for i in range(180)]
    links += [link(f"Visible {i}", (10, 20 * i, 80, 16)) for i in range(20)]
    page = Page("Many", links, content_size=(800, 6000))
    result = built(page)
    kept = names(result)
    assert len(kept) == 150 and result.counters["marks_truncated"] is True
    assert [f"Visible {i}" for i in range(20)] == kept[-20:]
    assert kept[:130] == [f"Link {i}" for i in range(130)]
    assert [e.mark for e in result.elements] == list(range(1, 151))


def test_element_reference_fields_for_the_gate() -> None:
    form = El(
        "FORM",
        role="generic",
        box=(0, 0, 600, 200),
        children=[
            field_input("Card", (10, 10, 200, 20), attrs={"type": "text", "autocomplete": "cc-number"}, value=CARD_NUMBER),
            button("Pay", (10, 40, 60, 20), attrs={"type": "submit"}),
            button("Help", (80, 40, 60, 20)),
        ],
    )
    outside = button("Outside", (10, 300, 80, 20), attrs={})
    result = built(Page("Checkout", [form, outside]))
    card, pay, helper, lonely = result.elements
    assert card.sensitive and card.form_has_card_field and card.input_type == "text"
    assert pay.is_submit_like and pay.form_backend_id == form.backend_id and pay.form_has_card_field
    assert not helper.is_submit_like
    assert lonely.form_backend_id is None and not lonely.form_has_card_field and not lonely.is_submit_like
    assert pay.name_hash == perception.name_hash("  PAY ") and pay.tag == "button" and pay.input_type is None
    assert pay.bounds == Box(10, 40, 60, 20) and pay.origin == parse_origin(ORIGIN)
    assert result.page_has_card_field is True


def test_a_hidden_card_field_arms_its_form_but_not_the_page() -> None:
    form = El(
        "FORM",
        role="generic",
        box=(0, 0, 600, 200),
        children=[hidden(field_input("Card", (10, 10, 200, 20), attrs={"autocomplete": "cc-number"})), button("Pay", (10, 40, 60, 20), attrs={"type": "submit"})],
    )
    result = built(Page("Checkout", [form]))
    [pay] = result.elements
    assert pay.form_has_card_field is True
    assert result.page_has_card_field is False


def test_names_and_values_are_cleaned_and_bounded() -> None:
    long_name = "N\x00a‮me " * 100
    page = Page("Clean", [button(long_name, (10, 10, 100, 20)), field_input("Note", (10, 40, 200, 20), value="v\x07" * 200)])
    result = built(page)
    name = result.elements[0].public["name"]
    value = result.elements[1].public["value"]
    assert len(name) <= 200 and "\x00" not in name and "‮" not in name and name.startswith("Name Name")
    assert len(value) <= 120 and "\x07" not in value
    assert set(result.elements[0].public) == {"mark", "role", "name", "value", "in_viewport"}


# --- values of sensitive fields ------------------------------------------------------------------

SENSITIVE_FIELDS = [
    ("type", {"type": "password"}),
    ("autocomplete card", {"type": "text", "autocomplete": "cc-number"}),
    ("name otp", {"type": "text", "name": "otp"}),
]


@pytest.mark.parametrize(("case", "attrs"), SENSITIVE_FIELDS, ids=[c[0] for c in SENSITIVE_FIELDS])
def test_a_filled_sensitive_field_keeps_its_name_and_loses_its_value(case: str, attrs: dict[str, str]) -> None:
    secret = "value-of-a-sensitive-field-3"
    page = Page("Sensitive", [field_input("Field name", (10, 10, 200, 20), attrs=attrs, value=secret, ax_value=secret), button("Go", (10, 40, 60, 20))])
    result = built(page)
    field_ref = result.elements[0]
    assert field_ref.sensitive and field_ref.public["name"] == "Field name" and field_ref.public["value"] == ""
    assert result.counters["redacted_fields"] == 1
    assert secret not in json.dumps([e.public for e in result.elements] + result.tree_lines)


def test_an_empty_sensitive_field_counts_nothing() -> None:
    page = Page("Empty", [field_input("Password", (10, 10, 200, 20), attrs={"type": "password"})])
    result = built(page)
    assert result.elements[0].sensitive and result.counters["redacted_fields"] == 0


def test_text_under_a_field_is_never_read() -> None:
    inner = El("DIV", role="generic", box=(12, 12, 180, 16), children=[text(FAKE_PASSWORD, (12, 12, 150, 16))])
    page = Page("Inner", [field_input("Password", (10, 10, 200, 20), attrs={"type": "password"}, value=FAKE_PASSWORD, children=[inner])])
    result = built(page)
    assert FAKE_PASSWORD not in json.dumps(result.tree_lines + [e.public for e in result.elements])


def test_plain_fields_show_their_value() -> None:
    page = Page(
        "Plain",
        [
            field_input("Comment", (10, 10, 200, 20), value="hello"),
            El("TEXTAREA", role="textbox", name="Story", box=(10, 40, 200, 60), text_value="once upon a time"),
            El("SELECT", role="combobox", name="Country", box=(10, 110, 200, 20), ax_value="France"),
        ],
    )
    assert [e.public["value"] for e in built(page).elements] == ["hello", "once upon a time", "France"]


def test_a_registered_secret_written_by_the_page_is_redacted_on_the_way_out() -> None:
    register_secret(FAKE_PASSWORD)
    page = Page("Echo", [El("P", role="paragraph", box=(10, 10, 300, 20), children=[text(f"You typed {FAKE_PASSWORD}", (10, 10, 300, 16))]), button(FAKE_PASSWORD, (10, 40, 100, 20))])
    result = built(page)
    assert FAKE_PASSWORD not in json.dumps(result.tree_lines + [e.public for e in result.elements])
    assert any("<redacted>" in line for line in result.tree_lines)


def publishes_no_sensitive_value(monkeypatch: pytest.MonkeyPatch | None) -> bool:
    page = Page("Login", [field_input("Password", (10, 10, 200, 20), attrs={"type": "password"}, value=FAKE_PASSWORD)])
    if monkeypatch is not None:
        # Defective: the value is published without asking the predicate.
        monkeypatch.setattr(perception, "_field_verdict", lambda node, ax_role: perception._Field(sensitive=False, card=False))
    result = built(page)
    return FAKE_PASSWORD not in json.dumps([e.public for e in result.elements] + result.tree_lines)


def test_value_check_catches_a_version_that_skips_the_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    assert publishes_no_sensitive_value(None)
    assert not publishes_no_sensitive_value(monkeypatch)


# --- tree ---------------------------------------------------------------------------------------


def test_tree_format_depth_headings_quotes_and_repeated_text() -> None:
    page = Page(
        "Tree",
        [
            El("H2", role="heading", name="Section", box=(10, 10, 300, 30), properties={"level": 2}, children=[text("Section", (10, 10, 100, 20))]),
            El(
                "NAV",
                role="navigation",
                name="Main",
                box=(10, 50, 300, 100),
                children=[link('Say "hi"\\now', (12, 52, 100, 16)), El("P", role="paragraph", box=(12, 80, 200, 20), children=[text("Line\nbreak\tand\x1bcontrol", (12, 80, 200, 16))])],
            ),
            field_input("Comment", (10, 200, 200, 20), value="not in the tree"),
        ],
    )
    lines = built(page).tree_lines
    assert lines == [
        'RootWebArea "Tree"',
        '  heading "Section" level=2',
        '  navigation "Main"',
        '    link "Say \\"hi\\"\\\\now"',
        '    StaticText "Line break andcontrol"',
        '  textbox "Comment"',
    ]
    assert "not in the tree" not in json.dumps(lines)


def test_tree_keeps_400_lines_viewport_first() -> None:
    paragraphs = [El("P", role="paragraph", box=(10, 1000 + 20 * i, 300, 16), children=[text(f"Far {i}", (10, 1000 + 20 * i, 100, 16))]) for i in range(450)]
    paragraphs += [El("P", role="paragraph", box=(10, 20 * i, 300, 16), children=[text(f"Near {i}", (10, 20 * i, 100, 16))]) for i in range(10)]
    result = built(Page("Long", paragraphs, content_size=(800, 12000)))
    assert len(result.tree_lines) == 400 and result.counters["tree_truncated"] is True
    assert result.counters["tree_lines"] == 400
    assert [line.strip() for line in result.tree_lines[-10:]] == [f'StaticText "Near {i}"' for i in range(10)]
    assert 'RootWebArea "Long"' in result.tree_lines


def test_a_failed_tree_is_withheld_with_a_notice() -> None:
    def broken(*args: Any) -> list[str]:
        raise RuntimeError("redaction failed")

    page = Page("Broken", [button("Still marked", (10, 10, 100, 20))])
    result = build(page.ax_tree(), Dom(page.dom_snapshot()), VIEWPORT, parse_origin(ORIGIN), tree_builder=broken)
    assert result.tree_lines == [] and result.counters["tree_lines"] == 0
    assert result.notices and result.notices[0].startswith("tree_unavailable")
    assert names(result) == ["Still marked"]


# --- frames ---------------------------------------------------------------------------------


def frame_page(*, frame_box: tuple[float, float, float, float] = (10, 60, 300, 100)) -> Page:
    inner = Page("Inner", [field_input("Frame password", (5, 5, 200, 20), attrs={"type": "password"}, value="frame-secret-value-2")], background=(255, 224, 138))
    return Page(
        "Frame",
        [button("Outside", (10, 10, 100, 20)), El("IFRAME", attrs={"title": "Login frame"}, role="Iframe", name="Login frame", box=frame_box, content=inner)],
        content_size=(800, 2000),
    )


def test_frame_content_is_never_read_and_a_visible_frame_is_masked() -> None:
    result = built(frame_page())
    assert result.counters["frames_masked"] == 1
    assert result.frame_boxes == [Box(10, 60, 300, 100)]
    assert "frame-secret-value-2" not in json.dumps(result.tree_lines + [e.public for e in result.elements])
    assert "Frame password" not in json.dumps(result.tree_lines + [e.public for e in result.elements])
    assert result.counters["redacted_fields"] == 0


def test_a_frame_outside_the_viewport_is_not_counted() -> None:
    assert built(frame_page(frame_box=(10, 900, 300, 100))).counters["frames_masked"] == 0


# --- image -------------------------------------------------------------------------------------


def login_page(**kwargs: Any) -> Page:
    return Page(
        "Login",
        [
            field_input("Username", (10, 10, 200, 24), value="alice"),
            field_input("Password", (10, 44, 200, 24), attrs={"type": "password"}, value=FAKE_PASSWORD),
            field_input("Card number", (10, 78, 200, 24), attrs={"type": "text", "autocomplete": "cc-number"}, value=CARD_NUMBER),
            button("Sign in", (10, 112, 80, 24)),
        ],
        **kwargs,
    )


async def test_masks_cover_every_pixel_of_the_sensitive_boxes(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    page = login_page()
    tab = await showing(fake_browser, registry, page)
    observation = await perceiver(registry).observe(tab)
    assert observation.server["capture"] == "ok" and observation.image_png is not None
    for box in (Box(10, 44, 200, 24), Box(10, 78, 200, 24)):
        assert colours(observation.image_png, box) == {MASK_COLOUR}
    # The plain field is not masked: its text is there.
    assert len(colours(observation.image_png, Box(10, 10, 200, 24))) > 1
    # Marks are drawn, around the masks.
    image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
    assert MARK_COLOUR[:3] in set(image.getdata())


def masks_hold(mask_drawer: Callable[..., None] | None, png: bytes, page: Page) -> bool:
    kwargs = {} if mask_drawer is None else {"mask_drawer": mask_drawer}
    result = built(page)
    rendered = render(png, VIEWPORT, sensitive_boxes=result.sensitive_boxes, frame_boxes=result.frame_boxes, marks=[(e.mark, e.bounds) for e in result.elements], **kwargs)
    return all(colours(rendered.png, box) == {MASK_COLOUR} for box in result.sensitive_boxes)


def test_mask_check_catches_masks_drawn_on_a_copy() -> None:
    page = login_page()
    png = base64.b64decode(page.screenshot()["data"])

    def on_a_copy(image: Image.Image, rect: tuple[int, int, int, int], kind: str) -> None:
        perception.draw_mask(image.copy(), rect, kind)

    assert masks_hold(None, png, page)
    assert not masks_hold(on_a_copy, png, page)


async def test_a_frame_mask_holds_only_its_fill_and_its_notice(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, frame_page())
    observation = await perceiver(registry).observe(tab)
    assert observation.image_png is not None
    found = colours(observation.image_png, Box(10, 60, 300, 100))
    assert found == {MASK_COLOUR, MASK_TEXT_COLOUR}


async def test_a_mask_that_fails_withholds_the_image_and_keeps_the_tree(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    def failing(image: Image.Image, rect: tuple[int, int, int, int], kind: str) -> None:
        raise OSError("cannot draw")

    tab = await showing(fake_browser, registry, login_page())
    observation = await perceiver(registry, mask_drawer=failing).observe(tab)
    assert observation.image_png is None and observation.snapshot is not None and observation.snapshot.image_png is None
    assert observation.server["capture"] == "unavailable"
    assert any(notice.startswith("capture_unavailable") for notice in observation.server["notices"])
    assert observation.page_content is not None and observation.page_content["tree"]
    assert FAKE_PASSWORD not in serialized(observation)
    with pytest.raises(ToolError) as caught:
        await perceiver(registry, mask_drawer=failing).capture_masked_png(tab)
    assert caught.value.code == "capture_unavailable"


@pytest.mark.parametrize(
    "broken_field",
    [
        # In the tree, shown, but without a box: it cannot be masked.
        replace(field_input("Password", (10, 44, 200, 24), attrs={"type": "password"}, value=FAKE_PASSWORD), box=None),
        # A box that is not a number.
        field_input("Password", (float("inf"), 44, 200, 24), attrs={"type": "password"}, value=FAKE_PASSWORD),
    ],
)
async def test_a_sensitive_field_without_a_usable_box_withholds_the_image(
    fake_browser: FakeBrowser, registry: TabRegistry, broken_field: El
) -> None:
    page = Page("Broken", [broken_field, button("Go", (10, 112, 80, 24))])
    tab = await showing(fake_browser, registry, page)
    observation = await perceiver(registry).observe(tab)
    assert observation.image_png is None and observation.server["capture"] == "unavailable"
    assert observation.page_content is not None and observation.page_content["elements"]


async def test_a_field_that_moves_during_the_capture_withholds_the_image(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    page = login_page()
    tab = await showing(fake_browser, registry, page)
    moved = login_page()
    moved.body[1] = replace(moved.body[1], box=(10, 300, 200, 24))

    def move(target_id: str) -> None:
        fake_browser.targets[target_id]["page"] = moved
        fake_browser.after.pop("Page.captureScreenshot")

    fake_browser.after["Page.captureScreenshot"] = move
    observation = await perceiver(registry).observe(tab)
    assert observation.image_png is None and observation.server["capture"] == "unavailable"


async def test_a_wide_image_is_reduced_and_marks_follow(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    target = Box(1000, 500, 200, 40)
    page = Page("Wide", [button("Target", (target.x, target.y, target.width, target.height)), field_input("Password", (1500, 100, 300, 40), attrs={"type": "password"}, value=FAKE_PASSWORD)], viewport=(0, 0, 2000, 1000), content_size=(2000, 1000))
    tab = await showing(fake_browser, registry, page)
    observation = await perceiver(registry).observe(tab)
    assert observation.image_png is not None
    image = Image.open(io.BytesIO(observation.image_png)).convert("RGB")
    assert image.size == (1600, 800)
    assert observation.server["image"] == {"width": 1600, "height": 800, "scale": 0.8}
    # The outline of the target sits 2 pixels outside its reduced box.
    assert image.getpixel((int(target.x * 0.8) - 1, int(target.y * 0.8) + 10)) == MARK_COLOUR[:3]
    assert image.getpixel((int(target.right * 0.8) + 0, int(target.y * 0.8) + 10)) == MARK_COLOUR[:3]
    full = render(base64.b64decode(page.screenshot()["data"]), Box(0, 0, 2000, 1000), sensitive_boxes=[], frame_boxes=[], marks=[(1, target)], max_width=None)
    reduced = render(base64.b64decode(page.screenshot()["data"]), Box(0, 0, 2000, 1000), sensitive_boxes=[], frame_boxes=[], marks=[(1, target)])
    assert full.mark_rects[1] == (1000, 500, 1200, 540)
    assert reduced.mark_rects[1] == (800, 400, 960, 432)
    assert colours(observation.image_png, Box(1500, 100, 300, 40), scale=0.8, viewport=Box(0, 0, 2000, 1000)) == {MASK_COLOUR}


async def test_capture_masked_png_has_the_masks_and_no_marks(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    png = await perceiver(registry).capture_masked_png(tab)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert MARK_COLOUR[:3] not in set(image.getdata())
    assert colours(png, Box(10, 44, 200, 24)) == {MASK_COLOUR}
    assert image.size == (800, 600)


# --- origin gate and second reading ------------------------------------------------------------

PERCEPTION_READS = ("Accessibility.", "DOMSnapshot.", "Page.captureScreenshot", "Page.getLayoutMetrics")


def content_reads(fake: FakeCdp) -> list[str]:
    return [m for m in fake.methods() if m.startswith(PERCEPTION_READS)]


@pytest.mark.parametrize(
    ("origins", "url", "label"),
    [
        (["https://other.example.test"], URL, "https://example.test"),
        ([], URL, "https://example.test"),
        ([ORIGIN], "about:blank", "about:blank"),
        ([ORIGIN], "chrome-error://chromewebdata/", None),
        ([ORIGIN], "http://example.test/page", "http://example.test"),
    ],
)
async def test_a_page_outside_the_allowed_sites_is_never_read(
    fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry, origins: list[str], url: str, label: str | None
) -> None:
    tab = await showing(fake_browser, registry, login_page(), url=url)
    with pytest.raises(ToolError) as caught:
        await perceiver(registry, origins).observe(tab)
    assert caught.value.code == "origin_not_allowed"
    assert caught.value.extra_server == {"origin": label}
    assert content_reads(fake) == []
    text = json.dumps([caught.value.message, caught.value.extra_server])
    for leak in ("Login", "/account", "token", "abc", "Username"):
        assert leak not in text


def read_then_check(perception_class: type[Perception]) -> type[Perception]:
    class ReadFirst(perception_class):  # type: ignore[valid-type,misc]
        """Defective: reads the page, then looks at its origin."""

        async def _read(self, tab: Tab, *, image: bool, full: bool) -> Any:
            await self._registry.read(tab, "DOMSnapshot.captureSnapshot", perception.SNAPSHOT_PARAMS)
            return await super()._read(tab, image=image, full=full)

    return ReadFirst


async def nothing_read_outside_the_list(fake: FakeCdp, registry: TabRegistry, fake_browser: FakeBrowser, perception_class: type[Perception]) -> bool:
    tab = await showing(fake_browser, registry, login_page())
    allowed = [parse_origin("https://other.example.test")]
    with pytest.raises(ToolError):
        await perception_class(registry, allowed_origins=lambda: allowed).observe(tab)
    return content_reads(fake) == []


async def test_gate_check_catches_a_version_that_reads_before_checking(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    assert await nothing_read_outside_the_list(fake, registry, fake_browser, Perception)
    assert not await nothing_read_outside_the_list(fake, registry, fake_browser, read_then_check(Perception))
    assert "DOMSnapshot.captureSnapshot" in fake.methods()


async def test_a_redirect_during_the_capture_publishes_nothing(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    seen: list[int] = []

    def redirect(target_id: str) -> None:
        # Every capture is followed by a move to another allowed page.
        seen.append(1)
        fake_browser.targets[target_id]["url"] = f"https://example.test/after-{len(seen)}"
        fake_browser.targets[target_id]["loader"] = f"MOVED{len(seen)}"

    fake_browser.after["DOMSnapshot.captureSnapshot"] = redirect
    percept = perceiver(registry)
    with pytest.raises(ToolError) as caught:
        await percept.observe(tab)
    assert caught.value.code == "page_changed_during_capture"
    assert caught.value.extra_server == {}
    with pytest.raises(ToolError):
        percept.snapshot(tab.tab_id, "0" * 32)
    assert percept._snapshots == {}


async def test_a_redirect_once_is_read_again_under_the_new_origin(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())

    def redirect_once(target_id: str) -> None:
        fake_browser.targets[target_id]["url"] = "https://bank.example.test/elsewhere"
        fake_browser.after.pop("DOMSnapshot.captureSnapshot")

    fake_browser.after["DOMSnapshot.captureSnapshot"] = redirect_once
    # The new origin is not allowed: the second attempt stops at the gate.
    with pytest.raises(ToolError) as caught:
        await perceiver(registry).observe(tab)
    assert caught.value.code == "origin_not_allowed"
    assert caught.value.extra_server == {"origin": "https://bank.example.test"}
    fake_browser.after["DOMSnapshot.captureSnapshot"] = redirect_once
    observation = await perceiver(registry, [ORIGIN, "https://bank.example.test"]).observe(tab)
    assert observation.server["origin"] == "https://bank.example.test"


async def test_a_new_document_on_the_same_origin_is_also_a_change(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())

    def reload(target_id: str) -> None:
        fake_browser.targets[target_id]["loader"] = "RELOADED-" + str(len(fake.received))

    fake_browser.after["Page.captureScreenshot"] = reload
    with pytest.raises(ToolError) as caught:
        await perceiver(registry).observe(tab)
    assert caught.value.code == "page_changed_during_capture"


# --- dialogs --------------------------------------------------------------------------------


async def test_an_open_dialog_is_reported_without_reading_the_page(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    await fake.emit("Page.javascriptDialogOpening", {"url": URL, "message": "Leave the page?", "type": "confirm"}, session_id=tab.session_id)
    await settle()
    sent = len(fake.received)
    observation = await perceiver(registry).observe(tab)
    assert len(fake.received) == sent
    assert observation.server["dialog_open"] is True and observation.snapshot is None
    assert observation.page_content == {"dialog": {"type": "confirm", "message": "Leave the page?"}}
    outside = await perceiver(registry, ["https://other.example.test"]).observe(tab)
    assert outside.server["dialog_open"] is True and outside.page_content is None
    assert "Leave the page" not in serialized(outside)


async def test_a_dialog_opening_during_a_read_ends_the_observation_at_once(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())

    async def blocked(params: dict[str, Any], session_id: str | None) -> Any:
        await fake.emit("Page.javascriptDialogOpening", {"url": URL, "message": "Blocked", "type": "alert"}, session_id=session_id)
        return NO_REPLY

    fake.on("DOMSnapshot.captureSnapshot", handler=blocked)
    observation = await asyncio.wait_for(perceiver(registry).observe(tab), 3)
    assert observation.server["dialog_open"] is True
    assert observation.page_content == {"dialog": {"type": "alert", "message": "Blocked"}}


# --- observation output and snapshots -----------------------------------------------------------


async def test_observation_output_separates_server_facts_from_page_content(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    observation = await perceiver(registry).observe(tab)
    server, page = observation.server, observation.page_content
    assert set(server) == {
        "tab_id", "snapshot_id", "origin", "loader_id", "viewport", "capture", "counters", "dialog_open", "notices", "image"
    }
    assert server["origin"] == ORIGIN and server["tab_id"] == tab.tab_id and len(server["snapshot_id"]) == 32
    assert page is not None and set(page) == {"url", "title", "tree", "elements"}
    assert page["title"] == "Login"
    envelope = finalize({"ok": True, "server": server, "page_content": page})
    assert envelope["page_content"]["url"] == "https://example.test/account/page?token=<redacted>"
    assert FAKE_PASSWORD not in serialized(observation) and CARD_NUMBER not in serialized(observation)
    tool_output = observation.to_tool_output()
    assert tool_output.image_png == observation.image_png


async def test_only_the_last_three_snapshots_are_kept(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    percept = perceiver(registry)
    ids = [(await percept.observe(tab, image=False)).server["snapshot_id"] for _ in range(4)]
    with pytest.raises(ToolError) as caught:
        percept.snapshot(tab.tab_id, ids[0])
    assert caught.value.code == "snapshot_stale"
    for snapshot_id in ids[1:]:
        snapshot = percept.snapshot(tab.tab_id, snapshot_id)
        assert snapshot.tab_id == tab.tab_id and snapshot.origin == parse_origin(ORIGIN)
        assert snapshot.loader_id and snapshot.viewport == VIEWPORT
        assert snapshot.element(1) is not None and snapshot.element(99) is None
    for tab_id, snapshot_id in ((tab.tab_id, "unknown"), ("other-tab", ids[-1])):
        with pytest.raises(ToolError) as caught:
            percept.snapshot(tab_id, snapshot_id)
        assert caught.value.code == "snapshot_stale"


async def test_an_observation_without_image_takes_no_screenshot(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    observation = await perceiver(registry).observe(tab, image=False)
    assert observation.server["capture"] == "not_requested" and observation.image_png is None
    assert "Page.captureScreenshot" not in fake.methods()
    screenshot_requests = [m for m in fake.received if m.get("method") == "Page.captureScreenshot"]
    assert screenshot_requests == []


async def test_the_screenshot_is_the_viewport_at_scale_one(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    page = login_page(viewport=(0, 300, 800, 600), content_size=(800, 2000))
    tab = await showing(fake_browser, registry, page)
    await perceiver(registry).observe(tab)
    [request] = [m for m in fake.received if m.get("method") == "Page.captureScreenshot"]
    assert request["params"] == {
        "format": "png",
        "clip": {"x": 0.0, "y": 300.0, "width": 800.0, "height": 600.0, "scale": 1},
        "captureBeyondViewport": False,
    }


# --- waiting ------------------------------------------------------------------------------------


async def test_wait_for_text_polls_light_readings_until_the_text_shows(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    before = Page("Wait", [button("Start", (10, 10, 80, 20)), El("P", role="paragraph", box=(10, 40, 300, 20), opacity="0", children=[text("Ready now", (10, 40, 100, 16))])])
    after = Page("Wait", [button("Start", (10, 10, 80, 20)), El("P", role="paragraph", box=(10, 40, 300, 20), children=[text("Ready now", (10, 40, 100, 16))])])
    tab = await showing(fake_browser, registry, before)
    reads = 0

    def reveal(target_id: str) -> None:
        nonlocal reads
        reads += 1
        if reads == 3:
            fake_browser.targets[target_id]["page"] = after

    fake_browser.after["Accessibility.getFullAXTree"] = reveal
    started = asyncio.get_running_loop().time()
    result = await perceiver(registry).wait_for(tab, kind="text", value="Ready", timeout_s=5)
    elapsed = asyncio.get_running_loop().time() - started
    assert result.met and 0.55 < elapsed < 2.5
    assert "Page.captureScreenshot" not in fake.methods()
    snapshots = [m["params"] for m in fake.received if m.get("method") == "DOMSnapshot.captureSnapshot"]
    assert snapshots and all("includePaintOrder" not in params for params in snapshots)


async def test_wait_for_element_needs_the_exact_role_and_name(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, Page("Wait", [button("Inserted button", (10, 10, 120, 20))]))
    percept = perceiver(registry)
    assert (await percept.wait_for(tab, kind="element", value={"role": "button", "name": "Inserted button"})).met
    with pytest.raises(ToolError) as caught:
        await percept.wait_for(tab, kind="element", value={"role": "link", "name": "Inserted button"}, timeout_s=0.4)
    assert caught.value.code == "wait_timeout"


async def test_wait_for_hidden_text_times_out(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    page = Page("Hidden", [button("Visible", (10, 10, 80, 20)), button("Secret instructions", (10, 40, 120, 20), opacity="0")])
    tab = await showing(fake_browser, registry, page)
    with pytest.raises(ToolError) as caught:
        await perceiver(registry).wait_for(tab, kind="text", value="Secret instructions", timeout_s=0.4)
    assert caught.value.code == "wait_timeout"


async def test_wait_for_obeys_the_allowed_sites(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    with pytest.raises(ToolError) as caught:
        await perceiver(registry, ["https://other.example.test"]).wait_for(tab, kind="text", value="Login", timeout_s=1)
    assert caught.value.code == "origin_not_allowed"
    assert content_reads(fake) == []


async def test_wait_for_load(fake: FakeCdp, fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    percept = perceiver(registry)
    assert (await percept.wait_for(tab, kind="load")).met
    await fake.emit("Page.frameStartedLoading", {"frameId": tab.main_frame_id}, session_id=tab.session_id)
    await settle()
    assert tab.loading
    with pytest.raises(ToolError) as caught:
        await percept.wait_for(tab, kind="load", timeout_s=0.2)
    assert caught.value.code == "wait_timeout"
    waiting = asyncio.create_task(percept.wait_for(tab, kind="load", timeout_s=5))
    await settle()
    await fake.emit("Page.frameStoppedLoading", {"frameId": tab.main_frame_id}, session_id=tab.session_id)
    assert (await asyncio.wait_for(waiting, 2)).met


@pytest.mark.parametrize(("kind", "value"), [("text", ""), ("text", None), ("element", {"role": "button"}), ("network", None)])
async def test_wait_for_refuses_malformed_requests(fake_browser: FakeBrowser, registry: TabRegistry, kind: str, value: Any) -> None:
    tab = await showing(fake_browser, registry, login_page())
    with pytest.raises(ValueError):
        await perceiver(registry).wait_for(tab, kind=kind, value=value)


def test_wait_timeout_is_ten_by_default_and_sixty_at_most() -> None:
    assert perception.DEFAULT_WAIT_S == 10.0
    assert perception._wait_timeout(600) == 60.0
    with pytest.raises(ValueError):
        perception._wait_timeout(-1)


# --- what redacted_fields counts --------------------------------------------------------------


def three_sensitive_two_filled() -> Page:
    return Page(
        "Two filled",
        [
            field_input("Password", (10, 10, 200, 20), attrs={"type": "password"}, value=FAKE_PASSWORD),
            field_input("Card", (10, 40, 200, 20), attrs={"autocomplete": "cc-number"}, value=CARD_NUMBER),
            field_input("One-time code", (10, 70, 200, 20), attrs={"name": "otp"}),
            field_input("Comment", (10, 100, 200, 20), value="plain"),
        ],
    )


def redacted_count_is_two() -> bool:
    result = built(three_sensitive_two_filled())
    return result.counters["redacted_fields"] == 2 and [e.sensitive for e in result.elements] == [True, True, True, False]


def test_redacted_fields_counts_the_filled_sensitive_fields() -> None:
    assert redacted_count_is_two()


def test_redacted_count_check_catches_a_count_of_every_sensitive_field(monkeypatch: pytest.MonkeyPatch) -> None:
    original = perception._element

    def counts_every_sensitive_field(mark: int, item: Any, dom: Dom, fields: Any, origin: Any, counters: dict[str, Any]) -> Any:
        before = counters["redacted_fields"]
        ref = original(mark, item, dom, fields, origin, counters)
        if ref.sensitive and counters["redacted_fields"] == before:
            counters["redacted_fields"] += 1
        return ref

    monkeypatch.setattr(perception, "_element", counts_every_sensitive_field)
    assert not redacted_count_is_two()


# --- tree nodes without a DOM identifier, and frames of tree nodes ------------------------------

PERCEPTION_SOURCE = Path(perception.__file__).read_text()


def reads_a_frame_of_a_tree_node(source: str) -> bool:
    # Measured: only the root of the accessibility tree carries frameId.
    return "frameId" in source


def test_the_perception_never_reads_the_frame_of_a_tree_node() -> None:
    assert not reads_a_frame_of_a_tree_node(PERCEPTION_SOURCE)


def test_frame_check_catches_a_reading_of_frame_id() -> None:
    assert reads_a_frame_of_a_tree_node(PERCEPTION_SOURCE + "\nFRAME = lambda node: node.get('frameId')\n")


def page_with_nodes_lacking_dom_identifiers() -> dict[str, Any]:
    page = Page("Identifiers", [button("Kept", (10, 10, 100, 20)), El("P", role="paragraph", box=(10, 40, 300, 20), children=[text("Text run", (10, 40, 100, 16))])])
    ax = page.ax_tree()
    for node in ax["nodes"]:
        if node.get("role", {}).get("value") in ("StaticText", "paragraph"):
            node.pop("backendDOMNodeId", None)
    ax["nodes"].append({"nodeId": "-99", "ignored": False, "role": {"type": "role", "value": "generic"}, "childIds": [], "parentId": "6"})
    next(node for node in ax["nodes"] if node["nodeId"] == "6")["childIds"].append("-99")
    return {"page": page, "ax": ax}


Builder = Callable[..., perception.Built]


def nodes_without_identifier_are_handled(builder: Builder) -> bool:
    case = page_with_nodes_lacking_dom_identifiers()
    try:
        result = builder(case["ax"], Dom(case["page"].dom_snapshot()), VIEWPORT, parse_origin(ORIGIN))
    except Exception:  # noqa: BLE001 - any exception is the failure looked for
        return False
    return names(result) == ["Kept"]


def builder_that_trusts_every_node(ax: dict[str, Any], dom: Dom, viewport: Box, origin: Any) -> perception.Built:
    # Defective: assumes every tree node names its DOM node.
    for node in ax["nodes"]:
        dom.by_backend[node["backendDOMNodeId"]]
    return build(ax, dom, viewport, origin)


def test_tree_nodes_without_a_dom_identifier_are_skipped_without_error() -> None:
    assert nodes_without_identifier_are_handled(build)


def test_identifier_check_catches_a_builder_that_trusts_every_node() -> None:
    assert not nodes_without_identifier_are_handled(builder_that_trusts_every_node)


# --- texts around an element, for the gate -----------------------------------------------------------


def test_the_gate_texts_of_an_element() -> None:
    page = Page(
        "Checkout",
        [
            El("H2", role="heading", name="Étape 3 : paiement", box=(10, 10, 300, 30), properties={"level": 2}, children=[text("Étape 3 : paiement", (10, 10, 200, 20))]),
            El(
                "FORM",
                role="form",
                name="Commande",
                attrs={"aria-label": "Commande"},
                box=(0, 50, 600, 300),
                children=[
                    El(
                        "FIELDSET",
                        role="group",
                        name="Coordonnées",
                        box=(0, 50, 600, 200),
                        children=[El("LEGEND", box=(5, 52, 200, 18), children=[text("Coordonnées bancaires", (5, 52, 200, 16))]), field_input("Titulaire", (10, 80, 200, 20))],
                    ),
                    El("INPUT", attrs={"type": "submit", "value": "Valider la commande"}, role="button", name="Continuer", box=(10, 260, 120, 24), clickable=True),
                    button("Aide", (140, 260, 60, 24), attrs={"title": "Supprimer le panier"}),
                ],
            ),
        ],
    )
    holder, submit, helper = built(page).elements
    assert holder.form_name == "Commande Coordonnées bancaires"
    assert submit.gate_texts == ("Continuer", "Valider la commande", "", "Commande", "Étape 3 : paiement")
    assert helper.title == "Supprimer le panier" and helper.heading == "Étape 3 : paiement"
    assert "button_value" not in helper.public and "title" not in helper.public


async def test_the_latest_snapshot_of_a_tab(fake_browser: FakeBrowser, registry: TabRegistry) -> None:
    tab = await showing(fake_browser, registry, login_page())
    percept = perceiver(registry)
    assert percept.latest_snapshot(tab.tab_id) is None
    first = await percept.observe(tab, image=False)
    second = await percept.observe(tab, image=False)
    latest = percept.latest_snapshot(tab.tab_id)
    assert latest is not None and latest.snapshot_id == second.server["snapshot_id"] != first.server["snapshot_id"]


def test_identities_of_simulated_nodes_survive_an_insertion() -> None:
    first, second = button("First", (10, 10, 80, 20)), button("Second", (10, 40, 80, 20))
    page = Page("Ids", [first, second])
    page.prepare()
    before = second.backend_id
    page.body.insert(1, button("Inserted", (10, 40, 80, 20)))
    page.prepare()
    assert second.backend_id == before and page.body[1].backend_id not in (first.backend_id, before)
