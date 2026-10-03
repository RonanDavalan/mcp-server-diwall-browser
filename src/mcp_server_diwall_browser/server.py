"""Factory of the MCP server: what it is built from, how a tool call goes through it.

Why this module exists:
    The libraries of the server each guard one thing; something has to put
    them together, once, the same way in production and in the tests. Every
    dependency the tests replace is a field of ``Dependencies``, given to the
    one factory: there is no switch, variable or file that turns a check off.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.tools import Tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import guide
from .actions import SETTLE_DOWNLOAD_START_S, SETTLE_LOAD_S, SETTLE_START_S, ActionContext, ActionOutcome, Actions
from .browser import BrowserAttachment, HttpGet, ProcNetReader, TabRegistry, default_http_get, read_proc_net_tcp
from .cdp import CdpConnection, CdpError
from .confirm import TIMEOUT_S, Channel, ElicitationChannel, NativeDialogChannel, choose_channel
from .evidence import DownloadManager, EvidenceWriter
from .gate import Pacer
from .journal import CONFIRMATION_STATES, INTENT_LIMIT, ClientInfo, Journal, JournalEntry
from .mount import MountChecker, Volume
from .operator_profile import Limits, Profile, load_profile
from .output import EXIT_POINT_ATTRIBUTE, ToolError, ToolOutput, ToolResult, bounded_text, tool
from .perception import MaskDrawer, Perception, draw_mask

SERVER_NAME = "mcp-server-diwall-browser"
PACKAGE_NAME = "mcp-server-diwall-browser"

# The fourteen tools, in the order of the operator's table, and whether each changes the page.
TOOLS: tuple[tuple[str, bool], ...] = (
    ("status", False),
    ("list_tabs", False),
    ("open_tab", True),
    ("navigate", True),
    ("close_tab", False),
    ("observe", False),
    ("scroll", False),
    ("wait", False),
    ("click", True),
    ("fill", True),
    ("select_option", True),
    ("press_key", True),
    ("handle_dialog", True),
    ("save_evidence", False),
)

_log = logging.getLogger(__name__)

JournalFactory = Callable[[Volume, Callable[[], ClientInfo | None]], Journal]


@dataclass
class Dependencies:
    """What the server is built from; the tests replace these, and only these."""

    load_profile: Callable[[], Profile]
    mount_checker: MountChecker
    cdp_factory: Callable[[], CdpConnection]
    http_get: HttpGet
    proc_net: ProcNetReader
    # The confirmation window: where its tools are found, and the environment
    # it is shown in (``None``: the server's own).
    which: Callable[[str], str | None]
    environ: Mapping[str, str] | None
    clock: Callable[[], float]
    sleep: Callable[[float], Awaitable[Any]]
    mask_drawer: MaskDrawer
    journal: JournalFactory
    # Delays of the actions and of the confirmation, the decided values by default.
    settle_start_s: float = SETTLE_START_S
    settle_load_s: float = SETTLE_LOAD_S
    settle_download_start_s: float = SETTLE_DOWNLOAD_START_S
    confirmation_timeout_s: float = TIMEOUT_S


def production_dependencies() -> Dependencies:
    """The real ones: the operator's profile, the kernel's tables, the browser, the clock."""
    return Dependencies(
        load_profile=load_profile,
        mount_checker=MountChecker(),
        cdp_factory=CdpConnection,
        http_get=default_http_get,
        proc_net=read_proc_net_tcp,
        which=shutil.which,
        environ=None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        mask_drawer=draw_mask,
        journal=Journal,
    )


@dataclass
class BrowserSession:
    """One connection to the browser and what lives on it; a new connection gets new ones."""

    attachment: BrowserAttachment
    registry: TabRegistry
    perception: Perception
    downloads: DownloadManager
    evidence: EvidenceWriter
    volume: Volume

    @property
    def alive(self) -> bool:
        return not self.attachment.connection.closed


class ServerState:
    """What the server keeps between two calls: the browser session, the journals, the ceilings.

    The profile is read again on every call, as the operator may change it
    without restarting the server.
    """

    def __init__(self, deps: Dependencies) -> None:
        self._deps = deps
        self._session: BrowserSession | None = None
        self._volumes: dict[tuple[str, str | None], Volume] = {}
        self._journals: dict[tuple[str, str | None], Journal] = {}
        self._attach_lock = asyncio.Lock()
        self._not_journaled = 0
        self._warned_not_journaled = False
        self._client_info: ClientInfo | None = None
        self.pacer = Pacer(self._limits, clock=deps.clock, sleep=deps.sleep)

    @property
    def deps(self) -> Dependencies:
        return self._deps

    @property
    def session(self) -> BrowserSession | None:
        return self._session

    def profile(self) -> Profile:
        return self._deps.load_profile()

    def _limits(self) -> Limits:
        # The ceilings hold without a readable profile: a protection that
        # depends on an optional file is missing where nobody wrote it.
        try:
            return self.profile().limits
        except ToolError:
            return Limits()

    @staticmethod
    def _volume_key(profile: Profile) -> tuple[str, str | None]:
        return (str(profile.secrets_dir), None if profile.secrets_crypt_dir is None else str(profile.secrets_crypt_dir))

    def volume(self, profile: Profile) -> Volume:
        key = self._volume_key(profile)
        volume = self._volumes.get(key)
        if volume is None:
            volume = Volume(self._deps.mount_checker, *key)
            self._volumes[key] = volume
        return volume

    def journal(self, profile: Profile) -> Journal:
        key = self._volume_key(profile)
        journal = self._journals.get(key)
        if journal is None:
            journal = self._deps.journal(self.volume(profile), self.client_info)
            self._journals[key] = journal
        return journal

    # --- who the client is -------------------------------------------------------------------

    def client_info(self) -> ClientInfo | None:
        return self._client_info

    def remember_client(self, context: Context[Any, Any, Any] | None) -> None:
        params = _client_params(context)
        info = getattr(params, "clientInfo", None)
        if info is not None:
            self._client_info = (getattr(info, "name", None), getattr(info, "version", None))

    # --- the journal of every call ---------------------------------------------------------------

    @property
    def operations_not_journaled(self) -> int:
        return self._not_journaled + sum(journal.operations_not_journaled for journal in self._journals.values())

    def journal_call(self, name: str, mutating: bool, envelope: dict[str, Any], intent: object) -> None:
        """The final line of a call, written from its result; the line before an armed action is the action's."""
        server = envelope.get("server") or {}
        error = server.get("error") if isinstance(server.get("error"), dict) else None
        gate = server.get("gate") if isinstance(server.get("gate"), dict) else {}
        confirmation = gate.get("confirmation")
        origin = server.get("origin")
        entry = JournalEntry(
            operation_id=str(server.get("operation_id", "")),
            tool=name,
            result="ok" if envelope.get("ok") is True else str((error or {}).get("code", "internal_error")),
            mutating=mutating,
            origin=origin if isinstance(origin, str) else None,
            intent=journal_intent(intent, server.get("typed")),
            red_list=gate.get("red_list") is True,
            confirmation=confirmation if confirmation in CONFIRMATION_STATES else "none",
        )
        try:
            journal = self.journal(self.profile())
        except ToolError as exc:
            # No profile, no volume to write in: counted, said once, never blocking.
            self._not_journaled += 1
            if not self._warned_not_journaled:
                self._warned_not_journaled = True
                _log.warning("audit journal unavailable (%s); operations are not journaled", exc.code)
            return
        journal.write(entry)

    # --- the browser ---------------------------------------------------------------------------

    async def browser(self) -> BrowserSession:
        """The live session, attaching first if there is none or the last one was lost."""
        async with self._attach_lock:
            if self._session is not None and self._session.alive:
                return self._session
            if self._session is not None:
                await self._close_session()
            profile = self.profile()
            attachment = await BrowserAttachment.attach(
                profile,
                cdp_factory=self._deps.cdp_factory,
                http_get=self._deps.http_get,
                proc_net=self._deps.proc_net,
            )
            volume = self.volume(profile)
            downloads = DownloadManager(volume, attachment.connection)
            # Until a gesture opens it, the download setting is closed: a page
            # download between the attachment and the first action would go
            # wherever the browser's own settings say, outside the volume.
            await downloads.forbid()
            registry = TabRegistry(attachment.connection)
            perception = Perception(
                registry,
                allowed_origins=lambda: self.profile().allowed_origins,
                mask_drawer=self._deps.mask_drawer,
            )
            self._session = BrowserSession(
                attachment=attachment,
                registry=registry,
                perception=perception,
                downloads=downloads,
                evidence=EvidenceWriter(volume),
                volume=volume,
            )
            return self._session

    async def close(self) -> None:
        """When the server stops: downloads back to the browser's own setting, connection closed.

        The tabs the server opened stay open, and the browser keeps running.
        """
        async with self._attach_lock:
            await self._close_session()

    async def _close_session(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        if session.alive:
            try:
                await session.downloads.restore_default()
            except (ToolError, CdpError) as exc:
                _log.warning("the download setting could not be restored (%s)", type(exc).__name__)
        await session.attachment.close()


def journal_intent(intent: object, typed: object) -> str:
    """The model's intent, followed by what was typed, as a marker: never the value itself."""
    text = intent if isinstance(intent, str) else ""
    if not isinstance(typed, str) or not typed:
        return text[:INTENT_LIMIT]
    suffix = f" [input: {typed}]"
    return text[: max(INTENT_LIMIT - len(suffix), 0)] + suffix


def _client_params(context: Context[Any, Any, Any] | None) -> Any:
    try:
        return context.request_context.session.client_params  # type: ignore[union-attr]
    except (AttributeError, LookupError, ValueError):
        return None


class _Approval(BaseModel):
    """What the client's own confirmation asks: one yes or no."""

    approve: bool = Field(description="True to let this one action go ahead.")


class ToolContext:
    """What a tool reaches during one call: the server's state and this call's client."""

    def __init__(self, state: ServerState, mcp: Context[Any, Any, Any] | None) -> None:
        self._state = state
        self._mcp = mcp

    @property
    def state(self) -> ServerState:
        return self._state

    @property
    def pacer(self) -> Pacer:
        return self._state.pacer

    def profile(self) -> Profile:
        return self._state.profile()

    async def browser(self) -> BrowserSession:
        return await self._state.browser()

    async def actions(self) -> Actions:
        profile = self.profile()
        session = await self.browser()
        deps = self._state.deps
        return Actions(
            ActionContext(
                profile=self._state.profile,
                registry=session.registry,
                perception=session.perception,
                pacer=self._state.pacer,
                channel=self.channel(profile),
                journal=self._state.journal(profile),
                volume=session.volume,
                evidence=session.evidence,
                downloads=session.downloads,
                settle_start_s=deps.settle_start_s,
                settle_load_s=deps.settle_load_s,
                settle_download_start_s=deps.settle_download_start_s,
            )
        )

    def channel(self, profile: Profile) -> Channel:
        """Where a confirmation would be asked for this call, chosen as decided by the confirmation module."""
        deps = self._state.deps
        if deps.environ is None:
            native = NativeDialogChannel(which=deps.which, timeout_s=deps.confirmation_timeout_s)
        else:
            native = NativeDialogChannel(which=deps.which, environ=deps.environ, timeout_s=deps.confirmation_timeout_s)
        elicitation = ElicitationChannel(self._elicit, announced=self.client_elicits(), timeout_s=deps.confirmation_timeout_s)
        return choose_channel(profile, native, elicitation)

    def channel_name(self, profile: Profile) -> str:
        channel = self.channel(profile)
        if isinstance(channel, NativeDialogChannel):
            return "native"
        if isinstance(channel, ElicitationChannel):
            return "elicitation"
        return "none"

    def client_elicits(self) -> bool:
        """Whether the client said, when the session opened, that it can ask its user."""
        try:
            session = self._mcp.request_context.session  # type: ignore[union-attr]
        except (AttributeError, LookupError, ValueError):
            return False
        wanted = types.ClientCapabilities(elicitation=types.ElicitationCapability())
        return bool(session.check_client_capability(wanted))

    async def _elicit(self, text: str) -> bool | None:
        if self._mcp is None:
            return None
        result = await self._mcp.elicit(text, _Approval)
        # Consent is an accepted answer that says yes; anything else is no.
        return result.action == "accept" and getattr(result.data, "approve", False) is True

    @staticmethod
    def outcome(outcome: ActionOutcome) -> ToolOutput:
        """What an action reports, as it reports it: its notices are passed on untouched."""
        server = {**outcome.server, "gate": dict(outcome.gate)}
        if outcome.typed_marker:
            server["typed"] = outcome.typed_marker
        return ToolOutput(server=server)


class ToolArguments(BaseModel):
    """Arguments of a tool: an unknown parameter is refused, never ignored; no parameter approves anything."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def intent_field() -> Any:
    """The ``intent`` parameter shared by the tools that change a page."""
    return Field(default="", max_length=INTENT_LIMIT, description="Why you act, in a few words; kept in the journal.")


# --- registration ------------------------------------------------------------------------------


class _ServerTool(Tool):
    """A tool of this server: arguments checked here, result through the single exit point."""

    entry: Any = Field(default=None, exclude=True)
    refusal: Any = Field(default=None, exclude=True)
    arguments_model: Any = Field(default=None, exclude=True)
    mutating: bool = Field(default=False, exclude=True)
    state: Any = Field(default=None, exclude=True)

    async def run(
        self,
        arguments: dict[str, Any],
        context: Context[Any, Any, Any] | None = None,
        convert_result: bool = False,
    ) -> types.CallToolResult:
        state: ServerState = self.state
        state.remember_client(context)
        values: dict[str, Any] = {}
        try:
            parsed = self.arguments_model.model_validate(arguments if isinstance(arguments, dict) else {})
        except ValidationError as exc:
            envelope, image = await self.refusal(_argument_problems(exc))
        else:
            values = parsed.model_dump()
            envelope, image = await self.entry(ToolContext(state, context), **values)
        state.journal_call(self.name, self.mutating, envelope, values.get("intent", ""))
        return call_result(envelope, image)


async def _no_arguments(ctx: Context[Any, Any, Any]) -> None:
    """Gives the registered tool its place for the call's context."""


def _argument_problems(exc: ValidationError) -> list[str]:
    problems = []
    for error in exc.errors()[:10]:
        location = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        kind = "inconnu" if error.get("type") == "extra_forbidden" else "invalide"
        problems.append(f"{bounded_text(location, 64)} ({kind})")
    return problems


def _argument_refusal(name: str) -> Callable[..., Awaitable[ToolResult]]:
    @tool(name)
    async def refuse(problems: list[str]) -> ToolOutput:
        raise ToolError(
            "invalid_arguments",
            "paramètres refusés, rien n'a été fait : " + ", ".join(problems),
        )

    return refuse


def call_result(envelope: dict[str, Any], image: bytes | None) -> types.CallToolResult:
    """The envelope as the first content, the image as the second; marked as an error when ok is false."""
    content: list[types.TextContent | types.ImageContent] = [
        types.TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))
    ]
    if image is not None:
        content.append(types.ImageContent(type="image", data=base64.b64encode(image).decode("ascii"), mimeType="image/png"))
    return types.CallToolResult(content=content, isError=envelope.get("ok") is not True)


def server_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "unknown"


def tool_modules() -> dict[str, Any]:
    """The module of each tool; imported here, when the server is built, as the tools use this module."""
    from .tools import (
        click,
        close_tab,
        fill,
        handle_dialog,
        list_tabs,
        navigate,
        observe,
        open_tab,
        press_key,
        save_evidence,
        scroll,
        select_option,
        status,
        wait,
    )

    modules = {
        "status": status,
        "list_tabs": list_tabs,
        "open_tab": open_tab,
        "navigate": navigate,
        "close_tab": close_tab,
        "observe": observe,
        "scroll": scroll,
        "wait": wait,
        "click": click,
        "fill": fill,
        "select_option": select_option,
        "press_key": press_key,
        "handle_dialog": handle_dialog,
        "save_evidence": save_evidence,
    }
    return {name: modules[name] for name, _mutating in TOOLS}


def registered_tool(state: ServerState, name: str, mutating: bool, module: Any) -> _ServerTool:
    entry = module.run
    if getattr(entry, EXIT_POINT_ATTRIBUTE, None) != name:
        # A tool that does not go through the single exit point is not registered at all.
        raise TypeError(f"tool {name} does not go through the single exit point")
    arguments_model = module.Arguments
    description = inspect.cleandoc(entry.__doc__ or "")
    base = _ServerTool.from_function(_no_arguments, name=name, description=description, structured_output=False)
    return base.model_copy(
        update={
            "parameters": arguments_model.model_json_schema(by_alias=True),
            "entry": entry,
            "refusal": _argument_refusal(name),
            "arguments_model": arguments_model,
            "mutating": mutating,
            "state": state,
            "annotations": types.ToolAnnotations(readOnlyHint=not mutating),
        }
    )


def create_server(deps: Dependencies) -> FastMCP:
    """The one factory: production calls it with the real dependencies, the tests with theirs."""
    state = ServerState(deps)
    modules = tool_modules()

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await state.close()

    return FastMCP(
        name=SERVER_NAME,
        instructions=guide.INSTRUCTIONS,
        tools=[registered_tool(state, name, mutating, modules[name]) for name, mutating in TOOLS],
        lifespan=lifespan,
    )
