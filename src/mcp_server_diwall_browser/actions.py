"""Act in an owned tab: click, fill, choose an option, press a key, scroll, answer a dialog.

Why this module exists:
    An action is where an error or an injection becomes real: money spent,
    data deleted, a secret typed on the wrong site. Every action goes through
    one pipeline that checks the element is still the one the model saw,
    asks the gate, writes the journal and keeps evidence before an armed
    action, and lets it leave only with a human's consent given in a window
    the model cannot reach.
"""

from __future__ import annotations

import asyncio
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
# Key -> (code, Windows virtual key code). Only keys that move or validate:
# no shortcut, no character typed through this path.
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
# A load the gesture started may turn into a download; one begins this long, at
# most, after the load ends. A download that begins later is refused.
SETTLE_DOWNLOAD_START_S = 0.5
FORM_TEXT_LIMIT = 4000

_STALE_SNAPSHOT = "la page a changé depuis l'observation : observer à nouveau avant d'agir"
_STALE_ELEMENT = "cet élément n'est plus celui de l'observation : observer à nouveau avant d'agir"
_UNKNOWN_MARK = "ce repère n'existe pas dans l'instantané : observer à nouveau avant d'agir"
_OBSCURED = "un autre élément recouvre la cible à cet endroit : rien n'a été cliqué"
_FOCUS_MOVED = "le curseur n'est pas dans l'élément visé : rien n'a été saisi"
_ORIGIN_REFUSED = "cette page n'est pas sur un site autorisé par le profil : aucune action n'y est faite"
_SENSITIVE = (
    "ce champ est sensible : il ne se remplit que par un identifiant du répertoire chiffré (credential_ref), "
    "jamais par un texte"
)


@dataclass(frozen=True)
class ActionOutcome:
    """What an action reports; ``gate`` feeds the final journal line written by the tool."""

    server: dict[str, Any]
    typed_marker: str | None
    gate: dict[str, Any]


@dataclass
class ActionContext:
    """Everything an action needs, given by the caller; nothing is global."""

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
    """What the browser says now about a marked element, after the identity check."""

    frame: FrameInfo
    role: str
    name: str
    object_id: str


@dataclass
class _Run:
    """The state of one action as it goes through the pipeline."""

    tool: str
    operation_id: str
    intent: str
    notices: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=lambda: {"red_list": False, "confirmation": "none"})
    dialog_opened: bool = False


class Actions:
    def __init__(self, context: ActionContext) -> None:
        self._context = context

    # --- clicking ----------------------------------------------------------------------

    async def click(
        self, tab_id: str, snapshot_id: str, mark: int, *, intent: str = "", intent_category: str | None = None
    ) -> ActionOutcome:
        context = self._context
        run = self._start("click", intent)
        await context.pacer.before_action("click")
        tab, snapshot, ref = self._target(tab_id, snapshot_id, mark)
        profile = context.profile()
        verified = await self.verify_element(tab, snapshot, ref)
        self._require_allowed(profile, verified.frame)
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
        quads = (await self._read(tab, "DOM.getContentQuads", {"backendNodeId": backend})).get("quads") or []
        if not quads or len(quads[0]) != 8:
            raise ToolError("element_obscured", _OBSCURED)
        xs, ys = quads[0][0::2], quads[0][1::2]
        x, y = round(sum(xs) / 4), round(sum(ys) / 4)
        hit = await self._read(tab, "DOM.getNodeForLocation", {"x": x, "y": y, "includeUserAgentShadowDOM": False})
        hit_id = hit.get("backendNodeId")
        if hit_id != backend:
            # The click lands on what the human sees there, never on what lies under it.
            if not isinstance(hit_id, int) or not await self._contains(tab, backend, hit_id):
                raise ToolError("element_obscured", _OBSCURED)
        await self._gesture(
            run,
            tab,
            [
                ("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "clickCount": 0}),
                ("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}),
                ("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}),
            ],
        )

    # --- filling and choosing ---------------------------------------------------------------

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
        self._require_allowed(profile, verified.frame)
        if ref.tag not in ("input", "textarea") or (ref.tag == "input" and (ref.input_type or "") not in FILL_INPUT_TYPES):
            raise ToolError("unsupported_element", "cet élément ne se remplit pas : un champ de texte ou une zone de texte est attendu")
        if ref.sensitive and text is not None:
            raise ToolError("sensitive_value_refused", _SENSITIVE)
        key = name = None
        if credential_ref is not None:
            key, name = _credential_reference(credential_ref)
            # Refused before any confirmation: consent does not make a wrong site right.
            self._resolve(await self._frame(tab), key, name)
        await self._guard(run, tab, profile, "fill", ref, verified, NOT_ARMED)
        if run.gate["confirmation"] == "granted":
            verified = await self.verify_element(tab, snapshot, ref)
        await self._with_gate(run, self._focus_into(tab, ref, verified.object_id, select=clear))
        if key is not None:
            # The origin read in the browser at the moment of typing, not the
            # snapshot's nor the one the model names.
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
        self._require_allowed(profile, verified.frame)
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

    # --- keys -----------------------------------------------------------------------------------

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
            self._require_allowed(profile, await self._frame(tab))
            await self._with_gate(run, self._key(run, tab, key))
            return await self._finish(run, tab, mark=None)
        if snapshot_id is None or mark is None:
            raise ToolError("unsupported_element", "la touche Entrée exige un repère (snapshot_id et mark)")
        tab, snapshot, ref = self._target(tab_id, snapshot_id, mark)
        verified = await self.verify_element(tab, snapshot, ref)
        self._require_allowed(profile, verified.frame)
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
            # Without the character, the browser does not submit the form.
            down.update(text="\r", unmodifiedText="\r")
        up = {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": virtual}
        await self._gesture(run, tab, [("Input.dispatchKeyEvent", down), ("Input.dispatchKeyEvent", up)])

    # --- scrolling ---------------------------------------------------------------------------------

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

    async def _viewport(self, tab: Tab) -> tuple[float, float]:
        visual = (await self._read(tab, "Page.getLayoutMetrics")).get("cssVisualViewport", {})
        return float(visual.get("pageX", 0)), float(visual.get("pageY", 0))

    # --- dialogs ---------------------------------------------------------------------------------

    async def handle_dialog(self, tab_id: str, accept: bool, *, intent: str = "", intent_category: str | None = None) -> ActionOutcome:
        """Answer the open dialog. Dismissing is always allowed; accepting goes through the gate.

        No page read happens here: while a dialog is open the page answers
        no call. Its origin comes from the address of its opening event.
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

    # --- evidence the operator asks for ---------------------------------------------------------------

    async def save_evidence(self, tab_id: str, label: str) -> ActionOutcome:
        context = self._context
        run = self._start("save_evidence", "")
        tab = context.registry.get(tab_id)
        context.registry.ensure_no_dialog(tab)
        self._require_allowed(context.profile(), await self._frame(tab))
        png = await context.perception.capture_masked_png(tab)
        path = context.evidence.manual(run.operation_id, label, png)
        outcome = await self._finish(run, tab, mark=None, settle=False)
        outcome.server["evidence_file"] = path.name
        return outcome

    # --- identity of an element ------------------------------------------------------------------------

    async def verify_element(self, tab: Tab, snapshot: Snapshot, ref: ElementRef) -> Verified:
        """Is the marked node still the one of the snapshot? Stops at the first difference.

        A mark is the identity (tab, document, node) the snapshot recorded,
        never a rank in a new reading of the page.
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

    # --- the pipeline ------------------------------------------------------------------------------

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

    def _require_allowed(self, profile: Profile, frame: FrameInfo) -> None:
        if not origin_allowed(frame.origin, profile.allowed_origins):
            raise ToolError("origin_not_allowed", _ORIGIN_REFUSED, extra_server={"origin": origin_label(frame)})

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
        """Journal, evidence and the human's consent, when the gate or the operator asks for them."""
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
        # Written before acting; an armed action that cannot be journaled does not leave.
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
            # An irreversible action without a trace that survives it has no
            # audit value: no evidence, no action.
            png = await self._with_gate(run, context.perception.capture_masked_png(tab))
            await self._with_gate(run, _sync(lambda: context.evidence.before(run.operation_id, png)))
        decision = await request_confirmation(context.channel, request)
        if decision is not ConfirmationDecision.GRANTED:
            run.gate = {"red_list": red_list, "confirmation": decision.value}
            raise refusal(decision, extra_server={"gate": dict(run.gate)})
        run.gate = {"red_list": red_list, "confirmation": "granted"}

    async def _form_context(self, tab: Tab, frame: FrameInfo, ref: ElementRef) -> tuple[str | None, tuple[str, ...]]:
        """Where the element's form sends and the amounts it shows, read now in the browser."""
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
        return ActionOutcome(server=server, typed_marker=typed_marker, gate=dict(run.gate))

    async def _settle(self, run: _Run, tab: Tab) -> bool:
        """Wait for a load the gesture started, never for a quiet network; say whether one started."""
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
        """Run a step after the journal line; a refusal carries the gate state for the final line."""
        try:
            return await awaitable
        except ToolError as exc:
            exc.extra_server.setdefault("gate", dict(run.gate))
            raise

    # --- browser calls -----------------------------------------------------------------------------

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
        """Give the focus to the element and check it is there: keys go wherever the focus is."""
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

    async def _gesture(self, run: _Run, tab: Tab, calls: list[tuple[str, dict[str, Any]]]) -> None:
        """Send the events of a gesture; a dialog it opens stops it, the gesture being done."""
        for number, (method, params) in enumerate(calls):
            try:
                await self._context.registry.read(tab, method, params)
            except DialogBlocking:
                if number == 0 and not run.dialog_opened:
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


async def _sync(function: Callable[[], Any]) -> Any:
    return function()
