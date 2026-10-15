"""Ce que le modèle voit d'une page : une image avec des repères, un arbre de texte, une liste d'éléments.

Pourquoi ce module existe :
    Le modèle doit voir ce que l'humain voit, et rien de ce que l'humain ne peut
    voir : un texte caché à l'œil est un canal pour des instructions que l'opérateur
    ne lit jamais. Les valeurs des champs sensibles doivent avoir disparu avant que
    quoi que ce soit quitte ce module, et l'image porte des masques dessus, ou il n'y a
    pas d'image. Tout vient d'un seul instantané, lu seulement après avoir vérifié
    que la page appartient à un site que l'opérateur a autorisé, et l'origine est
    relue ensuite pour qu'une page qui a bougé entre-temps ne soit pas montrée.
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

from . import observation_filter
from .browser import DialogBlocking, FrameInfo, Tab, TabRegistry, origin_label
from .origin import Origin, OriginError, origin_allowed, origin_of_url
from .output import ToolError, ToolOutput, bounded_text, redact_text
from .sensitive_field import ATTRIBUTE_ARGUMENTS, is_sensitive

# Les rôles qui font d'un nœud visible un élément que le modèle peut désigner.
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
# De combien, dans chacune des deux directions, l'image que rend le navigateur peut s'écarter de la vue en pixels de l'appareil :
# le navigateur arrondit la découpe qu'il fait à des pixels entiers, et on l'a mesuré un pixel trop court dans une direction.
IMAGE_SIZE_SLACK_PX = 2
SNAPSHOTS_KEPT = 3
POLL_INTERVAL_S = 0.3
DEFAULT_WAIT_S = 10.0
MAX_WAIT_S = 60.0
# Pixels de l'appareil par pixel CSS : un facteur d'échelle d'écran, un zoom de page, ou les deux. Hors de
# cette plage, les deux fenêtres d'affichage que le navigateur rapporte ne décrivent pas la même page.
DEVICE_SCALE_RANGE = (0.1, 20.0)
COMPUTED_STYLES = ("display", "visibility", "opacity", "font-size", "position", "pointer-events")
SNAPSHOT_PARAMS = {"computedStyles": list(COMPUTED_STYLES), "includeDOMRects": True, "includePaintOrder": True}
LIGHT_SNAPSHOT_PARAMS = {"computedStyles": list(COMPUTED_STYLES)}

# Ce qui se tient au-dessus de la page : ces rôles, un nœud modal, ou un nœud sorti du flux qui peint
# par-dessus d'autre contenu.
LAYER_ROLES = frozenset({"dialog", "alertdialog"})
LAYER_POSITIONS = frozenset({"fixed", "sticky"})
# Nœuds positionnés examinés pour savoir s'ils sont une couche ; une page qui en a davantage est lue comme si le reste n'existait pas.
MAX_LAYER_CANDIDATES = 100
OVERLAY_LINE = 'overlay "layer above the page"'

FIELD_TAGS = frozenset({"input", "textarea", "select"})
FRAME_TAGS = frozenset({"iframe", "frame"})
# Éléments qui ne sont jamais rendus, quelle que soit la page : non comptés comme cachés.
NEVER_RENDERED_TAGS = frozenset({"head", "script", "style", "meta", "link", "title", "template", "noscript", "base"})
# Raisons pour lesquelles le navigateur cache un nœud aux technologies d'assistance
# parce qu'il est caché (et non simplement parce qu'il est sans intérêt).
HIDING_REASONS = frozenset({"ariaHiddenElement", "inertElement", "notRendered", "notVisible"})
# Rôles qui ne portent pas de texte propre : sans nom, ils ne produisent aucune ligne.
STRUCTURAL_ROLES = frozenset({"generic", "none", "LabelText", "LineBreak", "paragraph"})
TEXT_ROLE = "StaticText"
# Ce qu'une couche peut être dite recouvrir : des choses sur lesquelles agir ou à lire, pas les conteneurs autour d'elles.
CONTENT_ROLES = MARKABLE_ROLES | {TEXT_ROLE, "heading", "image", "img"}
NO_DOM_ROLES = frozenset({"InlineTextBox"})

MASK_COLOUR = (40, 40, 40)
MASK_TEXT_COLOUR = (255, 255, 255)
MARK_COLOUR = (220, 0, 120, 255)
BADGE_TEXT_COLOUR = (255, 255, 255, 255)
FRAME_NOTICE = "cadre non montré au modèle"
# Écrit quand aucune police avec des lettres accentuées n'est installée.
FRAME_NOTICE_PLAIN = "cadre non montre au modele"
NOTICE_FONTS = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf")

_ORIGIN_MESSAGE = "cette page n'est pas sur un site autorisé par le profil de l'opérateur : le serveur ne la lit pas"
_CHANGED_MESSAGE = "la page a changé d'adresse pendant la lecture : rien n'en est publié ; observer à nouveau"
_STALE_MESSAGE = "cet instantané n'existe plus ou n'est pas le dernier : observer à nouveau la page"
_CAPTURE_MESSAGE = "l'image n'est pas publiée : un masque n'a pas pu être posé"
_WAIT_MESSAGE = "la condition attendue n'est pas apparue dans le délai"


# --- Données remises au reste du serveur --------------------------------------------------


@dataclass(frozen=True)
class Box:
    """Un rectangle en pixels CSS du document."""

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

    def contains_point(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def contains(self, other: Box, slack: float = 0.5) -> bool:
        return (
            self.x - slack <= other.x
            and self.y - slack <= other.y
            and other.right <= self.right + slack
            and other.bottom <= self.bottom + slack
        )


@dataclass(frozen=True)
class ElementRef:
    """Un élément repéré tel que la porte de sécurité le lit ; jamais envoyé tel quel au modèle."""

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
    # Ce que le modèle reçoit pour ce repère (nettoyé, valeur retenue si elle est sensible).
    public: dict[str, Any] = field(repr=False, compare=False)
    # Textes de la page autour de l'élément, lus par la porte seule (jamais envoyés tels quels).
    button_value: str = field(default="", repr=False)
    title: str = field(default="", repr=False)
    form_name: str = field(default="", repr=False)
    heading: str = field(default="", repr=False)

    @property
    def gate_texts(self) -> tuple[str, ...]:
        """Les textes qui peuvent dire ce qu'une action fait : nom, valeur du bouton, titre, formulaire, section."""
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
    """Une observation : ce que le serveur affirme, le contenu de la page, l'image."""

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


# --- Lecture de l'instantané --------------------------------------------------------------


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
    # Position dans l'ordre où le navigateur peint, le plus tardif peignant par-dessus le précédent ; None quand elle n'est pas demandée.
    paint_order: int | None = None


class SnapshotFormatError(ValueError):
    """La réponse du navigateur n'a pas la forme que ce module lit."""


class Dom:
    """Le document principal d'une réponse DOMSnapshot.captureSnapshot ; les autres documents sont écartés.

    Le navigateur donne les boîtes et les tailles en pixels de l'appareil ; chaque boîte et chaque taille
    que cette classe expose est en pixels CSS, l'unité de la fenêtre d'affichage et des clics.
    ``device_scale`` est le nombre de pixels de l'appareil dans un pixel CSS : le laisser à 1
    seulement pour un instantané pris à l'échelle 1.
    """

    def __init__(self, raw: dict[str, Any], device_scale: float = 1.0) -> None:
        if not math.isfinite(device_scale) or not DEVICE_SCALE_RANGE[0] <= device_scale <= DEVICE_SCALE_RANGE[1]:
            raise SnapshotFormatError("device_scale")
        try:
            strings = raw["strings"]
            document = raw["documents"][0]
            nodes = document["nodes"]
            layout = document["layout"]
            self.content_width = float(document.get("contentWidth", 0)) / device_scale
            self.content_height = float(document.get("contentHeight", 0)) / device_scale
            self.nodes = self._read_nodes(strings, nodes, layout, device_scale)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SnapshotFormatError(type(exc).__name__) from None
        self.by_backend = {node.backend_id: node for node in self.nodes}
        self._opacity: dict[int, float] = {}
        self._children: dict[int, list[int]] | None = None

    @staticmethod
    def _read_nodes(strings: list[str], nodes: dict[str, Any], layout: dict[str, Any], device_scale: float) -> list[DomNode]:
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
        paint_orders: dict[int, int] = {}
        orders = layout.get("paintOrders") or []
        for position, node_index in enumerate(layout["nodeIndex"]):
            if position < len(orders):
                paint_orders[node_index] = int(orders[position])
            x, y, width, height = (float(v) / device_scale for v in layout["bounds"][position])
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
                    paint_order=paint_orders.get(i),
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
        """Produit des opacités calculées du nœud et de ses ancêtres.

        L'opacité n'est pas héritée : un enfant d'un parent transparent calcule 1 alors qu'il ne montre
        rien. Un nœud de texte porte le style de son parent, si bien que seuls les éléments sont comptés.
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
        """Conditions 1 à 5 : une boîte, pas cachée, pas transparente, pas minuscule, sur la page."""
        box = node.box
        if box is None or not box.finite():
            return False
        if node.styles.get("visibility") in ("hidden", "collapse"):
            return False
        # Arrondi : 0,1 x 0,1 vaut 0,01, pas 0,010000000000000002.
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
        """Le texte des nœuds sous ``node``, dans l'ordre du document, borné."""
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


# --- Noms ---------------------------------------------------------------------------------


def normalized_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def name_hash(name: str) -> str:
    return hashlib.sha256(normalized_name(name).encode("utf-8")).hexdigest()


_QUOTE_ESCAPES = str.maketrans({"\\": "\\\\", '"': '\\"'})


def _quoted(name: str) -> str:
    return '"' + name.translate(_QUOTE_ESCAPES) + '"'


# --- Construction d'un instantané ---------------------------------------------------------


@dataclass
class _Shown:
    ax: dict[str, Any]
    dom: DomNode
    role: str
    name: str
    line: str | None
    in_viewport: bool
    # Posé par _classify, seulement quand un plafond est atteint : fait partie d'une couche au-dessus de la page,
    # et recouvert en son centre par une couche qui n'est pas la sienne.
    layer: bool = False
    covered: bool = False


@dataclass
class _Field:
    sensitive: bool
    card: bool


@dataclass
class Built:
    """Tout ce qui dérive d'un instantané, avant tout travail sur l'image."""

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
    """Les nœuds montrés au modèle, dans l'ordre de l'arbre, et le compte des nœuds cachés.

    Un nœud n'est montré que si l'arbre d'accessibilité ne l'ignore pas et que sa boîte passe chaque
    condition de visibilité ; un nœud caché emporte tout son sous-arbre et compte une fois. Rien sous
    un champ n'est visité : c'est la valeur du champ.
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
            # Pas de boîte propre (display: contents, par exemple) : ses enfants parlent pour eux-mêmes. Si rien
            # d'elle n'apparaît du tout, c'était un nœud caché qui avait quelque chose à dire, et il compte une fois.
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


@dataclass(frozen=True)
class _Cover:
    """Une boîte d'une couche qui prend le pointeur, et quand le navigateur la peint."""

    box: Box
    paint: int
    layer: int


def _paint_key(node: DomNode) -> int:
    # Sans ordre de peinture, l'ordre du document est la meilleure estimation de ce qui peint plus tard.
    return node.paint_order if node.paint_order is not None else node.index


def _classify(walk: _Walk, dom: Dom, viewport: Box) -> None:
    """Dire quels nœuds montrés appartiennent à une couche au-dessus de la page, et lesquels une telle couche recouvre.

    Une couche est une boîte de dialogue, un nœud modal, ou un nœud fixe ou collant qui peint par-dessus
    d'autre contenu en vue. Un nœud est recouvert quand le centre de sa boîte se trouve dans une boîte
    d'une couche qui prend le pointeur, peinte après lui et qui n'est pas la sienne. Tout vient du seul
    instantané : aucun appel par nœud.
    """
    roots = _layer_roots(walk, dom, viewport)
    if not roots:
        return
    members: set[int] = set()
    covers: list[_Cover] = []
    for root in roots:
        stack = [root]
        while stack:
            node = stack.pop()
            members.add(node.index)
            stack.extend(dom.children(node))
        covers.extend(_covers(root, dom))
    root_ids = {root.index for root in roots}
    for item in walk.shown:
        node = item.dom
        item.layer = node.index in members
        if not item.in_viewport or node.box is None:
            continue
        own = {node.index, *(ancestor.index for ancestor in dom.ancestors(node))} & root_ids if item.layer else set()
        x, y = node.box.x + node.box.width / 2, node.box.y + node.box.height / 2
        paint = _paint_key(node)
        item.covered = any(c.layer not in own and c.paint > paint and c.box.contains_point(x, y) for c in covers)


def _layer_roots(walk: _Walk, dom: Dom, viewport: Box) -> list[DomNode]:
    def in_view(node: DomNode) -> bool:
        return node.box is not None and dom.visible(node) and node.box.intersects(viewport)

    roots: dict[int, DomNode] = {}
    for item in walk.shown:
        if item.role in LAYER_ROLES and in_view(item.dom):
            roots[item.dom.index] = item.dom
    positioned: list[DomNode] = []
    for node in dom.nodes:
        if node.node_type != 1 or node.index in roots:
            continue
        if node.attributes.get("aria-modal", "").lower() == "true":
            if in_view(node):
                roots[node.index] = node
        elif node.styles.get("position") in LAYER_POSITIONS and len(positioned) < MAX_LAYER_CANDIDATES and in_view(node):
            positioned.append(node)
    seen = [item.dom for item in walk.shown if item.in_viewport and item.role in CONTENT_ROLES]
    for node in positioned:
        if _paints_over_content(node, seen, dom):
            roots[node.index] = node
    return sorted(roots.values(), key=lambda root: root.index)


def _paints_over_content(layer: DomNode, seen: list[DomNode], dom: Dom) -> bool:
    """Dit si du contenu en vue, hors de la couche, se trouve sous sa boîte et est peint avant elle."""
    assert layer.box is not None
    for node in seen:
        if node.box is None or node.index == layer.index or not node.box.intersects(layer.box):
            continue
        if _paint_key(node) < _paint_key(layer) and all(a.index != layer.index for a in dom.ancestors(node)):
            return True
    return False


def _covers(root: DomNode, dom: Dom) -> list[_Cover]:
    """Les boîtes d'une couche qui prennent le pointeur : une enveloppe transparente qui laisse passer les clics ne recouvre rien."""
    found: list[_Cover] = []
    stack: list[tuple[DomNode, Box | None]] = [(root, None)]
    while stack:
        node, outer = stack.pop()
        inside = outer
        if node.node_type == 1 and node.box is not None and dom.visible(node) and node.styles.get("pointer-events") != "none":
            if outer is None or not outer.contains(node.box):
                found.append(_Cover(box=node.box, paint=_paint_key(node), layer=root.index))
                inside = node.box
        stack.extend((child, inside) for child in dom.children(node))
    return found


def _rank(item: _Shown) -> int:
    """0 dans une couche au-dessus de la page ; 1 en vue ; 2 en vue et recouvert ; 3 le reste."""
    if item.layer:
        return 0
    if item.in_viewport:
        return 2 if item.covered else 1
    return 3


def field_verdicts(dom: Dom, ax: dict[str, dict[str, Any]] | None = None) -> dict[int, _Field]:
    """Le prédicat de champ sensible, demandé une fois pour chaque input, textarea et select."""
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
    """Boîtes des champs sensibles et des cadres qui traversent la fenêtre d'affichage."""
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


def _published_line(item: _Shown) -> str:
    """Le texte d'une ligne de l'arbre tel qu'il quitte le module : la valeur d'un champ n'y figure pas."""
    return redact_text(item.line or "")


def _filtered(
    walk: _Walk, region: str | None, contains: str | None, filter_text: Callable[[_Shown], str]
) -> tuple[_Walk, observation_filter.Selection]:
    """Le parcours réduit à ce que le filtre garde ; les profondeurs repartent de la plus haute ligne gardée."""
    shown = walk.shown
    selection = observation_filter.select(
        walk.ax,
        [item.ax.get("nodeId") for item in shown],
        [item.line is not None for item in shown],
        walk.depths,
        region=region,
        contains=contains,
        text_of=lambda position: filter_text(shown[position]),
    )
    kept = selection.positions
    base = min((walk.depths[p] for p in kept if shown[p].line is not None), default=0)
    reduced = _Walk(
        shown=[shown[p] for p in kept],
        depths=[max(walk.depths[p] - base, 0) for p in kept],
        omitted=walk.omitted,
        root=walk.root,
        ax=walk.ax,
    )
    return reduced, selection


def build(
    ax_raw: dict[str, Any],
    dom: Dom,
    viewport: Box,
    origin: Origin,
    *,
    tree_builder: Callable[..., list[str]] | None = None,
    region: str | None = None,
    contains: str | None = None,
    filter_text: Callable[[_Shown], str] | None = None,
) -> Built:
    """Fusionne l'arbre d'accessibilité et l'instantané du document principal.

    ``counters.redacted_fields`` compte les champs sensibles visibles dont la valeur n'était pas vide
    et a été retenue ; un champ sensible vide est marqué sensible mais ne compte rien, puisqu'il n'y a
    rien à retenir. ``counters.omitted_not_visible`` compte chaque sous-arbre caché une fois.

    ``region`` et ``contains`` réduisent l'arbre et les éléments avant les plafonds ; ils lisent les lignes
    déjà rédigées, ``filter_text`` ne sert qu'aux tests. La porte lit toute la page, filtrée ou non.
    """
    full_walk = _walk(ax_raw, dom, viewport)
    walk = full_walk
    selection: observation_filter.Selection | None = None
    if observation_filter.active(region, contains):
        walk, selection = _filtered(full_walk, region, contains, filter_text or _published_line)
    candidates = sorted(
        (item for item in walk.shown if item.role in MARKABLE_ROLES or (item.role == "generic" and item.dom.clickable)),
        key=lambda item: item.dom.index,
    )
    lines_shown = sum(1 for item in walk.shown if item.line is not None)
    if len(candidates) > MAX_MARKS or lines_shown > MAX_TREE_LINES:
        _classify(walk, dom, viewport)
    counters: dict[str, Any] = {
        "omitted_not_visible": walk.omitted,
        "redacted_fields": 0,
        "frames_masked": 0,
        "marks": 0,
        "marks_truncated": False,
        "tree_lines": 0,
        "tree_truncated": False,
    }
    if selection is not None:
        counters["lines_before_filter"] = selection.lines_before
        counters["lines_after_filter"] = selection.lines_after
        if selection.region_found is not None:
            counters["region_found"] = selection.region_found
    fields = field_verdicts(dom, walk.ax)

    # Les éléments que le modèle peut désigner : quand il y en a trop, ceux au-dessus de la page d'abord,
    # puis ceux en vue ; ceux qui sont gardés sont numérotés dans l'ordre du document.
    kept, counters["marks_truncated"] = _keep(candidates, MAX_MARKS, key=lambda item: item.dom.index, rank=_rank)
    elements = [
        _with_gate_texts(_element(mark, item, dom, fields, origin, counters), item, dom, full_walk)
        for mark, item in enumerate(kept, start=1)
    ]
    if counters["marks_truncated"]:
        # Dit seulement quand il a fallu choisir : en vue, mais sous une couche qui prend le clic.
        for element, item in zip(elements, kept, strict=True):
            if item.covered:
                element.public["covered"] = True
    counters["marks"] = len(elements)
    page_has_card_field = any(
        fields[item.dom.index].card for item in full_walk.shown if item.dom.index in fields
    )

    notices: list[str] = []
    try:
        tree_lines = (tree_builder or _tree)(walk.shown, walk.depths, counters)
    except Exception:  # noqa: BLE001 - un arbre en échec est retenu, jamais publié à moitié nettoyé
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
    by_rank: Callable[[tuple[int, _Shown]], int] = lambda pair: _rank(pair[1])  # noqa: E731
    kept, counters["tree_truncated"] = _keep(lines, MAX_TREE_LINES, key=lambda pair: pair[0], rank=by_rank)
    if any(item.layer for _position, item in kept):
        # La ligne qui introduit la couche prend l'une des lignes.
        kept, counters["tree_truncated"] = _keep(lines, MAX_TREE_LINES - 1, key=lambda pair: pair[0], rank=by_rank)
    layer = [(position, item) for position, item in kept if item.layer]
    # Rédaction des secrets enregistrés, avant que les lignes quittent le module.
    output = []
    if layer:
        output.append(redact_text(OVERLAY_LINE))
        base = min(depths[position] for position, _item in layer)
        output += [redact_text("  " * (depths[position] - base + 1) + (item.line or "")) for position, item in layer]
    output += [redact_text("  " * depths[position] + (item.line or "")) for position, item in kept if not item.layer]
    return output


def _keep(items: list[Any], limit: int, *, key: Callable[[Any], int], rank: Callable[[Any], int]) -> tuple[list[Any], bool]:
    """Au plus ``limit`` éléments : les rangs les plus bas d'abord, puis l'ordre du document ; ceux qui sont gardés le sont dans l'ordre du document."""
    if len(items) <= limit:
        return items, False
    ordered = sorted(items, key=lambda item: (rank(item), key(item)))
    return sorted(ordered[:limit], key=key), True


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
    """Ajoute les textes autour de l'élément que la porte lit pour décider s'il faut armer."""
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
    """Les éléments sans boîte (display: none) que l'arbre d'accessibilité laisse entièrement de côté.

    Chaque sous-arbre caché compte une fois, à son élément de tête.
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
    """Vrai quand un champ sensible que l'arbre montre n'a pas de boîte utilisable : il n'a pas pu être masqué."""
    shown_by_tree = {
        node.get("backendDOMNodeId") for node in ax.values() if not node.get("ignored") and isinstance(node.get("backendDOMNodeId"), int)
    }
    for index, verdict in fields.items():
        node = dom.nodes[index]
        if verdict.sensitive and (node.box is None or not node.box.finite()) and node.backend_id in shown_by_tree:
            return True
    return False


# --- Image --------------------------------------------------------------------------------

MaskDrawer = Callable[[Image.Image, tuple[int, int, int, int], str], None]


def draw_mask(image: Image.Image, rect: tuple[int, int, int, int], kind: str) -> None:
    """Un rectangle plein ; sur un cadre, avec l'avis écrit dedans."""
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        return
    tile = Image.new("RGB", (x1 - x0, y1 - y0), MASK_COLOUR)
    if kind == "frame":
        pen = ImageDraw.Draw(tile)
        pen.fontmode = "1"  # deux couleurs seulement : aucun mélange avec le remplissage
        font, text = _notice_font()
        pen.text((6, 6), text, fill=MASK_TEXT_COLOUR, font=font)
    image.paste(tile, (x0, y0))


@functools.cache
def _notice_font() -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
    # La police fournie avec Pillow n'a pas de lettres accentuées.
    for name in NOTICE_FONTS:
        try:
            return ImageFont.truetype(name, 14), FRAME_NOTICE
        except OSError:
            continue
    return ImageFont.load_default(size=14), FRAME_NOTICE_PLAIN


def _decode_png(png: bytes) -> Image.Image:
    # Décodée depuis la mémoire : l'image ne touche jamais un fichier.
    parser = ImageFile.Parser()
    parser.feed(png)
    image = parser.close()
    if image.format != "PNG":
        raise ValueError("the browser did not return a PNG image")
    return image.convert("RGB")


class CaptureFailed(Exception):
    """Un masque n'a pas pu être placé : l'image ne doit pas quitter le serveur."""


@dataclass(frozen=True)
class Rendered:
    png: bytes
    width: int
    height: int
    scale: float
    mask_rects: tuple[tuple[int, int, int, int], ...]
    mark_rects: dict[int, tuple[int, int, int, int]]


def _image_rect(box: Box, viewport: Box, sx: float, sy: float, size: tuple[int, int]) -> tuple[int, int, int, int]:
    # Arrondi vers l'extérieur, rogné à l'image : un masque ne laisse jamais un pixel de sa boîte découvert.
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
    device_view: tuple[float, float] | None = None,
) -> Rendered:
    """D'abord les masques, sur l'image entière ; puis la réduction ; puis les repères, jamais sur un masque.

    ``device_view`` est la taille de la vue en pixels de l'appareil, telle que le navigateur l'énonce.
    L'image est une découpe de la vue depuis son coin supérieur gauche, arrondie à des pixels entiers,
    pas la vue étirée pour s'y ajuster : quand elle est un pixel trop courte, une boîte est tout de même
    placée avec le rapport du navigateur entre la vue et sa taille CSS, et le rapport de l'image à la vue
    déplacerait une boîte du bord lointain jusqu'à un pixel, loin de son champ. Sans lui, l'image est
    supposée couvrir la fenêtre d'affichage.
    """
    try:
        image = _decode_png(png)
        span_width, span_height = device_view if device_view is not None else image.size
        sx = span_width / viewport.width if viewport.width > 0 else 1.0
        sy = span_height / viewport.height if viewport.height > 0 else 1.0
        masks = [(box, "field") for box in sensitive_boxes] + [(box, "frame") for box in frame_boxes]
        for box, kind in masks:
            mask_drawer(image, _image_rect(box, viewport, sx, sy, image.size), kind)
        scale = 1.0
        if max_width is not None and image.width > max_width:
            scale = max_width / image.width
            image = image.resize((max_width, max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
            sx, sy = sx * scale, sy * scale
            # La réduction mélange les bords de chaque masque avec leurs voisins :
            # les masques sont posés de nouveau à la taille réduite.
            for box, kind in masks:
                mask_drawer(image, _image_rect(box, viewport, sx, sy, image.size), kind)
        mask_rects = tuple(_image_rect(box, viewport, sx, sy, image.size) for box, _kind in masks)
    except CaptureFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - tout échec pendant le masquage garde l'image chez soi
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
    # Les repères sont dessinés après les masques mais jamais dessus.
    for x0, y0, x1, y1 in mask_rects:
        if x1 > x0 and y1 > y0:
            overlay.paste((0, 0, 0, 0), (x0, y0, x1, y1))
    base = image.convert("RGBA")
    base.alpha_composite(overlay)
    image.paste(base.convert("RGB"))
    return placed


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# --- Le module qu'utilisent les outils ----------------------------------------------------

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
    # La vue en pixels de l'appareil, telle que le navigateur l'énonce ; lue avec l'image, qui est placée d'après elle.
    device_view: tuple[float, float] | None = None
    # Faux quand les boîtes à masquer ont bougé entre l'instantané et l'image.
    masks_stable: bool = True


class Perception:
    """Observations des onglets possédés, filtrées par les sites autorisés par l'opérateur."""

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

    # --- opérations publiques -----------------------------------------------------------------

    async def observe(
        self, tab: Tab, *, image: bool = True, region: str | None = None, contains: str | None = None
    ) -> Observation:
        """Lit la page une fois, vérifie son origine avant et après, et construit ce que voit le modèle.

        ``region`` et ``contains`` réduisent l'arbre et les éléments rendus (``observation_filter``)."""
        for _attempt in range(2):
            try:
                reading = await self._read(tab, image=image, full=True)
            except DialogBlocking:
                return self._dialog_observation(tab)
            except _PageChanged:
                continue
            return self._observation(tab, reading, image=image, region=region, contains=contains)
        raise ToolError("page_changed_during_capture", _CHANGED_MESSAGE)

    def latest_snapshot(self, tab_id: str) -> Snapshot | None:
        """Le dernier instantané de l'onglet, s'il y en a un ; il peut décrire une page qui a bougé depuis."""
        kept = self._snapshots.get(tab_id)
        return kept[-1] if kept else None

    def snapshot(self, tab_id: str, snapshot_id: str) -> Snapshot:
        """L'un des derniers instantanés de l'onglet, ou ``snapshot_stale``."""
        for snapshot in self._snapshots.get(tab_id, ()):
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        raise ToolError("snapshot_stale", _STALE_MESSAGE)

    async def capture_masked_png(self, tab: Tab) -> bytes:
        """La fenêtre d'affichage avec les mêmes masques et sans repères, pour la preuve ; rien d'autre n'en fait une."""
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
                    device_view=reading.device_view,
                )
            except CaptureFailed:
                raise ToolError("capture_unavailable", _CAPTURE_MESSAGE) from None
            return rendered.png
        raise ToolError("page_changed_during_capture", _CHANGED_MESSAGE)

    async def wait_for(
        self, tab: Tab, *, kind: str, value: Any = None, timeout_s: float = DEFAULT_WAIT_S
    ) -> WaitResult:
        """Attend le chargement, un texte ou un élément, par des lectures légères toutes les 300 ms.

        N'attend jamais un réseau calme. ``wait_timeout`` à l'échéance.
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

    # --- lecture ------------------------------------------------------------------------------

    async def _gate(self, tab: Tab) -> tuple[FrameInfo, Origin]:
        """L'origine du cadre principal, refusée sauf si l'opérateur l'a autorisée."""
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
        zoom = _page_zoom(metrics) if image else None
        if zoom is not None:
            shot = await self._registry.read(
                tab,
                "Page.captureScreenshot",
                {"format": "png", "clip": _screenshot_clip(viewport, zoom), "captureBeyondViewport": False},
            )
            try:
                png = base64.b64decode(shot.get("data", ""), validate=True)
            except (binascii.Error, ValueError, TypeError):
                png = None
            if png is not None and not _image_fits_view(png, metrics):
                png = None
        dom = Dom(raw, _device_scale(metrics))
        masks_stable = True
        if png is not None:
            # L'image est prise après l'instantané : si un champ sensible ou un cadre a bougé entre-temps, son masque
            # le manquerait. Relire les boîtes et ne garder l'image que si elles n'ont pas bougé, ni le zoom
            # auquel la découpe a été faite.
            metrics_after = await self._registry.read(tab, "Page.getLayoutMetrics")
            dom_after = Dom(
                await self._registry.read(tab, "DOMSnapshot.captureSnapshot", LIGHT_SNAPSHOT_PARAMS), _device_scale(metrics_after)
            )
            masks_stable = (
                _viewport(metrics_after) == viewport
                and _page_zoom(metrics_after) == zoom
                and mask_boxes(dom, viewport, field_verdicts(dom)) == mask_boxes(dom_after, viewport, field_verdicts(dom_after))
            )
        after = await self._registry.main_frame_info(tab, stop_on_dialog=True)
        if after.origin != frame.origin or after.loader_id != frame.loader_id:
            raise _PageChanged()
        return _Reading(
            frame=frame,
            origin=origin,
            viewport=viewport,
            ax=ax,
            dom=dom,
            png=png,
            device_view=_device_view(metrics) if png is not None else None,
            masks_stable=masks_stable,
        )

    # --- construction des sorties -------------------------------------------------------------

    def _observation(
        self, tab: Tab, reading: _Reading, *, image: bool, region: str | None = None, contains: str | None = None
    ) -> Observation:
        built = build(
            reading.ax,
            reading.dom,
            reading.viewport,
            reading.origin,
            tree_builder=self._tree_builder,
            region=region,
            contains=contains,
        )
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
                    device_view=reading.device_view,
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
            # Le document auquel appartiennent les repères : une navigation en donne un nouveau.
            "loader_id": snapshot.loader_id,
            # La partie de la page en vue et jusqu'où le document s'étend, en pixels CSS.
            "viewport": {
                "width": reading.viewport.width,
                "height": reading.viewport.height,
                "scroll_x": reading.viewport.x,
                "scroll_y": reading.viewport.y,
                "content_height": reading.dom.content_height,
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


def _device_scale(metrics: dict[str, Any]) -> float:
    """Pixels de l'appareil par pixel CSS, d'après les deux descriptions de la fenêtre d'affichage visuelle.

    L'instantané est en pixels de l'appareil et la fenêtre en pixels CSS, donc aucun des deux ne donne le
    facteur seul ; le navigateur donne la fenêtre dans les deux unités et leur rapport est le facteur. Un
    zoom par pincement divise les deux par le même nombre : le rapport ne bouge pas. Une page qui ne peut
    pas être mise dans une seule unité est refusée, pas lue comme si le facteur valait 1 : un masque mal
    placé publierait le champ qu'il devait cacher.
    """
    try:
        device = float(metrics["visualViewport"]["clientWidth"])
        css = float(metrics["cssVisualViewport"]["clientWidth"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotFormatError(type(exc).__name__) from None
    if not (math.isfinite(device) and math.isfinite(css)) or device <= 0 or css <= 0:
        raise SnapshotFormatError("device_scale")
    return device / css


def _page_zoom(metrics: dict[str, Any]) -> float | None:
    """Le zoom de page (la touche Ctrl + du navigateur), ou None quand les métriques n'en donnent pas d'utilisable.

    La découpe d'une image se fait en pixels CSS multipliés par ce nombre. Le lire comme 1 quand il manque
    découperait une autre partie de la page que celle sur laquelle les masques sont posés, et l'image
    partirait avec ses champs sensibles en vue : sans zoom, il n'y a pas d'image.
    """
    try:
        zoom = metrics["visualViewport"]["zoom"]
    except (KeyError, TypeError):
        return None
    if isinstance(zoom, bool) or not isinstance(zoom, (int, float)):
        return None
    zoom = float(zoom)
    return zoom if math.isfinite(zoom) and zoom > 0 else None


def _screenshot_clip(viewport: Box, zoom: float) -> dict[str, float]:
    """La partie de la page à capturer, dans l'unité où le navigateur lit une découpe : pixels CSS multipliés par le zoom."""
    return {"x": viewport.x * zoom, "y": viewport.y * zoom, "width": viewport.width * zoom, "height": viewport.height * zoom, "scale": 1}


def _device_view(metrics: dict[str, Any]) -> tuple[float, float]:
    """La taille de la vue en pixels de l'appareil, telle que le navigateur l'énonce ; refusée si ce n'est pas une taille utilisable."""
    try:
        device = metrics["visualViewport"]
        size = (float(device["clientWidth"]), float(device["clientHeight"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotFormatError(type(exc).__name__) from None
    if not all(math.isfinite(side) and side > 0 for side in size):
        raise SnapshotFormatError("device_view")
    return size


def _image_fits_view(png: bytes, metrics: dict[str, Any]) -> bool:
    """Vrai quand l'image a la taille de la vue en pixels de l'appareil, à l'arrondi de la découpe près.

    Quelle qu'en soit la raison, une image d'une autre taille ne montre pas la vue sur laquelle les masques sont posés.
    """
    try:
        wanted = _device_view(metrics)
        size = _decode_png(png).size
    except Exception:  # noqa: BLE001 - une taille illisible est une taille qu'on ne peut pas contrôler
        return False
    return all(abs(got - want) <= IMAGE_SIZE_SLACK_PX for got, want in zip(size, wanted, strict=True))


def _walk_names(ax_raw: dict[str, Any], dom: Dom, viewport: Box) -> list[tuple[str, str]]:
    return [(item.role, item.name) for item in _walk(ax_raw, dom, viewport).shown]


def _wait_timeout(timeout_s: object) -> float:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s >= 0:
        raise ValueError("timeout_s is a non-negative number of seconds")
    return min(float(timeout_s), MAX_WAIT_S)
