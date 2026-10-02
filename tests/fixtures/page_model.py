"""Simulated pages: one description gives the accessibility tree, the DOM snapshot and the image.

Why this module exists:
    The perception is tested without a browser on pages built to isolate one
    condition at a time. Writing the browser's parallel arrays by hand for
    each of them would hide the intent in the bookkeeping; this builder keeps
    the shapes the browser was measured to return (see the browser tests),
    and the hand-written file in snapshots/ shows them in full.
"""

from __future__ import annotations

import base64
import io
import itertools
from dataclasses import dataclass, field, replace
from typing import Any

from PIL import Image, ImageDraw, ImageFont

BoxT = tuple[float, float, float, float]

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
FIELD_BORDER = (90, 90, 90)


@dataclass
class El:
    """An element or a text node of a simulated page."""

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[El] = field(default_factory=list)
    text: str = ""
    box: BoxT | None = (0, 0, 100, 20)
    display: str = "block"
    visibility: str = "visible"
    opacity: str = "1"
    role: str | None = None
    name: str = ""
    in_ax: bool = True
    ignored: bool = False
    ignored_reasons: tuple[str, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)
    ax_value: str | None = None
    input_value: str | None = None
    text_value: str | None = None
    clickable: bool = False
    paint: tuple[int, int, int] | None = None
    ink: str | None = None
    content: Page | None = None
    backend_id: int = 0


def text(value: str, box: BoxT | None = (0, 0, 80, 16), **kw: Any) -> El:
    return El("#text", text=value, box=box, role="StaticText", name=value, **kw)


def button(name: str, box: BoxT | None = (0, 0, 100, 20), *, attrs: dict[str, str] | None = None, **kw: Any) -> El:
    inner = None if box is None else (box[0] + 4, box[1] + 2, max(box[2] - 8, 1), max(box[3] - 4, 1))
    return El(
        "BUTTON",
        attrs={"type": "button", **(attrs or {})},
        role="button",
        name=name,
        box=box,
        clickable=True,
        paint=(230, 230, 230),
        children=[text(name, inner)],
        **kw,
    )


def link(name: str, box: BoxT, **kw: Any) -> El:
    return El("A", attrs={"href": "#x"}, role="link", name=name, box=box, clickable=True, children=[text(name, box)], **kw)


def field_input(
    name: str, box: BoxT | None, *, attrs: dict[str, str] | None = None, value: str = "", role: str = "textbox", **kw: Any
) -> El:
    return El(
        "INPUT",
        attrs=attrs or {"type": "text"},
        role=role,
        name=name,
        box=box,
        input_value=value,
        clickable=True,
        paint=WHITE,
        ink=value or None,
        **kw,
    )


def div(*children: El, box: BoxT | None = (0, 0, 400, 100), **kw: Any) -> El:
    kw.setdefault("role", None)
    return El("DIV", children=list(children), box=box, **kw)


def hidden(element: El) -> El:
    """display: none: no box and no node in the accessibility tree, down the subtree."""
    return replace(
        element,
        box=None,
        display="none",
        in_ax=False,
        children=[hidden(child) for child in element.children],
    )


@dataclass
class Page:
    title: str
    body: list[El]
    viewport: BoxT = (0, 0, 800, 600)
    content_size: tuple[float, float] = (800, 600)
    background: tuple[int, int, int] = WHITE
    # Highest identity ever given: a browser never gives a removed node's
    # identity to a new one.
    issued: int = field(default=9, repr=False, compare=False)

    # --- identities ------------------------------------------------------------

    def _number(self, counter: itertools.count[int]) -> None:
        # Like a browser, a node keeps its identity for its whole life: only
        # nodes that have none yet get one, so an insertion does not shift
        # the identity of the nodes after it.
        for element in self._elements():
            if element.backend_id == 0:
                element.backend_id = next(counter)
            if element.content is not None:
                element.content._number(counter)

    def _all_ids(self) -> list[int]:
        ids = []
        for element in self._elements():
            ids.append(element.backend_id)
            if element.content is not None:
                ids.extend(element.content._all_ids())
        return ids

    def _elements(self) -> list[El]:
        result: list[El] = []

        def walk(element: El) -> None:
            result.append(element)
            for child in element.children:
                walk(child)

        for element in self.body:
            walk(element)
        return result

    def prepare(self) -> Page:
        counter = itertools.count(max([self.issued, *self._all_ids()]) + 1)
        self._number(counter)
        self.issued = max([self.issued, *self._all_ids()])
        return self

    def find(self, backend_id: int) -> El | None:
        self.prepare()
        for element in self._elements():
            if element.backend_id == backend_id:
                return element
        return None

    def parent_of(self, backend_id: int) -> El | None:
        self.prepare()
        for element in self._elements():
            if any(child.backend_id == backend_id for child in element.children):
                return element
        return None

    # --- DOMSnapshot.captureSnapshot --------------------------------------------

    def dom_snapshot(self) -> dict[str, Any]:
        self.prepare()
        strings: list[str] = []
        index: dict[str, int] = {}

        def s(value: str) -> int:
            if value not in index:
                index[value] = len(strings)
                strings.append(value)
            return index[value]

        documents: list[dict[str, Any]] = []
        self._document(documents, s, document_backend=1)
        return {"documents": documents, "strings": strings}

    def _document(self, documents: list[dict[str, Any]], s: Any, document_backend: int) -> int:
        position = len(documents)
        nodes: dict[str, Any] = {
            "parentIndex": [],
            "nodeType": [],
            "shadowRootType": {"index": [], "value": []},
            "nodeName": [],
            "nodeValue": [],
            "backendNodeId": [],
            "attributes": [],
            "textValue": {"index": [], "value": []},
            "inputValue": {"index": [], "value": []},
            "inputChecked": {"index": []},
            "optionSelected": {"index": []},
            "contentDocumentIndex": {"index": [], "value": []},
            "pseudoType": {"index": [], "value": []},
            "pseudoIdentifier": {"index": [], "value": []},
            "isClickable": {"index": []},
            "currentSourceURL": {"index": [], "value": []},
            "originURL": {"index": [], "value": []},
        }
        layout: dict[str, Any] = {
            "nodeIndex": [],
            "styles": [],
            "bounds": [],
            "text": [],
            "stackingContexts": {"index": []},
            "paintOrders": [],
            "offsetRects": [],
            "scrollRects": [],
            "clientRects": [],
        }
        document: dict[str, Any] = {
            "documentURL": s("https://example.test/page"),
            "title": s(self.title),
            "baseURL": s("https://example.test/page"),
            "contentLanguage": -1,
            "encodingName": s("UTF-8"),
            "publicId": -1,
            "systemId": -1,
            "frameId": s("FRAME0"),
            "nodes": nodes,
            "layout": layout,
            "textBoxes": {"layoutIndex": [], "bounds": [], "start": [], "length": []},
            "scrollOffsetX": self.viewport[0],
            "scrollOffsetY": self.viewport[1],
            "contentWidth": self.content_size[0],
            "contentHeight": self.content_size[1],
        }
        documents.append(document)
        paint = itertools.count(1)
        styles_of: dict[int, list[int]] = {}

        def add(
            parent: int,
            node_type: int,
            name: str,
            backend: int,
            *,
            value: str = "",
            attrs: dict[str, str] | None = None,
            box: BoxT | None = None,
            style: tuple[str, str, str, str] | None = None,
        ) -> int:
            i = len(nodes["backendNodeId"])
            nodes["parentIndex"].append(parent)
            nodes["nodeType"].append(node_type)
            nodes["nodeName"].append(s(name))
            nodes["nodeValue"].append(s(value) if value else -1)
            nodes["backendNodeId"].append(backend)
            flat: list[int] = []
            for key, item in (attrs or {}).items():
                flat += [s(key), s(item)]
            nodes["attributes"].append(flat)
            if box is not None:
                layout["nodeIndex"].append(i)
                layout["bounds"].append(list(box))
                styles = [s(v) for v in style] if style is not None else []
                layout["styles"].append(styles)
                layout["text"].append(-1)
                layout["paintOrders"].append(next(paint))
                styles_of[i] = styles
            return i

        root = add(-1, 9, "#document", document_backend, box=(0, 0, self.viewport[2], self.viewport[3]))
        whole = (0, 0, self.content_size[0], self.content_size[1])
        html = add(root, 1, "HTML", document_backend + 1, box=whole, style=("block", "visible", "1", "16px"))
        head = add(html, 1, "HEAD", document_backend + 2)
        title = add(head, 1, "TITLE", document_backend + 3)
        add(title, 3, "#text", document_backend + 4, value=self.title)
        body = add(html, 1, "BODY", document_backend + 5, box=whole, style=("block", "visible", "1", "16px"))

        def element_node(element: El, parent: int, parent_style: tuple[str, str, str, str]) -> None:
            if element.tag == "#text":
                add(parent, 3, "#text", element.backend_id, value=element.text, box=element.box, style=parent_style if element.box else None)
                return
            style = (element.display, element.visibility, element.opacity, "16px")
            i = add(parent, 1, element.tag, element.backend_id, attrs=element.attrs, box=element.box, style=style if element.box else None)
            if element.input_value is not None:
                nodes["inputValue"]["index"].append(i)
                nodes["inputValue"]["value"].append(s(element.input_value))
            if element.text_value is not None:
                nodes["textValue"]["index"].append(i)
                nodes["textValue"]["value"].append(s(element.text_value))
            if element.clickable:
                nodes["isClickable"]["index"].append(i)
            if element.content is not None:
                inner = element.content._document(documents, s, document_backend=900000 + 1000 * len(documents))
                nodes["contentDocumentIndex"]["index"].append(i)
                nodes["contentDocumentIndex"]["value"].append(inner)
            for child in element.children:
                element_node(child, i, style)

        for element in self.body:
            element_node(element, body, ("block", "visible", "1", "16px"))
        return position

    # --- Accessibility.getFullAXTree ----------------------------------------------

    def ax_tree(self) -> dict[str, Any]:
        self.prepare()
        nodes: list[dict[str, Any]] = []
        inline = itertools.count(1)

        def ax_node(node_id: str, role: str, *, name: str | None, backend: int | None, parent: str | None, ignored: bool = False, reasons: tuple[str, ...] = (), properties: dict[str, Any] | None = None, value: str | None = None) -> dict[str, Any]:
            node: dict[str, Any] = {"nodeId": node_id, "ignored": ignored, "role": {"type": "role", "value": role}, "childIds": []}
            if ignored:
                node["ignoredReasons"] = [{"name": r, "value": {"type": "boolean", "value": True}} for r in reasons or ("uninteresting",)]
            else:
                node["name"] = {"type": "computedString", "value": name or ""}
                node["properties"] = [
                    {"name": key, "value": {"type": "booleanOrUndefined", "value": item}} for key, item in (properties or {}).items()
                ]
            if value is not None:
                node["value"] = {"type": "string", "value": value}
            if backend is not None:
                node["backendDOMNodeId"] = backend
            if parent is not None:
                node["parentId"] = parent
            nodes.append(node)
            return node

        root = ax_node("1", "RootWebArea", name=self.title, backend=1, parent=None)
        root["frameId"] = "FRAME0"
        html = ax_node("2", "none", name=None, backend=2, parent="1", ignored=True)
        body = ax_node("6", "none", name=None, backend=6, parent="2", ignored=True)
        root["childIds"].append("2")
        html["childIds"].append("6")

        def visit(element: El, parent: dict[str, Any]) -> None:
            if not element.in_ax:
                return
            if element.role is None and not element.ignored:
                for child in element.children:
                    visit(child, parent)
                return
            node = ax_node(
                str(element.backend_id),
                element.role or "none",
                name=element.name,
                backend=element.backend_id,
                parent=parent["nodeId"],
                ignored=element.ignored,
                reasons=element.ignored_reasons,
                properties=element.properties,
                value=element.ax_value,
            )
            parent["childIds"].append(node["nodeId"])
            if element.role == "StaticText" and not element.ignored:
                box_node = ax_node(f"-{next(inline)}", "InlineTextBox", name=element.name, backend=None, parent=node["nodeId"])
                node["childIds"].append(box_node["nodeId"])
            for child in element.children:
                visit(child, node)

        for element in self.body:
            visit(element, body)
        return {"nodes": nodes}

    # --- Page.getLayoutMetrics and Page.captureScreenshot ----------------------------

    def layout_metrics(self) -> dict[str, Any]:
        x, y, width, height = self.viewport
        viewport = {"offsetX": 0, "offsetY": 0, "pageX": x, "pageY": y, "clientWidth": width, "clientHeight": height, "scale": 1, "zoom": 1}
        size = {"x": 0, "y": 0, "width": self.content_size[0], "height": self.content_size[1]}
        return {"cssVisualViewport": viewport, "visualViewport": viewport, "cssContentSize": size, "contentSize": size}

    def screenshot(self, clip: dict[str, Any] | None = None) -> dict[str, Any]:
        x0, y0, width, height = self.viewport
        if clip is not None:
            x0, y0, width, height = clip["x"], clip["y"], clip["width"], clip["height"]
        image = Image.new("RGB", (int(width), int(height)), self.background)
        pen = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=12)

        def draw(element: El, dx: float, dy: float) -> None:
            if element.box is None or element.opacity == "0" or element.visibility == "hidden":
                return
            if not all(abs(v) < 1e9 for v in element.box):
                return
            bx, by, bw, bh = element.box
            rect = (bx - dx, by - dy, bx - dx + bw - 1, by - dy + bh - 1)
            if element.paint is not None:
                pen.rectangle(rect, fill=element.paint, outline=FIELD_BORDER if element.tag == "INPUT" else None)
            if element.ink:
                pen.text((rect[0] + 3, rect[1] + 2), element.ink, fill=BLACK, font=font)
            if element.tag == "#text" and element.text:
                pen.text((rect[0], rect[1]), element.text, fill=BLACK, font=font)
            if element.content is not None:
                inner = element.content
                pen.rectangle(rect, fill=inner.background)
                for child in inner._elements():
                    if child.box is not None and child.ink:
                        pen.text((rect[0] + child.box[0] + 3, rect[1] + child.box[1] + 2), child.ink, fill=BLACK, font=font)
            for child in element.children:
                draw(child, dx, dy)

        for element in self.body:
            draw(element, x0, y0)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return {"data": base64.b64encode(output.getvalue()).decode("ascii")}
