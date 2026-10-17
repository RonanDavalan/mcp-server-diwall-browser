"""Le seul module qui parle au navigateur, par le protocole DevTools.

Pourquoi ce module existe :
    La connexion de débogage donne le plein contrôle du navigateur de l'opérateur, tous les onglets
    compris. Chaque appel que fait le projet passe par une seule méthode qui le contrôle contre une
    liste fixée dans le code avant qu'un octet soit envoyé, ne journalise jamais les paramètres (un
    texte saisi peut être un secret), et n'exécute des scripts de page que depuis une table fixe, pour
    qu'aucun texte venu du modèle ou d'une page ne devienne jamais du code.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .origin import OriginError, is_blank, origin_of_url
from .output import ToolError

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_S = 30.0
OPEN_TIMEOUT_S = 10.0

# Toute méthode que le serveur peut envoyer. Une méthode absente ici ne peut pas être appelée, quel que
# soit l'appelant ; en ajouter une est un changement relu de ce fichier.
ALLOWED_METHODS = frozenset(
    {
        # Rattachement et possession des onglets (browser.py).
        "Browser.getVersion",
        "Target.setDiscoverTargets",
        "Target.createTarget",
        "Target.attachToTarget",
        "Target.closeTarget",
        "Page.enable",
        # Sans elle, le navigateur n'envoie jamais Inspector.targetCrashed, et un onglet planté resterait
        # listé comme utilisable.
        "Inspector.enable",
        "Page.navigate",
        "Page.getFrameTree",
        "Page.handleJavaScriptDialog",
        # Perception : les lectures dont les tests du navigateur figent les résultats.
        "Accessibility.getFullAXTree",
        "DOMSnapshot.captureSnapshot",
        "Page.getLayoutMetrics",
        "Page.captureScreenshot",
        "DOM.getNodeForLocation",
        "DOM.describeNode",
        # Saisie dans un champ.
        "DOM.focus",
        "Input.insertText",
        "Input.dispatchKeyEvent",
        # Actions : identité d'un élément, où il se trouve, le geste lui-même, et où vont les téléchargements.
        "Accessibility.getPartialAXTree",
        # L'objet de page d'un nœud : ce sur quoi s'exécute un script constant de la table.
        "DOM.resolveNode",
        "DOM.getContentQuads",
        "DOM.scrollIntoViewIfNeeded",
        "Input.dispatchMouseEvent",
        "Browser.setDownloadBehavior",
        # Scripts de page : seulement par call_internal, depuis INTERNAL_SCRIPTS.
        "Runtime.evaluate",
        "Runtime.callFunctionOn",
    }
)

ScriptKind = Literal["evaluate", "call_function_on"]

# Méthode qui exécute un script, sorte de script qu'elle prend, paramètre qui porte le texte du script,
# et chaque paramètre que call_internal peut poser.
_SCRIPT_METHODS: dict[str, tuple[str, str, frozenset[str]]] = {
    "Runtime.evaluate": ("evaluate", "expression", frozenset({"expression", "returnByValue"})),
    "Runtime.callFunctionOn": (
        "call_function_on",
        "functionDeclaration",
        frozenset({"functionDeclaration", "objectId", "arguments", "returnByValue"}),
    ),
}

# Méthodes qui chargent une adresse : une adresse javascript: est aussi un script.
_ADDRESS_METHODS = frozenset({"Page.navigate", "Target.createTarget"})

_UNREACHABLE_MESSAGE = "le navigateur ne répond pas : vérifier qu'il est lancé avec le port de débogage du profil"
_TIMEOUT_MESSAGE = "le navigateur n'a pas répondu dans le délai"

_log = logging.getLogger(__name__)

# La bibliothèque websockets journalise chaque trame, paramètres compris, au niveau DEBUG. Elle écrit
# sur ce journal, tenu au-dessus de DEBUG quel que soit le niveau de la racine.
_frame_log = logging.getLogger(__name__ + ".frames")
_frame_log.setLevel(logging.WARNING)


@dataclass(frozen=True)
class InternalScript:
    """Un script de page écrit dans cette base de code ; jamais construit à partir de données."""

    kind: ScriptKind
    text: str
    # Faux quand le résultat est un objet auquel l'appel suivant se réfère (un élément), vrai quand c'est une valeur simple.
    by_value: bool = True

    def __post_init__(self) -> None:
        if self.kind not in ("evaluate", "call_function_on"):
            raise ValueError("an internal script is evaluate or call_function_on")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("an internal script has a non-empty text")


# Nom -> script. En lecture seule à l'exécution ; on ajoute des scripts en modifiant ce littéral, et
# chaque texte est une chaîne simple sans valeur formatée dedans.
INTERNAL_SCRIPTS: Mapping[str, InternalScript] = MappingProxyType(
    {
        "contains": InternalScript(
            "call_function_on",
            "function(other) { return this === other || this.contains(other); }",
        ),
        "select_text": InternalScript(
            "call_function_on",
            "function() { if (typeof this.select === 'function') { this.select(); return true; } "
            "if (this.isContentEditable) { const range = this.ownerDocument.createRange(); range.selectNodeContents(this); "
            "const selection = this.ownerDocument.defaultView.getSelection(); selection.removeAllRanges(); "
            "selection.addRange(range); return true; } return false; }",
        ),
        "set_select_option": InternalScript(
            "call_function_on",
            "function(wanted) { const options = Array.from(this.options || []); "
            "const index = options.findIndex(o => o.text.trim() === wanted || o.value === wanted); "
            "if (index < 0) { return false; } this.selectedIndex = index; "
            "this.dispatchEvent(new Event('input', { bubbles: true })); "
            "this.dispatchEvent(new Event('change', { bubbles: true })); return true; }",
        ),
        "active_element": InternalScript("evaluate", "document.activeElement", by_value=False),
        "is_connected": InternalScript("call_function_on", "function() { return this.isConnected; }"),
    }
)


class CdpError(Exception):
    """Le navigateur a répondu à un appel par une erreur de protocole."""

    def __init__(self, code: int, message: str) -> None:
        # Seul le code entre dans le texte de l'exception ; le message reste un attribut pour qu'afficher
        # l'exception ne répète rien.
        super().__init__(code)
        self.code = code
        self.message = message


class DisallowedMethodError(Exception):
    """Un appel que ce module refuse d'envoyer : une erreur de programmation, jamais une erreur d'outil."""


class UnknownScriptError(DisallowedMethodError):
    """call_internal a reçu un nom qui n'est pas dans la table des scripts."""


Event = tuple[str, dict[str, Any], str | None]
EventHandler = Callable[[str, dict[str, Any], str | None], Awaitable[None] | None]
EventPredicate = Callable[[str, dict[str, Any], str | None], bool]


def _unreachable(message: str = _UNREACHABLE_MESSAGE) -> ToolError:
    return ToolError("browser_unreachable", message)


class EventWaiter:
    """Attend le premier événement qui satisfait un prédicat.

    Il écoute dès sa création, si bien qu'un appelant peut commencer à attendre avant d'envoyer l'appel qui
    déclenche l'événement. L'attendre rend ``(méthode, paramètres, session_id)`` ; il lève ``TimeoutError`` à
    l'échéance et ``browser_unreachable`` si la connexion se ferme.
    """

    def __init__(self, connection: CdpConnection, predicate: EventPredicate, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        self._connection = connection
        self._predicate = predicate
        self._deadline = loop.time() + timeout
        self._future: asyncio.Future[Event] = loop.create_future()
        # Un échec que personne n'attend ne doit pas être signalé comme jamais récupéré.
        self._future.add_done_callback(lambda f: f.cancelled() or f.exception())

    def _offer(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if self._future.done():
            return
        try:
            matched = self._predicate(method, params, session_id)
        except Exception as exc:  # noqa: BLE001 - un prédicat défectueux ne doit pas arrêter le lecteur
            _log.debug("event predicate failed with %s", type(exc).__name__)
            return
        if matched:
            self._future.set_result((method, params, session_id))

    def _fail(self) -> None:
        if not self._future.done():
            self._future.set_exception(_unreachable())

    def cancel(self) -> None:
        self._connection._waiters.discard(self)
        self._future.cancel()

    def __await__(self) -> Generator[Any, None, Event]:
        return self._wait().__await__()

    async def _wait(self) -> Event:
        try:
            async with asyncio.timeout_at(self._deadline):
                return await self._future
        finally:
            self._connection._waiters.discard(self)


class CdpConnection:
    """Une connexion WebSocket au navigateur ; chaque appel passe par ``send``."""

    def __init__(self, *, internal_scripts: Mapping[str, InternalScript] | None = None) -> None:
        # La table peut être remplacée ici, et seulement ici, pour que les tests exécutent leurs propres scripts ;
        # rien dans la configuration ne l'atteint.
        table = INTERNAL_SCRIPTS if internal_scripts is None else internal_scripts
        for name, script in table.items():
            if not isinstance(name, str) or not isinstance(script, InternalScript):
                raise TypeError("the script table maps names to InternalScript values")
        self._scripts: Mapping[str, InternalScript] = MappingProxyType(dict(table))
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._handlers: list[EventHandler] = []
        self._waiters: set[EventWaiter] = set()
        self._tasks: set[asyncio.Future[Any]] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._ws is None or self._closed

    async def connect(self, ws_url: str) -> None:
        if self._ws is not None:
            raise RuntimeError("the connection is already open")
        try:
            # proxy=None : la bibliothèque passerait sinon par le mandataire nommé dans l'environnement, et
            # livrerait ainsi le navigateur.
            self._ws = await connect(
                ws_url,
                proxy=None,
                max_size=MAX_MESSAGE_BYTES,
                compression=None,
                ping_interval=None,
                open_timeout=OPEN_TIMEOUT_S,
                logger=_frame_log,
            )
        except (OSError, TimeoutError, WebSocketException) as exc:
            _log.debug("cdp connection failed with %s", type(exc).__name__)
            raise _unreachable() from None
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        """Fermer cette connexion ; le navigateur lui-même reste en marche."""
        self._closed = True
        ws = self._ws
        if ws is not None:
            await ws.close()
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
        for task in list(self._tasks):
            task.cancel()
        self._fail_all()

    def add_event_handler(self, callback: EventHandler) -> None:
        """Appeler ``callback(method, params, session_id)`` pour chaque événement.

        Une fonction de rappel s'exécute dans le lecteur et doit rendre vite ; si elle rend un objet attendable,
        cet objet s'exécute comme une tâche séparée, de sorte qu'elle peut elle-même appeler ``send``.
        """
        self._handlers.append(callback)

    def remove_event_handler(self, callback: EventHandler) -> None:
        self._handlers.remove(callback)

    def wait_event(self, predicate: EventPredicate, *, timeout: float) -> EventWaiter:
        waiter = EventWaiter(self, predicate, timeout)
        if self.closed:
            waiter._fail()
        else:
            self._waiters.add(waiter)
        return waiter

    async def send(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        _script: InternalScript | None = None,
    ) -> dict[str, Any]:
        """Envoyer un appel et rendre son ``result``.

        ``_script`` est privé à ``call_internal`` : une méthode de script est refusée sans lui, et son texte doit
        être celui de la table.
        """
        payload = dict(params) if params is not None else {}
        self._check_call(method, payload, _script)
        return await self._exchange(method, payload, session_id, timeout)

    async def attach(self, target_id: str) -> str:
        """Ouvrir une session à plat sur une cible et rendre son identifiant."""
        result = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise CdpError(0, "no session identifier in the answer")
        return session_id

    async def call_internal(
        self,
        name: str,
        *,
        session_id: str,
        object_id: str | None = None,
        arguments: Sequence[Mapping[str, Any]] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Exécuter le script ``name`` de la table ; les valeurs voyagent comme arguments de protocole, jamais dans le texte."""
        script = self._scripts.get(name) if isinstance(name, str) else None
        if script is None:
            raise UnknownScriptError(f"unknown internal script: {name!r}")
        if script.kind == "evaluate":
            if object_id is not None or arguments:
                raise DisallowedMethodError("an evaluated script takes no object and no arguments")
            params: dict[str, Any] = {"expression": script.text, "returnByValue": script.by_value}
            method = "Runtime.evaluate"
        else:
            if not isinstance(object_id, str) or not object_id:
                raise DisallowedMethodError("a function script runs on an object")
            params = {
                "functionDeclaration": script.text,
                "objectId": object_id,
                "arguments": _protocol_arguments(arguments),
                "returnByValue": script.by_value,
            }
            method = "Runtime.callFunctionOn"
        return await self.send(method, params, session_id=session_id, timeout=timeout, _script=script)

    # --- contrôles ------------------------------------------------------------------

    def _check_call(self, method: str, params: dict[str, Any], script: InternalScript | None) -> None:
        if not isinstance(method, str) or method not in ALLOWED_METHODS:
            raise DisallowedMethodError(f"method not allowed: {method!r}")
        script_method = _SCRIPT_METHODS.get(method)
        if script_method is None:
            if script is not None:
                raise DisallowedMethodError("an internal script goes with a script method only")
            if method in _ADDRESS_METHODS:
                _check_address(method, params.get("url"))
            return
        kind, text_field, allowed_params = script_method
        if script is None:
            raise DisallowedMethodError(f"{method} is only sent through call_internal")
        if not any(script is known for known in self._scripts.values()):
            raise DisallowedMethodError("the script is not in the table")
        if script.kind != kind:
            raise DisallowedMethodError(f"a {script.kind} script cannot be sent with {method}")
        text = params.get(text_field)
        if not isinstance(text, str) or text != script.text:
            raise DisallowedMethodError("the script text is not exactly the table's text")
        if not set(params) <= allowed_params:
            raise DisallowedMethodError(f"unexpected parameter for {method}")

    # --- transport ------------------------------------------------------------------

    async def _exchange(
        self, method: str, params: dict[str, Any], session_id: str | None, timeout: float
    ) -> dict[str, Any]:
        ws = self._ws
        if ws is None or self._closed:
            raise _unreachable()
        message_id = next(self._ids)
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params}
        if session_id is not None:
            message["sessionId"] = session_id
        text = json.dumps(message, allow_nan=False)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        _log.debug("cdp call %s #%d", method, message_id)
        try:
            await ws.send(text)
            response = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            _log.debug("cdp call %s #%d timed out", method, message_id)
            raise _unreachable(_TIMEOUT_MESSAGE) from None
        except (ConnectionClosed, OSError) as exc:
            _log.debug("cdp call %s #%d lost the connection (%s)", method, message_id, type(exc).__name__)
            raise _unreachable() from None
        finally:
            self._pending.pop(message_id, None)
        error = response.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            text_message = error.get("message") if isinstance(error, dict) else None
            code = code if isinstance(code, int) and not isinstance(code, bool) else 0
            _log.debug("cdp call %s #%d failed with code %d", method, message_id, code)
            raise CdpError(code, text_message if isinstance(text_message, str) else "")
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    async def _read_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for raw in ws:
                self._dispatch(raw)
        except ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001 - tout ce qui arrête le lecteur ferme la connexion
            _log.debug("cdp reader stopped with %s", type(exc).__name__)
        finally:
            self._closed = True
            self._fail_all()

    def _dispatch(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            _log.debug("cdp message is not JSON")
            return
        if not isinstance(message, dict):
            return
        if "id" in message:
            message_id = message["id"]
            future = self._pending.get(message_id) if isinstance(message_id, int) else None
            if future is not None and not future.done():
                future.set_result(message)
            return
        method = message.get("method")
        params = message.get("params", {})
        session_id = message.get("sessionId")
        if not isinstance(method, str) or (session_id is not None and not isinstance(session_id, str)):
            return
        if not isinstance(params, dict):
            params = {}
        _log.debug("cdp event %s", method)
        for waiter in list(self._waiters):
            waiter._offer(method, params, session_id)
        for handler in list(self._handlers):
            try:
                outcome = handler(method, params, session_id)
            except Exception as exc:  # noqa: BLE001 - un gestionnaire ne doit pas affamer les autres
                _log.warning("cdp event handler failed with %s", type(exc).__name__)
                continue
            if inspect.isawaitable(outcome):
                task = asyncio.ensure_future(outcome)
                self._tasks.add(task)
                task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Future[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _log.warning("cdp event task failed with %s", type(task.exception()).__name__)

    def _fail_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(_unreachable())
        for waiter in list(self._waiters):
            waiter._fail()
        self._waiters.clear()


def _check_address(method: str, url: object) -> None:
    # Le module des onglets les refuse d'abord, avec une erreur d'outil ; ceci est la dernière ligne, pour un
    # appelant qui l'aurait oublié.
    if isinstance(url, str) and is_blank(url):
        return
    try:
        origin_of_url(url)  # type: ignore[arg-type]
    except OriginError:
        raise DisallowedMethodError(f"{method} only loads http, https or about:blank addresses") from None


def _protocol_arguments(arguments: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for argument in arguments or ():
        if not isinstance(argument, Mapping) or len(argument) != 1:
            raise DisallowedMethodError("each argument is {'value': ...} or {'objectId': ...}")
        if "value" in argument:
            result.append({"value": argument["value"]})
        elif isinstance(argument.get("objectId"), str):
            result.append({"objectId": argument["objectId"]})
        else:
            raise DisallowedMethodError("each argument is {'value': ...} or {'objectId': ...}")
    return result
