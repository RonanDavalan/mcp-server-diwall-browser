"""Agir dans un onglet possédé : cliquer, remplir, choisir une option, presser une touche, défiler, répondre à une boîte de dialogue.

Pourquoi ce module existe :
    Une action est l'endroit où une erreur ou une injection devient réelle : de l'argent dépensé,
    des données supprimées, un secret saisi sur le mauvais site. Toute action passe par un seul
    pipeline qui vérifie que l'élément est toujours celui que le modèle a vu, interroge la porte,
    écrit le journal et garde une preuve avant une action armée, et ne la laisse partir qu'avec
    le consentement d'un humain donné dans une fenêtre que le modèle ne peut pas atteindre.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import credentials
from .browser import DialogBlocking, FrameInfo, Tab, TabRegistry, dialog_refusal, origin_label
from .cdp import CdpError
from .confirm import Channel, ConfirmationDecision, ConfirmRequest, find_amounts, refusal, request_confirmation
from .evidence import DownloadManager, EvidenceWriter
from .gate import NOT_ARMED, GateDecision, Pacer, classify
from .journal import Journal, JournalEntry
from .mount import Volume
from .operator_profile import Profile
from .origin import Origin, OriginError, form_target_origin, origin_allowed, origin_of_url
from .output import ToolError, bounded_text, current_operation_id, new_operation_id
from .perception import LIGHT_SNAPSHOT_PARAMS, NAME_LIMIT, Dom, ElementRef, Perception, Snapshot, name_hash

FILL_INPUT_TYPES = frozenset({"", "text", "search", "email", "tel", "url", "number", "password"})
# Touche -> (code, code de touche virtuelle Windows). Seules les touches qui déplacent ou valident :
# aucun raccourci, aucun caractère tapé par ce chemin.
KEYS: dict[str, tuple[str, int]] = {
    "Enter": ("Enter", 13),
    "Tab": ("Tab", 9),
    "Escape": ("Escape", 27),
    "ArrowUp": ("ArrowUp", 38),
    "ArrowDown": ("ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", 39),
    "PageUp": ("PageUp", 33),
    "PageDown": ("PageDown", 34),
    "Home": ("Home", 36),
    "End": ("End", 35),
}
MAX_SCROLL = 5000
SCROLL_TO_ATTEMPTS = 20
SETTLE_START_S = 0.5
SETTLE_LOAD_S = 10.0
# Un chargement que le geste a lancé peut devenir un téléchargement ; il commence au plus tard ce laps de
# temps après la fin du chargement, et le navigateur a le même temps pour le prendre en charge avant que
# le réglage soit refermé. Un téléchargement qui commence plus tard est refusé.
SETTLE_DOWNLOAD_START_S = 0.5
FORM_TEXT_LIMIT = 4000

_STALE_SNAPSHOT = "la page a changé depuis l'observation : observer à nouveau avant d'agir"
_STALE_ELEMENT = "cet élément n'est plus celui de l'observation : observer à nouveau avant d'agir"
_UNKNOWN_MARK = "ce repère n'existe pas dans l'instantané : observer à nouveau avant d'agir"
_OBSCURED = "un autre élément recouvre la cible à cet endroit : rien n'a été cliqué"
_NO_OFFSET = "la position de la page n'a pas pu être lue : ce qui recouvre la cible n'est pas connu, rien n'a été cliqué"
_FOCUS_MOVED = "le curseur n'est pas dans l'élément visé : rien n'a été saisi"
_ORIGIN_REFUSED = "cette page n'est pas sur un site autorisé par le profil : aucune action n'y est faite"
_SENSITIVE = (
    "ce champ est sensible : il ne se remplit que par un identifiant du répertoire chiffré (credential_ref), "
    "jamais par un texte"
)


@dataclass(frozen=True)
class ActionOutcome:
    """Ce qu'une action rapporte ; ``gate`` alimente la ligne de journal finale que l'outil écrit."""

    server: dict[str, Any]
    typed_marker: str | None
    gate: dict[str, Any]


@dataclass
class ActionContext:
    """Tout ce dont une action a besoin, donné par l'appelant ; rien n'est global."""

    profile: Callable[[], Profile]
    registry: TabRegistry
    perception: Perception
    pacer: Pacer
    channel: Channel
    journal: Journal
    volume: Volume
    evidence: EvidenceWriter
    downloads: DownloadManager
    settle_start_s: float = SETTLE_START_S
    settle_load_s: float = SETTLE_LOAD_S
    settle_download_start_s: float = SETTLE_DOWNLOAD_START_S


@dataclass(frozen=True)
class Verified:
    """Ce que le navigateur dit maintenant d'un élément repéré, après le contrôle d'identité."""

    frame: FrameInfo
    role: str
    name: str
    object_id: str


@dataclass
class _Run:
    """L'état d'une action quand elle traverse le pipeline."""

    tool: str
    operation_id: str
    intent: str
    notices: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=lambda: {"red_list": False, "confirmation": "none"})
    dialog_opened: bool = False
    # L'origine contrôlée contre les sites autorisés, pour la ligne de journal de l'appel.
    origin: str | None = None


class Actions:
    def __init__(self, context: ActionContext) -> None:
        self._context = context

    # --- cliquer ------------------------------------------------------------------------------

    async def click(
        self, tab_id: str, snapshot_id: str, mark: int, *, intent: str = "", intent_category: str | None = None
    ) -> ActionOutcome:
        context = self._context
        run = self._start("click", intent)
        await context.pacer.before_action("click")
        tab, snapshot, ref = self._target(tab_id, snapshot_id, mark)
        profile = context.profile()
        verified = await self.verify_element(tab, snapshot, ref)
        run.origin = self._require_allowed(profile, verified.frame)
        decision = classify(ref, action_kind="click", intent_category=intent_category, snapshot=snapshot)
        await self._guard(run, tab, profile, "click", ref, verified, decision)
        if run.gate["confirmation"] == "granted":
            verified = await self.verify_element(tab, snapshot, ref)
        await self._with_gate(run, context.downloads.prepare(run.operation_id))
        try:
            await self._with_gate(run, self._click_at(run, tab, ref))
            return await self._finish(run, tab, mark=mark)
        finally:
            await context.downloads.forbid()

    async def _click_at(self, run: _Run, tab: Tab, ref: ElementRef) -> None:
        backend = ref.backend_node_id
        await self._read(tab, "DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend})
        centre_x, centre_y = await self._centre(tab, backend)
        x, y = round(centre_x), round(centre_y)
        # Les quadrilatères et les événements de souris sont dans la vue, mais la recherche de nœud lit un point
        # du document : demandée à un point de la vue d'une page défilée, elle répond pour un autre endroit, ou pour aucun.
        scroll_x, scroll_y = await self._viewport(tab, strict=True)
        await self._require_target_at(tab, backend, round(centre_x + scroll_x), round(centre_y + scroll_y))
        await self._gesture(
            run, tab, [("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "clickCount": 0})]
        )
        if run.dialog_opened:
            return
        await self._recheck_before_press(tab, backend, x, y)
        await self._gesture(
            run,
            tab,
            [
                ("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}),
                ("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}),
            ],
            started=True,
        )

    async def _recheck_before_press(self, tab: Tab, backend: int, x: int, y: int) -> None:
        """Refuser la pression sauf si la cible, ou l'un de ses descendants, se trouve encore sous le pointeur.

        Le mouvement du pointeur est ce qui fait bouger certaines pages (un menu qui s'ouvre, une bannière qui
        glisse) ou recouvre la cible sans la déplacer, et la pression va là où est le pointeur. La question est
        donc reposée à chaque fois, jamais seulement quand la boîte ou le décalage a changé : une cible recouverte
        là où elle se trouve a la même boîte et le même décalage.
        """
        try:
            await self._centre(tab, backend)
        except CdpError:
            # Le mouvement a emporté l'élément, ou sa boîte : il n'y a rien à presser.
            raise ToolError("element_obscured", _OBSCURED) from None
        scroll_x, scroll_y = await self._viewport(tab, strict=True)
        await self._require_target_at(tab, backend, round(x + scroll_x), round(y + scroll_y))

    async def _centre(self, tab: Tab, backend: int) -> tuple[float, float]:
        """Le milieu de la boîte d'un élément, dans la vue ; refuser un élément sans boîte."""
        quads = (await self._read(tab, "DOM.getContentQuads", {"backendNodeId": backend})).get("quads") or []
        if not quads or len(quads[0]) != 8:
            raise ToolError("element_obscured", _OBSCURED)
        return sum(quads[0][0::2]) / 4, sum(quads[0][1::2]) / 4

    async def _require_target_at(self, tab: Tab, backend: int, x: int, y: int) -> None:
        """Refuser sauf si la cible, ou l'un de ses descendants, est ce qui se trouve à un point du document."""
        hit = await self._read(tab, "DOM.getNodeForLocation", {"x": x, "y": y, "includeUserAgentShadowDOM": False})
        hit_id = hit.get("backendNodeId")
        if hit_id != backend:
            # Le clic tombe sur ce que l'humain voit à cet endroit, jamais sur ce qui se trouve dessous.
            if not isinstance(hit_id, int) or not await self._contains(tab, backend, hit_id):
                raise ToolError("element_obscured", _OBSCURED)

    # --- remplir et choisir -------------------------------------------------------------------

    async def fill(
        self,
        tab_id: str,
        snapshot_id: str,
        mark: int,
        *,
        text: str | None = None,
        credential_ref: dict[str, Any] | None = None,
        clear: bool = True,
        intent: str = "",
    ) -> ActionOutcome:
        if (text is None) == (credential_ref is None):
            raise ValueError("fill takes either text or credential_ref, never both, never none")
        context = self._context
        run = self._start("fill", intent)
        await context.pacer.before_action("fill")
        tab, snapshot, ref = self._target(tab_id, snapshot_id, mark)
        profile = context.profile()
        verified = await self.verify_element(tab, snapshot, ref)
        run.origin = self._require_allowed(profile, verified.frame)
        if ref.tag not in ("input", "textarea") or (ref.tag == "input" and (ref.input_type or "") not in FILL_INPUT_TYPES):
            raise ToolError("unsupported_element", "cet élément ne se remplit pas : un champ de texte ou une zone de texte est attendu")
        if ref.sensitive and text is not None:
            raise ToolError("sensitive_value_refused", _SENSITIVE)
        key = name = None
        if credential_ref is not None:
            key, name = _credential_reference(credential_ref)
            # Refusé avant toute confirmation : le consentement ne rend pas bon un mauvais site.
            self._resolve(await self._frame(tab), key, name)
        await self._guard(run, tab, profile, "fill", ref, verified, NOT_ARMED)
        if run.gate["confirmation"] == "granted":
            verified = await self.verify_element(tab, snapshot, ref)
        await self._with_gate(run, self._focus_into(tab, ref, verified.object_id, select=clear))
        if key is not None:
            # L'origine lue dans le navigateur au moment de la frappe, pas celle de l'instantané ni celle que le modèle nomme.
            secret = self._resolve(await self._frame(tab), key, name)
            await self._with_gate(run, self._read(tab, "Input.insertText", {"text": secret.reveal()}))
            del secret
            marker = f"<secret:{key}>"
        else:
            await self._with_gate(run, self._read(tab, "Input.insertText", {"text": text}))
            marker = "<typed>"
        return await self._finish(run, tab, mark=mark, typed_marker=marker)

    async def select_option(self, tab_id: str, snapshot_id: str, mark: int, option: str, *, intent: str = "") -> ActionOutcome:
        if not isinstance(option, str):
            raise ValueError("option is a string")
        context = self._context
        run = self._start("select_option", intent)
        await context.pacer.before_action("select_option")
        tab, snapshot, ref = self._target(tab_id, snapshot_id, mark)
        profile = context.profile()
        verified = await self.verify_element(tab, snapshot, ref)
        run.origin = self._require_allowed(profile, verified.frame)
        if ref.tag != "select":
            raise ToolError("unsupported_element", "seule une liste de choix native (select) se règle ainsi")
        await self._guard(run, tab, profile, "select_option", ref, verified, NOT_ARMED)
        if run.gate["confirmation"] == "granted":
            verified = await self.verify_element(tab, snapshot, ref)
        result = await self._with_gate(
            run, self._script(tab, "set_select_option", object_id=verified.object_id, arguments=[{"value": option}])
        )
        if _script_value(result) is not True:
            raise ToolError("option_not_found", "aucune option de cette liste ne porte ce texte ou cette valeur")
        return await self._finish(run, tab, mark=mark)

    # --- touches ------------------------------------------------------------------------------

    async def press_key(
        self,
        tab_id: str,
        key: str,
        *,
        snapshot_id: str | None = None,
        mark: int | None = None,
        intent: str = "",
        intent_category: str | None = None,
    ) -> ActionOutcome:
        context = self._context
        run = self._start("press_key", intent)
        await context.pacer.before_action("press_key")
        if key not in KEYS:
            raise ToolError("unsupported_element", "touche refusée ; touches permises : " + ", ".join(KEYS))
        tab = context.registry.get(tab_id)
        context.registry.ensure_no_dialog(tab)
        profile = context.profile()
        if key != "Enter":
            run.origin = self._require_allowed(profile, await self._frame(tab))
            await self._with_gate(run, self._key(run, tab, key))
            return await self._finish(run, tab, mark=None)
        if snapshot_id is None or mark is None:
            raise ToolError("unsupported_element", "la touche Entrée exige un repère (snapshot_id et mark)")
        tab, snapshot, ref = self._target(tab_id, snapshot_id, mark)
        verified = await self.verify_element(tab, snapshot, ref)
        run.origin = self._require_allowed(profile, verified.frame)
        await self._focus_into(tab, ref, verified.object_id, select=False)
        decision = classify(ref, action_kind="enter_key", intent_category=intent_category, snapshot=snapshot)
        await self._guard(run, tab, profile, "enter_key", ref, verified, decision)
        if run.gate["confirmation"] == "granted":
            verified = await self.verify_element(tab, snapshot, ref)
            await self._focus_into(tab, ref, verified.object_id, select=False)
        await self._with_gate(run, context.downloads.prepare(run.operation_id))
        try:
            await self._with_gate(run, self._key(run, tab, key))
            return await self._finish(run, tab, mark=mark)
        finally:
            await context.downloads.forbid()

    async def _key(self, run: _Run, tab: Tab, key: str) -> None:
        code, virtual = KEYS[key]
        down: dict[str, Any] = {"type": "keyDown", "key": key, "code": code, "windowsVirtualKeyCode": virtual}
        if key == "Enter":
            # Sans le caractère, le navigateur ne soumet pas le formulaire.
            down.update(text="\r", unmodifiedText="\r")
        up = {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": virtual}
        await self._gesture(run, tab, [("Input.dispatchKeyEvent", down), ("Input.dispatchKeyEvent", up)])

    # --- défiler ------------------------------------------------------------------------------

    async def scroll(
        self,
        tab_id: str,
        *,
        dy: int | None = None,
        to: str | None = None,
        mark: int | None = None,
        snapshot_id: str | None = None,
    ) -> ActionOutcome:
        if sum(value is not None for value in (dy, to, mark)) != 1:
            raise ValueError("scroll takes exactly one of dy, to and mark")
        if dy is not None and (isinstance(dy, bool) or not isinstance(dy, int) or not -MAX_SCROLL <= dy <= MAX_SCROLL):
            raise ValueError("dy is a whole number of CSS pixels from -5000 to 5000")
        if to is not None and to not in ("top", "bottom"):
            raise ValueError("to is top or bottom")
        context = self._context
        run = self._start("scroll", "")
        await context.pacer.before_action("scroll")
        tab = context.registry.get(tab_id)
        context.registry.ensure_no_dialog(tab)
        if mark is not None:
            if snapshot_id is None:
                raise ValueError("scrolling to a mark needs its snapshot_id")
            tab, snapshot, ref = self._target(tab_id, snapshot_id, mark)
            await self.verify_element(tab, snapshot, ref)
            await self._read(tab, "DOM.scrollIntoViewIfNeeded", {"backendNodeId": ref.backend_node_id})
        elif dy is not None:
            await self._wheel(run, tab, dy)
        else:
            delta = -MAX_SCROLL if to == "top" else MAX_SCROLL
            for _attempt in range(SCROLL_TO_ATTEMPTS):
                before = await self._viewport(tab)
                await self._wheel(run, tab, delta)
                await asyncio.sleep(0.05)
                if await self._viewport(tab) == before:
                    break
        return await self._finish(run, tab, mark=mark, settle=False)

    async def _wheel(self, run: _Run, tab: Tab, dy: int) -> None:
        viewport = (await self._read(tab, "Page.getLayoutMetrics")).get("cssVisualViewport", {})
        x = round(float(viewport.get("clientWidth", 0)) / 2)
        y = round(float(viewport.get("clientHeight", 0)) / 2)
        await self._gesture(
            run, tab, [("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy})]
        )

    async def _viewport(self, tab: Tab, *, strict: bool = False) -> tuple[float, float]:
        """Où la vue commence dans le document, en pixels CSS.

        Un décalage que le navigateur ne donne pas se lit comme 0, ce qui suffit pour voir si un défilement a
        bougé. ``strict`` le refuse à la place : un clic qui ne peut pas placer sa recherche dans le document ne
        sait pas ce qui se trouve sous le point qu'il cliquerait.
        """
        visual = (await self._read(tab, "Page.getLayoutMetrics")).get("cssVisualViewport", {})
        if strict and not (isinstance(visual, dict) and all(_is_finite_number(visual.get(key)) for key in ("pageX", "pageY"))):
            raise ToolError("element_obscured", _NO_OFFSET)
        return float(visual.get("pageX", 0)), float(visual.get("pageY", 0))

    # --- boîtes de dialogue -------------------------------------------------------------------

    async def handle_dialog(self, tab_id: str, accept: bool, *, intent: str = "", intent_category: str | None = None) -> ActionOutcome:
        """Répondre à la boîte de dialogue ouverte. Fermer est toujours permis ; accepter passe par la porte.

        Aucune lecture de page n'a lieu ici : tant qu'une boîte de dialogue est ouverte, la page ne répond à
        aucun appel. Son origine vient de l'adresse de son événement d'ouverture.
        """
        if not isinstance(accept, bool):
            raise ValueError("accept is a boolean")
        context = self._context
        run = self._start("handle_dialog", intent)
        await context.pacer.before_action("handle_dialog")
        tab = context.registry.get(tab_id)
        dialog = tab.dialog
        if dialog is None:
            raise ToolError("unsupported_element", "aucune boîte de dialogue n'est ouverte dans cet onglet")
        if not accept:
            await context.registry.handle_dialog(tab, False)
            return await self._finish(run, tab, mark=None)
        try:
            origin: Origin | None = origin_of_url(tab.dialog_url or "")
        except OriginError:
            origin = None
        profile = context.profile()
        if not origin_allowed(origin, profile.allowed_origins):
            raise ToolError("origin_not_allowed", _ORIGIN_REFUSED, extra_server={"origin": None if origin is None else origin.canonical})
        assert origin is not None
        run.origin = origin.canonical
        message = dialog.get("message", "")
        decision = classify(
            None,
            action_kind="dialog_accept",
            intent_category=intent_category,
            snapshot=context.perception.latest_snapshot(tab.tab_id),
            dialog_message=message,
        )
        await self._guard_dialog(run, tab, profile, origin, dialog, decision)
        if tab.dialog != dialog:
            raise ToolError("element_stale", "la boîte de dialogue a changé pendant la confirmation : rien n'a été accepté")
        await context.registry.handle_dialog(tab, True)
        return await self._finish(run, tab, mark=None)

    # --- preuve demandée par l'opérateur ------------------------------------------------------

    async def save_evidence(self, tab_id: str, label: str) -> ActionOutcome:
        context = self._context
        run = self._start("save_evidence", "")
        tab = context.registry.get(tab_id)
        context.registry.ensure_no_dialog(tab)
        run.origin = self._require_allowed(context.profile(), await self._frame(tab))
        png = await context.perception.capture_masked_png(tab)
        path = context.evidence.manual(run.operation_id, label, png)
        outcome = await self._finish(run, tab, mark=None, settle=False)
        outcome.server["evidence_file"] = path.name
        # Relatif au répertoire du serveur dans le volume : evidence/<jour>/<opération>/<fichier>.
        outcome.server["evidence_path"] = "/".join(path.parts[-4:])
        return outcome

    # --- identité d'un élément ----------------------------------------------------------------

    async def verify_element(self, tab: Tab, snapshot: Snapshot, ref: ElementRef) -> Verified:
        """Le nœud repéré est-il toujours celui de l'instantané ? S'arrête à la première différence.

        Un repère est l'identité (onglet, document, nœud) que l'instantané a enregistrée, jamais un rang dans
        une nouvelle lecture de la page.
        """
        frame = await self._frame(tab)
        if frame.loader_id != snapshot.loader_id:
            raise ToolError("snapshot_stale", _STALE_SNAPSHOT)
        backend = ref.backend_node_id
        try:
            await self._read(tab, "DOM.describeNode", {"backendNodeId": backend})
            object_id = await self._object(tab, backend)
            connected = await self._script(tab, "is_connected", object_id=object_id)
            tree = await self._read(tab, "Accessibility.getPartialAXTree", {"backendNodeId": backend, "fetchRelatives": False})
        except CdpError:
            raise ToolError("element_stale", _STALE_ELEMENT) from None
        if _script_value(connected) is not True:
            raise ToolError("element_stale", _STALE_ELEMENT)
        node = next(
            (item for item in tree.get("nodes", ()) if item.get("backendDOMNodeId") == backend and not item.get("ignored")),
            None,
        )
        if node is None:
            raise ToolError("element_stale", _STALE_ELEMENT)
        role = str((node.get("role") or {}).get("value") or "")
        name = bounded_text((node.get("name") or {}).get("value"), NAME_LIMIT)
        if role != ref.role or name_hash(name) != ref.name_hash:
            raise ToolError("element_stale", _STALE_ELEMENT)
        if frame.origin != snapshot.origin:
            raise ToolError("snapshot_stale", _STALE_SNAPSHOT)
        return Verified(frame=frame, role=role, name=name, object_id=object_id)

    # --- le pipeline --------------------------------------------------------------------------

    def _start(self, tool: str, intent: str) -> _Run:
        return _Run(tool=tool, operation_id=current_operation_id() or new_operation_id(), intent=intent if isinstance(intent, str) else "")

    def _target(self, tab_id: str, snapshot_id: str, mark: int) -> tuple[Tab, Snapshot, ElementRef]:
        registry = self._context.registry
        tab = registry.get(tab_id)
        registry.ensure_no_dialog(tab)
        snapshot = self._context.perception.snapshot(tab_id, snapshot_id)
        ref = snapshot.element(mark) if isinstance(mark, int) and not isinstance(mark, bool) else None
        if ref is None:
            raise ToolError("element_stale", _UNKNOWN_MARK)
        return tab, snapshot, ref

    def _require_allowed(self, profile: Profile, frame: FrameInfo) -> str:
        if frame.origin is None or not origin_allowed(frame.origin, profile.allowed_origins):
            raise ToolError("origin_not_allowed", _ORIGIN_REFUSED, extra_server={"origin": origin_label(frame)})
        return frame.origin.canonical

    async def _guard(
        self,
        run: _Run,
        tab: Tab,
        profile: Profile,
        gesture: str,
        ref: ElementRef,
        verified: Verified,
        decision: GateDecision,
    ) -> None:
        """Journal, preuve et consentement de l'humain, quand la porte ou l'opérateur les demandent."""
        operator_setting = verified.frame.origin in profile.confirm_each_change
        if not decision.armed and not operator_setting:
            return
        target, amounts = await self._form_context(tab, verified.frame, ref)
        request = ConfirmRequest(
            origin=verified.frame.origin.canonical if verified.frame.origin else "",
            tool=run.tool,
            gesture=gesture,
            element_role=verified.role,
            element_name=verified.name,
            form_target_origin=target,
            amounts=amounts,
            categories=decision.categories,
            signals=decision.signals,
            reason="armed" if decision.armed else "operator_setting",
        )
        await self._ask(run, tab, request, red_list=decision.armed)

    async def _guard_dialog(
        self, run: _Run, tab: Tab, profile: Profile, origin: Origin, dialog: dict[str, str], decision: GateDecision
    ) -> None:
        if not decision.armed and origin not in profile.confirm_each_change:
            return
        message = dialog.get("message", "")
        request = ConfirmRequest(
            origin=origin.canonical,
            tool=run.tool,
            gesture="dialog_accept",
            element_role=f"dialog {dialog.get('type', '')}".strip(),
            element_name=message,
            form_target_origin=None,
            amounts=find_amounts(message),
            categories=decision.categories,
            signals=decision.signals,
            reason="armed" if decision.armed else "operator_setting",
        )
        await self._ask(run, tab, request, red_list=decision.armed)

    async def _ask(self, run: _Run, tab: Tab, request: ConfirmRequest, *, red_list: bool) -> None:
        context = self._context
        run.gate = {"red_list": red_list, "confirmation": "requested"}
        # Écrit avant d'agir ; une action armée qui ne peut pas être journalisée ne part pas.
        context.journal.write_required(
            JournalEntry(
                operation_id=run.operation_id,
                tool=run.tool,
                result="requested",
                mutating=True,
                origin=request.origin,
                intent=run.intent,
                red_list=red_list,
                confirmation="requested",
            )
        )
        if red_list:
            # Une action irréversible sans trace qui lui survive n'a aucune valeur d'audit : pas de preuve, pas d'action.
            png = await self._with_gate(run, context.perception.capture_masked_png(tab))
            await self._with_gate(run, _sync(lambda: context.evidence.before(run.operation_id, png)))
        decision = await request_confirmation(context.channel, request)
        if decision is not ConfirmationDecision.GRANTED:
            run.gate = {"red_list": red_list, "confirmation": decision.value}
            raise refusal(decision, extra_server={"gate": dict(run.gate)})
        run.gate = {"red_list": red_list, "confirmation": "granted"}

    async def _form_context(self, tab: Tab, frame: FrameInfo, ref: ElementRef) -> tuple[str | None, tuple[str, ...]]:
        """Où le formulaire de l'élément envoie et les montants qu'il montre, lus maintenant dans le navigateur."""
        raw = await self._read(tab, "DOMSnapshot.captureSnapshot", LIGHT_SNAPSHOT_PARAMS)
        try:
            dom = Dom(raw)
        except ValueError:
            return None, ()
        node = dom.by_backend.get(ref.backend_node_id)
        if node is None:
            return None, ()
        form = dom.enclosing(node, "form")
        scope = form if form is not None else dom.parent(node)
        target = None
        if form is not None:
            origin = form_target_origin(frame.url, form.attributes.get("action"))
            target = origin.canonical if origin is not None else "adresse illisible"
        amounts = find_amounts(dom.text_content(scope, FORM_TEXT_LIMIT)) if scope is not None else ()
        return target, amounts

    async def _finish(
        self, run: _Run, tab: Tab, *, mark: int | None, typed_marker: str | None = None, settle: bool = True
    ) -> ActionOutcome:
        context = self._context
        if settle and not run.dialog_opened:
            started_a_load = await self._settle(run, tab)
            if started_a_load:
                await context.downloads.wait_begin(context.settle_download_start_s)
        if run.gate["red_list"] and run.gate["confirmation"] == "granted":
            try:
                png = await context.perception.capture_masked_png(tab)
                context.evidence.after(run.operation_id, png)
            except ToolError:
                run.notices.append("la capture « après » n'a pas pu être enregistrée")
        run.notices.extend(context.registry.drain_notices())
        run.notices.extend(context.downloads.drain_notices())
        server: dict[str, Any] = {
            "tab_id": tab.tab_id,
            "action": run.tool,
            "notices": run.notices,
            "dialog_open": tab.dialog is not None,
            "counters": context.pacer.counters(),
        }
        if mark is not None:
            server["mark"] = mark
        if run.origin is not None:
            server["origin"] = run.origin
        return ActionOutcome(server=server, typed_marker=typed_marker, gate=dict(run.gate))

    async def _settle(self, run: _Run, tab: Tab) -> bool:
        """Attendre un chargement que le geste a lancé, jamais un réseau calme ; dire si un chargement a commencé."""
        context = self._context
        deadline = time.monotonic() + context.settle_start_s
        while not tab.loading and time.monotonic() < deadline and tab.dialog is None:
            await asyncio.sleep(0.05)
        started = tab.loading
        if started and tab.dialog is None and not tab.closed:
            if not await context.registry.wait_loaded(tab, timeout_s=context.settle_load_s):
                run.notices.append("la page chargée par l'action n'avait pas fini de charger à l'échéance")
        return started

    async def _with_gate(self, run: _Run, awaitable: Any) -> Any:
        """Exécuter une étape après la ligne de journal ; un refus porte l'état de la porte pour la ligne finale."""
        try:
            return await awaitable
        except ToolError as exc:
            exc.extra_server.setdefault("gate", dict(run.gate))
            raise

    # --- appels au navigateur -----------------------------------------------------------------

    async def _frame(self, tab: Tab) -> FrameInfo:
        try:
            return await self._context.registry.main_frame_info(tab, stop_on_dialog=True)
        except DialogBlocking:
            raise dialog_refusal() from None

    async def _read(self, tab: Tab, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return await self._context.registry.read(tab, method, params)
        except DialogBlocking:
            raise dialog_refusal() from None

    async def _script(
        self, tab: Tab, name: str, *, object_id: str | None = None, arguments: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        try:
            return await self._context.registry.run_script(tab, name, object_id=object_id, arguments=arguments)
        except DialogBlocking:
            raise dialog_refusal() from None

    async def _object(self, tab: Tab, backend: int) -> str:
        resolved = await self._read(tab, "DOM.resolveNode", {"backendNodeId": backend})
        object_id = (resolved.get("object") or {}).get("objectId")
        if not isinstance(object_id, str):
            raise CdpError(0, "no object for the node")
        return object_id

    async def _contains(self, tab: Tab, container: int, inner: int) -> bool:
        try:
            container_object = await self._object(tab, container)
            inner_object = await self._object(tab, inner)
            result = await self._script(tab, "contains", object_id=container_object, arguments=[{"objectId": inner_object}])
        except CdpError:
            return False
        return _script_value(result) is True

    async def _focus_into(self, tab: Tab, ref: ElementRef, object_id: str, *, select: bool) -> None:
        """Donner le focus à l'élément et vérifier qu'il y est : les touches vont là où est le focus."""
        try:
            await self._read(tab, "DOM.focus", {"backendNodeId": ref.backend_node_id})
            if select:
                await self._script(tab, "select_text", object_id=object_id)
            active = await self._script(tab, "active_element")
        except CdpError:
            raise ToolError("element_stale", _STALE_ELEMENT) from None
        active_object = (active.get("result") or {}).get("objectId")
        if not isinstance(active_object, str):
            raise ToolError("element_obscured", _FOCUS_MOVED)
        try:
            inside = await self._script(tab, "contains", object_id=object_id, arguments=[{"objectId": active_object}])
        except CdpError:
            raise ToolError("element_obscured", _FOCUS_MOVED) from None
        if _script_value(inside) is not True:
            raise ToolError("element_obscured", _FOCUS_MOVED)

    async def _gesture(
        self, run: _Run, tab: Tab, calls: list[tuple[str, dict[str, Any]]], *, started: bool = False
    ) -> None:
        """Envoyer les événements d'un geste ; une boîte de dialogue qu'il ouvre l'arrête, le geste étant fait.

        ``started`` dit que des événements du même geste ont déjà été envoyés : une boîte de dialogue n'est alors
        jamais un refus, puisque le geste est en cours.
        """
        for number, (method, params) in enumerate(calls):
            try:
                await self._context.registry.read(tab, method, params)
            except DialogBlocking:
                if number == 0 and not started and not run.dialog_opened:
                    raise dialog_refusal() from None
                run.dialog_opened = True
                run.notices.append("une boîte de dialogue s'est ouverte : la traiter (handle_dialog)")
                return

    def _resolve(self, frame: FrameInfo, key: str, name: str | None) -> credentials.Secret:
        if frame.origin is None:
            raise ToolError("credential_not_bound", "la page n'a pas d'origine web : aucun identifiant n'y est saisi")
        return credentials.resolve(self._context.volume, frame.origin, key, name)


def _credential_reference(reference: dict[str, Any]) -> tuple[str, str | None]:
    if not isinstance(reference, dict) or set(reference) - {"key", "name"} or "key" not in reference:
        raise ToolError("credential_invalid", "credential_ref est {\"key\": \"username\" | \"password\", \"name\": facultatif}")
    key = reference["key"]
    name = reference.get("name")
    if key not in credentials.KEYS:
        raise ToolError("credential_invalid", "la clé demandée doit être username ou password")
    if name is not None and not isinstance(name, str):
        raise ToolError("credential_invalid", "le nom d'identifiant demandé n'est pas un nom valide")
    return key, name


def _script_value(result: dict[str, Any]) -> Any:
    return (result.get("result") or {}).get("value")


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


async def _sync(function: Callable[[], Any]) -> Any:
    return function()
