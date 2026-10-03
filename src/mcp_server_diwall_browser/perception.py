"""What the model sees of a page: an image with marks, a text tree, a list of elements.

Why this module exists:
    The model must see what the human sees, and nothing the human cannot
    see: text hidden from the eye is a channel for instructions the operator
    never reads. Values of sensitive fields must be gone before anything
    leaves this module, and the image carries masks over them, or there is
    no image. Everything comes from one snapshot, read only after checking
    that the page belongs to a site the operator allowed, and the origin is
    read again afterwards so that a page that moved meanwhile is not shown.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import functools
import hashlib
import io
import math
import os
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from PIL import Image, ImageDraw, ImageFile, ImageFont

from .browser import DialogBlocking, FrameInfo, Tab, TabRegistry, origin_label
from .origin import Origin, OriginError, origin_allowed, origin_of_url
from .output import ToolError, ToolOutput, bounded_text, redact_text
from .sensitive_field import ATTRIBUTE_ARGUMENTS, is_sensitive

# Roles that make a visible node an element the model can designate.
MARKABLE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "checkbox",
        "radio",
        "switch",
        "slider",
        "spinbutton",
        "tab",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "treeitem",
    }
)
STATES = ("checked", "disabled", "expanded", "focused", "required")
MAX_MARKS = 150
MAX_TREE_LINES = 400
NAME_LIMIT = 200
VALUE_LIMIT = 120
MIN_OPACITY = 0.01
MIN_BOX_SIZE = 2.0
MAX_IMAGE_WIDTH = 1600
SNAPSHOTS_KEPT = 3
POLL_INTERVAL_S = 0.3
DEFAULT_WAIT_S = 10.0
MAX_WAIT_S = 60.0
COMPUTED_STYLES = ("display", "visibility", "opacity", "font-size")
SNAPSHOT_PARAMS = {"computedStyles": list(COMPUTED_STYLES), "includeDOMRects": True, "includePaintOrder": True}
LIGHT_SNAPSHOT_PARAMS = {"computedStyles": list(COMPUTED_STYLES)}

FIELD_TAGS = frozenset({"input", "textarea", "select"})
FRAME_TAGS = frozenset({"iframe", "frame"})
# Elements that are never rendered, whatever the page: not counted as hidden.
NEVER_RENDERED_TAGS = frozenset({"head", "script", "style", "meta", "link", "title", "template", "noscript", "base"})
# Reasons for which the browser hides a node from assistive technology
# because it is hidden (not because it is merely uninteresting).
HIDING_REASONS = frozenset({"ariaHiddenElement", "inertElement", "notRendered", "notVisible"})
# Roles that carry no text of their own: without a name they produce no line.
STRUCTURAL_ROLES = frozenset({"generic", "none", "LabelText", "LineBreak", "paragraph"})
TEXT_ROLE = "StaticText"
NO_DOM_ROLES = frozenset({"InlineTextBox"})

MASK_COLOUR = (40, 40, 40)
MASK_TEXT_COLOUR = (255, 255, 255)
MARK_COLOUR = (220, 0, 120, 255)
BADGE_TEXT_COLOUR = (255, 255, 255, 255)
FRAME_NOTICE = "cadre non montré au modèle"
# Written when no font with accented letters is installed.
FRAME_NOTICE_PLAIN = "cadre non montre au modele"
NOTICE_FONTS = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf")

_ORIGIN_MESSAGE = "cette page n'est pas sur un site autorisé par le profil de l'opérateur : le serveur ne la lit pas"
_CHANGED_MESSAGE = "la page a changé d'adresse pendant la lecture : rien n'en est publié ; observer à nouveau"
_STALE_MESSAGE = "cet instantané n'existe plus ou n'est pas le dernier : observer à nouveau la page"
_CAPTURE_MESSAGE = "l'image n'est pas publiée : un masque n'a pas pu être posé"
_WAIT_MESSAGE = "la condition attendue n'est pas apparue dans le délai"


# --- Data handed to the rest of the server ------------------------------------------------


@dataclass(frozen=True)
class Box:
    """A rectangle in CSS pixels of the document."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def finite(self) -> bool:
        return all(math.isfinite(v) for v in (self.x, self.y, self.width, self.height))

    def intersects(self, other: Box) -> bool:
        return self.x < other.right and other.x < self.right and self.y < other.bottom and other.y < self.bottom


@dataclass(frozen=True)
class ElementRef:
    """A marked element as the security gate reads it; never sent to the model as such."""

    mark: int
    backend_node_id: int
    role: str
    name: str
    name_hash: str
    tag: str
    input_type: str | None
    sensitive: bool
    bounds: Box
    in_viewport: bool
    form_backend_id: int | None
    form_has_card_field: bool
    is_submit_like: bool
    origin: Origin
    # What the model receives for this mark (cleaned, value withheld if sensitive).
    public: dict[str, Any] = field(repr=False, compare=False)
    # Page texts around the element, read by the gate only (never sent as such).
    button_value: str = field(default="", repr=False)
    title: str = field(default="", repr=False)
    form_name: str = field(default="", repr=False)
    heading: str = field(default="", repr=False)

    @property
    def gate_texts(self) -> tuple[str, ...]:
        """The texts that may say what an action does: name, button value, title, form, section."""
        return (self.name, self.button_value, self.title, self.form_name, self.heading)


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    tab_id: str
    loader_id: str
    origin: Origin
    viewport: Box
    elements: tuple[ElementRef, ...]
    tree_lines: tuple[str, ...]
    counters: dict[str, Any]
    page_has_card_field: bool
    image_png: bytes | None = field(repr=False)

    def element(self, mark: int) -> ElementRef | None:
        for element in self.elements:
            if element.mark == mark:
                return element
        return None


@dataclass
class Observation:
    """One observation: what the server asserts, the page content, the image."""

    server: dict[str, Any]
    page_content: dict[str, Any] | None
    image_png: bytes | None
    snapshot: Snapshot | None

    def to_tool_output(self) -> ToolOutput:
        return ToolOutput(server=self.server, page_content=self.page_content, image_png=self.image_png)


@dataclass(frozen=True)
class WaitResult:
    met: bool
    dialog_open: bool = False


# --- Reading the snapshot -----------------------------------------------------------------


@dataclass
class DomNode:
    index: int
    backend_id: int
    parent: int
    node_type: int
    tag: str
    text: str
    attributes: dict[str, str]
    input_value: str | None
    text_value: str | None
    clickable: bool
    box: Box | None
    styles: dict[str, str]


class SnapshotFormatError(ValueError):
    """The browser's answer does not have the shape this module reads."""


class Dom:
    """The main document of a DOMSnapshot.captureSnapshot answer; other documents are dropped."""

    def __init__(self, raw: dict[str, Any]) -> None:
        try:
            strings = raw["strings"]
            document = raw["documents"][0]
            nodes = document["nodes"]
            layout = document["layout"]
            self.content_width = float(document.get("contentWidth", 0))
            self.content_height = float(document.get("contentHeight", 0))
            self.nodes = self._read_nodes(strings, nodes, layout)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SnapshotFormatError(type(exc).__name__) from None
        self.by_backend = {node.backend_id: node for node in self.nodes}
        self._opacity: dict[int, float] = {}
        self._children: dict[int, list[int]] | None = None

    @staticmethod
    def _read_nodes(strings: list[str], nodes: dict[str, Any], layout: dict[str, Any]) -> list[DomNode]:
        def text(index: int) -> str:
            return strings[index] if isinstance(index, int) and index >= 0 else ""

        def rare(name: str) -> dict[int, Any]:
            data = nodes.get(name)
            if not data:
                return {}
            values = data.get("value")
            return dict(zip(data["index"], values if values is not None else [True] * len(data["index"]), strict=True))

        count = len(nodes["backendNodeId"])
        input_values, text_values, clickable = rare("inputValue"), rare("textValue"), rare("isClickable")
        boxes: dict[int, Box] = {}
        styles: dict[int, dict[str, str]] = {}
        for position, node_index in enumerate(layout["nodeIndex"]):
            x, y, width, height = (float(v) for v in layout["bounds"][position])
            boxes[node_index] = Box(x, y, width, height)
            values = layout["styles"][position]
            styles[node_index] = {name: text(v) for name, v in zip(COMPUTED_STYLES, values, strict=False)}
        result = []
        for i in range(count):
            pairs = nodes["attributes"][i]
            attributes = {text(pairs[k]).lower(): text(pairs[k + 1]) for k in range(0, len(pairs) - 1, 2)}
            input_value = input_values.get(i)
            text_value = text_values.get(i)
            result.append(
                DomNode(
                    index=i,
                    backend_id=int(nodes["backendNodeId"][i]),
                    parent=int(nodes["parentIndex"][i]),
                    node_type=int(nodes["nodeType"][i]),
                    tag=text(nodes["nodeName"][i]).lower(),
                    text=text(nodes["nodeValue"][i]) if "nodeValue" in nodes else "",
                    attributes=attributes,
                    input_value=text(input_value) if input_value is not None else None,
                    text_value=text(text_value) if text_value is not None else None,
                    clickable=bool(clickable.get(i, False)),
                    box=boxes.get(i),
                    styles=styles.get(i, {}),
                )
            )
        return result

    def parent(self, node: DomNode) -> DomNode | None:
        return self.nodes[node.parent] if 0 <= node.parent < len(self.nodes) else None

    def ancestors(self, node: DomNode) -> Iterable[DomNode]:
        current = self.parent(node)
        seen = 0
        while current is not None and seen <= len(self.nodes):
            yield current
            current = self.parent(current)
            seen += 1

    def effective_opacity(self, node: DomNode) -> float:
        """Product of the computed opacities of the node and its ancestors.

        Opacity is not inherited: a child of a transparent parent computes
        to 1 while it shows nothing. A text node carries its parent's style,
        so only elements are counted.
        """
        cached = self._opacity.get(node.index)
        if cached is not None:
            return cached
        own = 1.0
        if node.node_type == 1 and node.styles:
            try:
                own = float(node.styles.get("opacity", "1") or "1")
            except ValueError:
                own = 0.0
            if not math.isfinite(own):
                own = 0.0
        parent = self.parent(node)
        value = own * (self.effective_opacity(parent) if parent is not None else 1.0)
        self._opacity[node.index] = value
        return value

    def visible(self, node: DomNode) -> bool:
        """Conditions 1 to 5: a box, not hidden, not transparent, not tiny, on the page."""
        box = node.box
        if box is None or not box.finite():
            return False
        if node.styles.get("visibility") in ("hidden", "collapse"):
            return False
        # Rounded: 0.1 x 0.1 is 0.01, not 0.010000000000000002.
        if round(self.effective_opacity(node), 9) <= MIN_OPACITY:
            return False
        if box.width < MIN_BOX_SIZE or box.height < MIN_BOX_SIZE:
            return False
        if box.right <= 0 or box.bottom <= 0:
            return False
        return not (box.x >= self.content_width or box.y >= self.content_height)

    def enclosing(self, node: DomNode, tag: str) -> DomNode | None:
        for ancestor in self.ancestors(node):
            if ancestor.tag == tag and ancestor.node_type == 1:
                return ancestor
        return None

    def children(self, node: DomNode) -> list[DomNode]:
        if self._children is None:
            self._children = {}
            for item in self.nodes:
                self._children.setdefault(item.parent, []).append(item.index)
        return [self.nodes[index] for index in self._children.get(node.index, [])]

    def text_content(self, node: DomNode, limit: int) -> str:
        """The text of the nodes under ``node``, in document order, bounded."""
        pieces: list[str] = []
        stack = [node]
        size = 0
        while stack and size <= limit:
            current = stack.pop()
            if current.node_type == 3:
                pieces.append(current.text)
                size += len(current.text)
            elif current.tag not in NEVER_RENDERED_TAGS:
                stack.extend(reversed(self.children(current)))
        return bounded_text(" ".join(pieces), limit)

    def inside_field(self, node: DomNode) -> bool:
        return any(ancestor.tag in FIELD_TAGS for ancestor in self.ancestors(node))


def _ax_nodes(raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    try:
        nodes = {str(node["nodeId"]): node for node in raw["nodes"]}
        root = next(str(node["nodeId"]) for node in raw["nodes"] if not node.get("parentId"))
    except (KeyError, TypeError, StopIteration) as exc:
        raise SnapshotFormatError(type(exc).__name__) from None
    return nodes, root


def _ax_value(node: dict[str, Any], key: str) -> Any:
    item = node.get(key)
    return item.get("value") if isinstance(item, dict) else None


def _ax_property(node: dict[str, Any], name: str) -> Any:
    for item in node.get("properties", ()):
        if isinstance(item, dict) and item.get("name") == name:
            return _ax_value(item, "value")
    return None


# --- Names ----------------------------------------------------------------------------------


def normalized_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def name_hash(name: str) -> str:
    return hashlib.sha256(normalized_name(name).encode("utf-8")).hexdigest()


_QUOTE_ESCAPES = str.maketrans({"\\": "\\\\", '"': '\\"'})


def _quoted(name: str) -> str:
    return '"' + name.translate(_QUOTE_ESCAPES) + '"'


# --- Building a snapshot ----------------------------------------------------------------------


@dataclass
class _Shown:
    ax: dict[str, Any]
    dom: DomNode
    role: str
    name: str
    line: str | None
    in_viewport: bool


@dataclass
class _Field:
    sensitive: bool
    card: bool


@dataclass
class Built:
    """Everything derived from one snapshot, before any image work."""

    elements: list[ElementRef]
    tree_lines: list[str]
    counters: dict[str, Any]
    page_has_card_field: bool
    sensitive_boxes: list[Box]
    frame_boxes: list[Box]
    unmaskable: bool
    title: str
    notices: list[str]


@dataclass
class _Walk:
    shown: list[_Shown]
    depths: list[int]
    omitted: int
    root: dict[str, Any]
    ax: dict[str, dict[str, Any]]


def _walk(ax_raw: dict[str, Any], dom: Dom, viewport: Box) -> _Walk:
    """The nodes shown to the model, in tree order, and the count of hidden ones.

    A node is shown only if the accessibility tree does not ignore it and its
    box passes every visibility condition; a hidden node takes its whole
    subtree with it and counts once. Nothing under a field is visited: it is
    the field's value.
    """
    ax, root_id = _ax_nodes(ax_raw)
    shown: list[_Shown] = []
    depths: list[int] = []
    omitted_roots: set[int] = set()
    ax_backends: set[int] = set()
    omitted = 0

    def visit(node_id: str, depth: int, line_parent_name: str | None) -> None:
        nonlocal omitted
        node = ax.get(node_id)
        if node is None:
            return
        role = str(_ax_value(node, "role") or "")
        if role in NO_DOM_ROLES:
            return
        backend = node.get("backendDOMNodeId")
        dom_node = dom.by_backend.get(backend) if isinstance(backend, int) else None
        if isinstance(backend, int):
            ax_backends.add(backend)
        children = [str(child) for child in node.get("childIds", ())]
        if node.get("ignored"):
            reasons = {r.get("name") for r in node.get("ignoredReasons", ()) if isinstance(r, dict)}
            if reasons & HIDING_REASONS:
                omitted += 1
                if dom_node is not None:
                    omitted_roots.add(dom_node.index)
                return
            for child in children:
                visit(child, depth, line_parent_name)
            return
        name = bounded_text(_ax_value(node, "name"), NAME_LIMIT)
        if dom_node is None or dom_node.box is None:
            # No box of its own (display: contents, for one): its children
            # speak for themselves. If nothing of it shows at all, it was a
            # hidden node with something to say, and counts once.
            before = (len(shown), omitted)
            for child in children:
                visit(child, depth, line_parent_name)
            if (len(shown), omitted) == before and (name or role in MARKABLE_ROLES):
                omitted += 1
                if dom_node is not None:
                    omitted_roots.add(dom_node.index)
            return
        if not dom.visible(dom_node):
            omitted += 1
            omitted_roots.add(dom_node.index)
            return
        line = _line(role, name, node, line_parent_name)
        shown.append(
            _Shown(ax=node, dom=dom_node, role=role, name=name, line=line, in_viewport=dom_node.box.intersects(viewport))
        )
        depths.append(depth)
        if dom_node.tag in FIELD_TAGS:
            return
        for child in children:
            visit(child, depth + 1 if line is not None else depth, name if line is not None else line_parent_name)

    visit(root_id, 0, None)
    omitted += _hidden_without_ax_node(dom, ax_backends, omitted_roots)
    return _Walk(shown=shown, depths=depths, omitted=omitted, root=ax[root_id], ax=ax)


def field_verdicts(dom: Dom, ax: dict[str, dict[str, Any]] | None = None) -> dict[int, _Field]:
    """The sensitive-field predicate, asked once for every input, textarea and select."""
    roles: dict[int, str] = {}
    for node in (ax or {}).values():
        backend = node.get("backendDOMNodeId")
        if isinstance(backend, int):
            roles[backend] = str(_ax_value(node, "role") or "")
    return {
        node.index: _field_verdict(node, ax_role=roles.get(node.backend_id))
        for node in dom.nodes
        if node.tag in FIELD_TAGS and node.node_type == 1
    }


def mask_boxes(dom: Dom, viewport: Box, fields: dict[int, _Field]) -> tuple[list[Box], list[Box]]:
    """Boxes of the sensitive fields and of the frames that cross the viewport."""
    sensitive = [
        dom.nodes[index].box
        for index, verdict in fields.items()
        if verdict.sensitive
        and dom.nodes[index].box is not None
        and dom.nodes[index].box.finite()  # type: ignore[union-attr]
        and dom.nodes[index].box.intersects(viewport)  # type: ignore[union-attr]
    ]
    frames = [
        node.box
        for node in dom.nodes
        if node.tag in FRAME_TAGS
        and node.node_type == 1
        and node.box is not None
        and node.box.finite()
        and node.box.intersects(viewport)
    ]
    return sensitive, frames  # type: ignore[return-value]


def build(
    ax_raw: dict[str, Any], dom: Dom, viewport: Box, origin: Origin, *, tree_builder: Callable[..., list[str]] | None = None
) -> Built:
    """Merge the accessibility tree and the snapshot of the main document.

    ``counters.redacted_fields`` counts the visible sensitive fields whose
    value was not empty and was withheld; an empty sensitive field is marked
    sensitive but counts nothing, there being nothing to withhold.
    ``counters.omitted_not_visible`` counts each hidden subtree once.
    """
    walk = _walk(ax_raw, dom, viewport)
    counters: dict[str, Any] = {
        "omitted_not_visible": walk.omitted,
        "redacted_fields": 0,
        "frames_masked": 0,
        "marks": 0,
        "marks_truncated": False,
        "tree_lines": 0,
        "tree_truncated": False,
    }
    fields = field_verdicts(dom, walk.ax)

    # Elements the model can designate, in document order.
    candidates = sorted(
        (item for item in walk.shown if item.role in MARKABLE_ROLES or (item.role == "generic" and item.dom.clickable)),
        key=lambda item: item.dom.index,
    )
    kept, counters["marks_truncated"] = _keep(candidates, MAX_MARKS, key=lambda item: item.dom.index)
    elements = [
        _with_gate_texts(_element(mark, item, dom, fields, origin, counters), item, dom, walk)
        for mark, item in enumerate(kept, start=1)
    ]
    counters["marks"] = len(elements)
    page_has_card_field = any(
        fields[item.dom.index].card for item in walk.shown if item.dom.index in fields
    )

    notices: list[str] = []
    try:
        tree_lines = (tree_builder or _tree)(walk.shown, walk.depths, counters)
    except Exception:  # noqa: BLE001 - a failed tree is withheld, never published half-cleaned
        tree_lines = []
        counters["tree_truncated"] = False
        notices.append("tree_unavailable : l'arbre de la page n'est pas publié, sa rédaction a échoué")
    counters["tree_lines"] = len(tree_lines)

    sensitive_boxes, frame_boxes = mask_boxes(dom, viewport, fields)
    counters["frames_masked"] = len(frame_boxes)
    return Built(
        elements=elements,
        tree_lines=tree_lines,
        counters=counters,
        page_has_card_field=page_has_card_field,
        sensitive_boxes=sensitive_boxes,
        frame_boxes=frame_boxes,
        unmaskable=_unmaskable(dom, fields, walk.ax),
        title=redact_text(bounded_text(_ax_value(walk.root, "name"), NAME_LIMIT)),
        notices=notices,
    )


def _line(role: str, name: str, node: dict[str, Any], parent_name: str | None) -> str | None:
    if not name and role in STRUCTURAL_ROLES:
        return None
    if role == TEXT_ROLE and (not name or name == parent_name):
        return None
    text = f"{bounded_text(role, 64)} {_quoted(name)}"
    if role == "heading":
        level = _ax_property(node, "level")
        if isinstance(level, (int, float)) and not isinstance(level, bool):
            text += f" level={int(level)}"
    return text


def _tree(shown: list[_Shown], depths: list[int], counters: dict[str, Any]) -> list[str]:
    lines = [(position, item) for position, item in enumerate(shown) if item.line is not None]
    kept, counters["tree_truncated"] = _keep(lines, MAX_TREE_LINES, key=lambda pair: pair[0])
    # Redaction of the registered secrets, before the lines leave the module.
    return [redact_text("  " * depths[position] + (item.line or "")) for position, item in kept]


def _keep(items: list[Any], limit: int, *, key: Callable[[Any], int]) -> tuple[list[Any], bool]:
    """At most ``limit`` items: those in the viewport first, then document order."""
    if len(items) <= limit:
        return items, False

    def in_viewport(item: Any) -> bool:
        return (item[1] if isinstance(item, tuple) else item).in_viewport

    first = [item for item in items if in_viewport(item)][:limit]
    rest = [item for item in items if not in_viewport(item)][: limit - len(first)]
    return sorted(first + rest, key=key), True


def _field_verdict(node: DomNode, *, ax_role: str | None) -> _Field:
    arguments = {keyword: node.attributes.get(attribute) for attribute, keyword in ATTRIBUTE_ARGUMENTS.items()}
    verdict = is_sensitive(tag=node.tag, role=ax_role, **arguments)
    return _Field(sensitive=verdict.sensitive, card=verdict.card)


def _element(
    mark: int, item: _Shown, dom: Dom, fields: dict[int, _Field], origin: Origin, counters: dict[str, Any]
) -> ElementRef:
    node = item.dom
    verdict = fields.get(node.index)
    sensitive = verdict is not None and verdict.sensitive
    input_type = node.attributes.get("type", "").lower() if node.tag == "input" else None
    raw_value = _raw_value(item, node, input_type)
    if sensitive:
        if raw_value:
            counters["redacted_fields"] += 1
        value = ""
    else:
        value = redact_text(bounded_text(raw_value, VALUE_LIMIT))
    form = dom.enclosing(node, "form")
    form_has_card = form is not None and any(
        fields[index].card for index in fields if dom.enclosing(dom.nodes[index], "form") is form
    )
    button_type = node.attributes.get("type", "").lower()
    submit_like = form is not None and (
        (node.tag == "button" and button_type in ("", "submit")) or (node.tag == "input" and button_type in ("submit", "image"))
    )
    in_viewport = item.in_viewport
    public: dict[str, Any] = {
        "mark": mark,
        "role": redact_text(bounded_text(item.role, 64)),
        "name": redact_text(item.name),
        "value": value,
    }
    states = [state for state in STATES if _ax_property(item.ax, state) in (True, "true")]
    if states:
        public["state"] = states
    public["in_viewport"] = in_viewport
    clickable_generic = item.role == "generic" and node.clickable
    if clickable_generic:
        public["clickable_generic"] = True
    assert node.box is not None
    return ElementRef(
        mark=mark,
        backend_node_id=node.backend_id,
        role=item.role,
        name=item.name,
        name_hash=name_hash(item.name),
        tag=node.tag,
        input_type=input_type,
        sensitive=sensitive,
        bounds=node.box,
        in_viewport=in_viewport,
        form_backend_id=form.backend_id if form is not None else None,
        form_has_card_field=form_has_card,
        is_submit_like=submit_like,
        origin=origin,
        public=public,
    )


def _with_gate_texts(element: ElementRef, item: _Shown, dom: Dom, walk: _Walk) -> ElementRef:
    """Add the texts around the element that the gate reads to decide whether to arm."""
    node = item.dom
    button_value = ""
    if node.tag == "button" or (node.tag == "input" and (element.input_type or "") in ("submit", "button", "image", "reset")):
        button_value = bounded_text(node.attributes.get("value", ""), NAME_LIMIT)
    form_name = ""
    form = dom.enclosing(node, "form")
    if form is not None:
        form_name = _ax_name_of(walk.ax, form.backend_id) or bounded_text(form.attributes.get("aria-label", ""), NAME_LIMIT)
    fieldset = dom.enclosing(node, "fieldset")
    if fieldset is not None:
        legend = next((child for child in dom.children(fieldset) if child.tag == "legend"), None)
        if legend is not None:
            form_name = bounded_text(f"{form_name} {dom.text_content(legend, NAME_LIMIT)}", NAME_LIMIT)
    heading = ""
    for shown in walk.shown:
        if shown.role == "heading" and shown.dom.index < node.index:
            heading = shown.name
    return replace(
        element,
        button_value=button_value,
        title=bounded_text(node.attributes.get("title", ""), NAME_LIMIT),
        form_name=form_name,
        heading=heading,
    )


def _ax_name_of(ax: dict[str, dict[str, Any]], backend_id: int) -> str:
    for node in ax.values():
        if node.get("backendDOMNodeId") == backend_id and not node.get("ignored"):
            return bounded_text(_ax_value(node, "name"), NAME_LIMIT)
    return ""


def _raw_value(item: _Shown, node: DomNode, input_type: str | None) -> str:
    if node.tag in FIELD_TAGS:
        if input_type in ("checkbox", "radio"):
            return ""
        if node.tag == "textarea":
            return node.text_value or node.input_value or ""
        if node.tag == "select":
            value = _ax_value(item.ax, "value")
            return value if isinstance(value, str) else ""
        return node.input_value or ""
    value = _ax_value(item.ax, "value")
    return value if isinstance(value, str) else ""


def _hidden_without_ax_node(dom: Dom, ax_backends: set[int], omitted_roots: set[int]) -> int:
    """Elements without a box (display: none) that the accessibility tree leaves out entirely.

    Each hidden subtree counts once, at its top element.
    """
    count = 0
    for node in dom.nodes:
        if node.node_type != 1 or node.box is not None or node.tag in NEVER_RENDERED_TAGS:
            continue
        if node.backend_id in ax_backends:
            continue
        parent = dom.parent(node)
        if parent is None or parent.box is None:
            continue
        if parent.tag in FIELD_TAGS or dom.inside_field(node):
            continue
        if node.index in omitted_roots or any(a.index in omitted_roots for a in dom.ancestors(node)):
            continue
        count += 1
    return count


def _unmaskable(dom: Dom, fields: dict[int, _Field], ax: dict[str, dict[str, Any]]) -> bool:
    """True when a sensitive field the tree shows has no usable box: it could not be masked."""
    shown_by_tree = {
        node.get("backendDOMNodeId") for node in ax.values() if not node.get("ignored") and isinstance(node.get("backendDOMNodeId"), int)
    }
    for index, verdict in fields.items():
        node = dom.nodes[index]
        if verdict.sensitive and (node.box is None or not node.box.finite()) and node.backend_id in shown_by_tree:
            return True
    return False


# --- Image ----------------------------------------------------------------------------------

MaskDrawer = Callable[[Image.Image, tuple[int, int, int, int], str], None]


def draw_mask(image: Image.Image, rect: tuple[int, int, int, int], kind: str) -> None:
    """A solid rectangle; over a frame, with the notice written inside it."""
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        return
    tile = Image.new("RGB", (x1 - x0, y1 - y0), MASK_COLOUR)
    if kind == "frame":
        pen = ImageDraw.Draw(tile)
        pen.fontmode = "1"  # two colours only: no blend with the fill
        font, text = _notice_font()
        pen.text((6, 6), text, fill=MASK_TEXT_COLOUR, font=font)
    image.paste(tile, (x0, y0))


@functools.cache
def _notice_font() -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
    # The font bundled with Pillow has no accented letters.
    for name in NOTICE_FONTS:
        try:
            return ImageFont.truetype(name, 14), FRAME_NOTICE
        except OSError:
            continue
    return ImageFont.load_default(size=14), FRAME_NOTICE_PLAIN


def _decode_png(png: bytes) -> Image.Image:
    # Decoded from memory: the image never touches a file.
    parser = ImageFile.Parser()
    parser.feed(png)
    image = parser.close()
    if image.format != "PNG":
        raise ValueError("the browser did not return a PNG image")
    return image.convert("RGB")


class CaptureFailed(Exception):
    """A mask could not be placed: the image must not leave the server."""


@dataclass(frozen=True)
class Rendered:
    png: bytes
    width: int
    height: int
    scale: float
    mask_rects: tuple[tuple[int, int, int, int], ...]
    mark_rects: dict[int, tuple[int, int, int, int]]


def _image_rect(box: Box, viewport: Box, sx: float, sy: float, size: tuple[int, int]) -> tuple[int, int, int, int]:
    # Rounded outwards, clipped to the image: a mask never leaves a pixel of its box uncovered.
    x0 = max(0, math.floor((box.x - viewport.x) * sx))
    y0 = max(0, math.floor((box.y - viewport.y) * sy))
    x1 = min(size[0], math.ceil((box.right - viewport.x) * sx))
    y1 = min(size[1], math.ceil((box.bottom - viewport.y) * sy))
    return x0, y0, max(x0, x1), max(y0, y1)


def render(
    png: bytes,
    viewport: Box,
    *,
    sensitive_boxes: Sequence[Box],
    frame_boxes: Sequence[Box],
    marks: Sequence[tuple[int, Box]],
    mask_drawer: MaskDrawer = draw_mask,
    max_width: int | None = MAX_IMAGE_WIDTH,
) -> Rendered:
    """Masks first, on the full image; then the reduction; then the marks, never over a mask."""
    try:
        image = _decode_png(png)
        sx = image.width / viewport.width if viewport.width > 0 else 1.0
        sy = image.height / viewport.height if viewport.height > 0 else 1.0
        masks = [(box, "field") for box in sensitive_boxes] + [(box, "frame") for box in frame_boxes]
        for box, kind in masks:
            mask_drawer(image, _image_rect(box, viewport, sx, sy, image.size), kind)
        scale = 1.0
        if max_width is not None and image.width > max_width:
            scale = max_width / image.width
            image = image.resize((max_width, max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
            sx, sy = sx * scale, sy * scale
            # The reduction blends the edges of each mask with its neighbours:
            # the masks are laid again at the reduced size.
            for box, kind in masks:
                mask_drawer(image, _image_rect(box, viewport, sx, sy, image.size), kind)
        mask_rects = tuple(_image_rect(box, viewport, sx, sy, image.size) for box, _kind in masks)
    except CaptureFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure while masking keeps the image in
        raise CaptureFailed(type(exc).__name__) from None
    mark_rects = _draw_marks(image, marks, viewport, sx, sy, mask_rects)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return Rendered(output.getvalue(), image.width, image.height, scale, mask_rects, mark_rects)


def _draw_marks(
    image: Image.Image,
    marks: Sequence[tuple[int, Box]],
    viewport: Box,
    sx: float,
    sy: float,
    mask_rects: Sequence[tuple[int, int, int, int]],
) -> dict[int, tuple[int, int, int, int]]:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=12)
    placed: dict[int, tuple[int, int, int, int]] = {}
    for mark, box in marks:
        x0, y0, x1, y1 = _image_rect(box, viewport, sx, sy, image.size)
        if x1 <= x0 or y1 <= y0:
            continue
        placed[mark] = (x0, y0, x1, y1)
        pen.rectangle((x0 - 2, y0 - 2, x1 + 1, y1 + 1), outline=MARK_COLOUR, width=2)
        label = str(mark)
        left, top, right, bottom = pen.textbbox((0, 0), label, font=font)
        width, height = right - left + 6, bottom - top + 4
        for bx, by in ((x0 - 2, y0 - height - 2), (x0 - width - 2, y0 - 2), (x0, y0), (x0 - 2, y1 + 2)):
            badge = (bx, by, bx + width, by + height)
            if bx < 0 or by < 0 or badge[2] > image.width or badge[3] > image.height:
                continue
            if any(_overlap(badge, rect) for rect in mask_rects):
                continue
            pen.rectangle(badge, fill=MARK_COLOUR)
            pen.text((bx + 3 - left, by + 2 - top), label, fill=BADGE_TEXT_COLOUR, font=font)
            break
    # Marks are drawn after the masks but never on them.
    for x0, y0, x1, y1 in mask_rects:
        if x1 > x0 and y1 > y0:
            overlay.paste((0, 0, 0, 0), (x0, y0, x1, y1))
    base = image.convert("RGBA")
    base.alpha_composite(overlay)
    image.paste(base.convert("RGB"))
    return placed


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# --- The module the tools use -------------------------------------------------------------------

AllowedOrigins = Callable[[], Iterable[Origin]]


class _PageChanged(Exception):
    pass


@dataclass
class _Reading:
    frame: FrameInfo
    origin: Origin
    viewport: Box
    ax: dict[str, Any]
    dom: Dom
    png: bytes | None
    # False when the boxes to mask moved between the snapshot and the image.
    masks_stable: bool = True


class Perception:
    """Observations of owned tabs, gated by the operator's allowed sites."""

    def __init__(
        self,
        registry: TabRegistry,
        *,
        allowed_origins: AllowedOrigins,
        mask_drawer: MaskDrawer = draw_mask,
        tree_builder: Callable[..., list[str]] | None = None,
    ) -> None:
        self._registry = registry
        self._allowed_origins = allowed_origins
        self._mask_drawer = mask_drawer
        self._tree_builder = tree_builder
        self._snapshots: dict[str, collections.deque[Snapshot]] = {}

    # --- public operations -----------------------------------------------------------

    async def observe(self, tab: Tab, *, image: bool = True) -> Observation:
        """Read the page once, check its origin before and after, and build what the model sees."""
        for _attempt in range(2):
            try:
                reading = await self._read(tab, image=image, full=True)
            except DialogBlocking:
                return self._dialog_observation(tab)
            except _PageChanged:
                continue
            return self._observation(tab, reading, image=image)
        raise ToolError("page_changed_during_capture", _CHANGED_MESSAGE)

    def latest_snapshot(self, tab_id: str) -> Snapshot | None:
        """The last snapshot of the tab, if any; it may describe a page that has moved since."""
        kept = self._snapshots.get(tab_id)
        return kept[-1] if kept else None

    def snapshot(self, tab_id: str, snapshot_id: str) -> Snapshot:
        """One of the last snapshots of the tab, or ``snapshot_stale``."""
        for snapshot in self._snapshots.get(tab_id, ()):
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        raise ToolError("snapshot_stale", _STALE_MESSAGE)

    async def capture_masked_png(self, tab: Tab) -> bytes:
        """The viewport with the same masks and no marks, for the evidence; nothing else makes one."""
        for _attempt in range(2):
            try:
                reading = await self._read(tab, image=True, full=True)
            except DialogBlocking:
                raise ToolError("capture_unavailable", _CAPTURE_MESSAGE) from None
            except _PageChanged:
                continue
            built = build(reading.ax, reading.dom, reading.viewport, reading.origin)
            if built.unmaskable or reading.png is None or not reading.masks_stable:
                raise ToolError("capture_unavailable", _CAPTURE_MESSAGE)
            try:
                rendered = render(
                    reading.png,
                    reading.viewport,
                    sensitive_boxes=built.sensitive_boxes,
                    frame_boxes=built.frame_boxes,
                    marks=(),
                    mask_drawer=self._mask_drawer,
                    max_width=None,
                )
            except CaptureFailed:
                raise ToolError("capture_unavailable", _CAPTURE_MESSAGE) from None
            return rendered.png
        raise ToolError("page_changed_during_capture", _CHANGED_MESSAGE)

    async def wait_for(
        self, tab: Tab, *, kind: str, value: Any = None, timeout_s: float = DEFAULT_WAIT_S
    ) -> WaitResult:
        """Wait for the load, a text or an element, by light reads every 300 ms.

        Never waits for a quiet network. ``wait_timeout`` at the deadline.
        """
        limit = _wait_timeout(timeout_s)
        if kind == "load":
            if await self._registry.wait_loaded(tab, timeout_s=limit):
                return WaitResult(met=True)
            raise ToolError("wait_timeout", _WAIT_MESSAGE)
        if kind == "text":
            if not isinstance(value, str) or not value:
                raise ValueError("a text wait needs a non-empty text")
            wanted = bounded_text(value, NAME_LIMIT)

            def met(names: list[tuple[str, str]]) -> bool:
                return any(wanted in name for _role, name in names)

        elif kind == "element":
            if not isinstance(value, dict) or not isinstance(value.get("role"), str) or not isinstance(value.get("name"), str):
                raise ValueError("an element wait needs {'role': ..., 'name': ...}")
            role, name = value["role"], bounded_text(value["name"], NAME_LIMIT)

            def met(names: list[tuple[str, str]]) -> bool:
                return (role, name) in names

        else:
            raise ValueError("kind is load, text or element")
        deadline = time.monotonic() + limit
        while True:
            try:
                reading = await self._read(tab, image=False, full=False)
            except DialogBlocking:
                return WaitResult(met=False, dialog_open=True)
            except _PageChanged:
                reading = None
            if reading is not None and met(_walk_names(reading.ax, reading.dom, reading.viewport)):
                return WaitResult(met=True)
            if time.monotonic() + POLL_INTERVAL_S > deadline:
                raise ToolError("wait_timeout", _WAIT_MESSAGE)
            await asyncio.sleep(POLL_INTERVAL_S)

    # --- reading ------------------------------------------------------------------

    async def _gate(self, tab: Tab) -> tuple[FrameInfo, Origin]:
        """The origin of the main frame, refused unless the operator allowed it."""
        frame = await self._registry.main_frame_info(tab, stop_on_dialog=True)
        if not origin_allowed(frame.origin, self._allowed_origins()):
            raise ToolError("origin_not_allowed", _ORIGIN_MESSAGE, extra_server={"origin": origin_label(frame)})
        return frame, frame.origin

    async def _read(self, tab: Tab, *, image: bool, full: bool) -> _Reading:
        frame, origin = await self._gate(tab)
        metrics = await self._registry.read(tab, "Page.getLayoutMetrics")
        viewport = _viewport(metrics)
        ax = await self._registry.read(tab, "Accessibility.getFullAXTree")
        raw = await self._registry.read(tab, "DOMSnapshot.captureSnapshot", SNAPSHOT_PARAMS if full else LIGHT_SNAPSHOT_PARAMS)
        png = None
        if image:
            shot = await self._registry.read(
                tab,
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "clip": {"x": viewport.x, "y": viewport.y, "width": viewport.width, "height": viewport.height, "scale": 1},
                    "captureBeyondViewport": False,
                },
            )
            try:
                png = base64.b64decode(shot.get("data", ""), validate=True)
            except (binascii.Error, ValueError, TypeError):
                png = None
        dom = Dom(raw)
        masks_stable = True
        if png is not None:
            # The image is taken after the snapshot: if a sensitive field or a
            # frame moved meanwhile, its mask would miss it. Read the boxes
            # again and keep the image only if they did not move.
            metrics_after = await self._registry.read(tab, "Page.getLayoutMetrics")
            dom_after = Dom(await self._registry.read(tab, "DOMSnapshot.captureSnapshot", LIGHT_SNAPSHOT_PARAMS))
            masks_stable = _viewport(metrics_after) == viewport and mask_boxes(
                dom, viewport, field_verdicts(dom)
            ) == mask_boxes(dom_after, viewport, field_verdicts(dom_after))
        after = await self._registry.main_frame_info(tab, stop_on_dialog=True)
        if after.origin != frame.origin or after.loader_id != frame.loader_id:
            raise _PageChanged()
        return _Reading(frame=frame, origin=origin, viewport=viewport, ax=ax, dom=dom, png=png, masks_stable=masks_stable)

    # --- building outputs --------------------------------------------------------

    def _observation(self, tab: Tab, reading: _Reading, *, image: bool) -> Observation:
        built = build(reading.ax, reading.dom, reading.viewport, reading.origin, tree_builder=self._tree_builder)
        notices = list(built.notices)
        published: bytes | None = None
        server_image: dict[str, Any] | None = None
        if not image:
            capture = "not_requested"
        elif reading.png is None or built.unmaskable or not reading.masks_stable:
            capture = "unavailable"
        else:
            marks = [(element.mark, element.bounds) for element in built.elements if element.in_viewport]
            try:
                rendered = render(
                    reading.png,
                    reading.viewport,
                    sensitive_boxes=built.sensitive_boxes,
                    frame_boxes=built.frame_boxes,
                    marks=marks,
                    mask_drawer=self._mask_drawer,
                )
            except CaptureFailed:
                capture = "unavailable"
            else:
                capture = "ok"
                published = rendered.png
                server_image = {"width": rendered.width, "height": rendered.height, "scale": rendered.scale}
        if capture == "unavailable":
            notices.append(f"capture_unavailable : {_CAPTURE_MESSAGE}")
        snapshot = Snapshot(
            snapshot_id=os.urandom(16).hex(),
            tab_id=tab.tab_id,
            loader_id=reading.frame.loader_id,
            origin=reading.origin,
            viewport=reading.viewport,
            elements=tuple(built.elements),
            tree_lines=tuple(built.tree_lines),
            counters=dict(built.counters),
            page_has_card_field=built.page_has_card_field,
            image_png=published,
        )
        self._snapshots.setdefault(tab.tab_id, collections.deque(maxlen=SNAPSHOTS_KEPT)).append(snapshot)
        server: dict[str, Any] = {
            "tab_id": tab.tab_id,
            "snapshot_id": snapshot.snapshot_id,
            "origin": reading.origin.canonical,
            # The document the marks belong to: a navigation gives a new one.
            "loader_id": snapshot.loader_id,
            # The part of the page in view, in CSS pixels; scale is image pixels per CSS pixel.
            "viewport": {
                "x": reading.viewport.x,
                "y": reading.viewport.y,
                "width": reading.viewport.width,
                "height": reading.viewport.height,
                "scale": server_image["scale"] if server_image is not None else 1.0,
            },
            "capture": capture,
            "counters": dict(built.counters),
            "dialog_open": False,
            "notices": notices,
        }
        if server_image is not None:
            server["image"] = server_image
        page_content = {
            "url": reading.frame.url,
            "title": built.title,
            "tree": list(built.tree_lines),
            "elements": [dict(element.public) for element in built.elements],
        }
        return Observation(server=server, page_content=page_content, image_png=published, snapshot=snapshot)

    def _dialog_observation(self, tab: Tab) -> Observation:
        dialog = tab.dialog or {}
        try:
            origin: Origin | None = origin_of_url(tab.dialog_url or "")
        except OriginError:
            origin = None
        allowed = origin_allowed(origin, self._allowed_origins())
        server: dict[str, Any] = {
            "tab_id": tab.tab_id,
            "capture": "unavailable",
            "dialog_open": True,
            "notices": ["dialog_open : une boîte de dialogue bloque la page ; la traiter avant d'observer"],
        }
        page_content = None
        if allowed:
            server["origin"] = origin.canonical  # type: ignore[union-attr]
            page_content = {
                "dialog": {
                    "type": bounded_text(dialog.get("type", ""), 32),
                    "message": redact_text(bounded_text(dialog.get("message", ""), NAME_LIMIT * 5)),
                }
            }
        return Observation(server=server, page_content=page_content, image_png=None, snapshot=None)


def _viewport(metrics: dict[str, Any]) -> Box:
    try:
        css = metrics["cssVisualViewport"]
        box = Box(float(css["pageX"]), float(css["pageY"]), float(css["clientWidth"]), float(css["clientHeight"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotFormatError(type(exc).__name__) from None
    if not box.finite() or box.width <= 0 or box.height <= 0:
        raise SnapshotFormatError("viewport")
    return box


def _walk_names(ax_raw: dict[str, Any], dom: Dom, viewport: Box) -> list[tuple[str, str]]:
    return [(item.role, item.name) for item in _walk(ax_raw, dom, viewport).shown]


def _wait_timeout(timeout_s: object) -> float:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s >= 0:
        raise ValueError("timeout_s is a non-negative number of seconds")
    return min(float(timeout_s), MAX_WAIT_S)
