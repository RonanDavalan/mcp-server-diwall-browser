# Architecture

Why the server is built the way it is. How to install and run it is in
[OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Two layers

```
language model, through its MCP client       reads, decides, explains to the operator
        │  MCP over standard input and output: 14 tools
mcp-server-diwall-browser                    eyes and hands: observes, acts, refuses
        │  Chrome DevTools Protocol, 127.0.0.1 only
the operator's browser                       dedicated profile, authenticated sessions

beside the server:  encrypted directory      credentials in; journal, evidence, downloads out
                    confirmation window      kdialog or zenity, answered by the operator
```

The model brings the judgement; the server brings what the model cannot have alone: the same
image and accessibility tree the operator sees, gestures in the operator's own session, and the
refusals that do not depend on the model's judgement.

## One step

1. `open_tab` or `navigate` loads a page in a tab the server owns.
2. `observe` reads the page: the server takes a snapshot, numbers the elements it can act on with
   marks, draws the marks and the masks on the image, and returns the image, the tree, the
   elements and a `snapshot_id`.
3. The model names a mark of that snapshot in `click`, `fill`, `select_option` or `press_key`.
4. The server counts the action against the ceilings, finds the browser node behind the mark,
   checks that it is the same node, still in the page, at the point it will touch, and that the
   page is on an allowed origin. The gate decides whether the action is locked (payment, deletion,
   secret change). A locked action is journaled and captured first, then the confirmation window
   asks the operator; the identity is checked again, the gesture is made, the server waits for the
   load it started, and captures again.
5. The result leaves through the single exit point, which redacts it and separates what the server
   states (`server`) from what the page contains (`page_content`). The journal gets one line.

## Modules

| Module | Role |
|---|---|
| `__main__` | entry point: hardening, logging to standard error through redaction, the server over standard streams |
| `hardening` | core files off, umask 077 |
| `origin` | parsing, normalising and comparing addresses and origins; the subdomain wildcard, a type of its own |
| `output` | the single exit point: redaction, envelope, `server` and `page_content` kept apart |
| `sensitive_field` | the predicate that says a field is sensitive |
| `mount` | whether the encrypted directory is mounted as expected, and every path inside it |
| `credentials` | reading credential files, resolving one for an exact origin, and the schema other code may ask |
| `credential_import` | an operator command, outside the server: converts an old credential file, or a directory of them, to the schema, showing no value |
| `journal` | the audit journal, one JSON line per call |
| `operator_profile` | reading the operator profile; with `confirm`, the only module that reads the environment |
| `cdp` | the DevTools client, its list of allowed methods and its constant scripts |
| `browser` | attachment checks, tabs the server owns, navigation, dialogs |
| `perception` | snapshot, marks, tree, image, masks, what is kept under the caps, waiting for a text or an element |
| `gate` | the locked categories, their detection, the ceilings |
| `confirm` | the confirmation: text of the window, native window or client's request |
| `actions` | click, fill, select an option, keys, scroll, dialogs, through one pipeline |
| `evidence` | captures and downloads written in the encrypted directory |
| `guide` | the text the model reads when the session opens |
| `server` | the factory of the server, its dependencies, the browser session, the journal of every call |
| `tools/` | one file per tool, each through the exit point |

Each security check exists in one module only; a second module that needs it calls the first.
Tests check this, and that no tool bypasses the exit point.

## Safeguards

- Loopback: the server attaches only if every socket on the debugging port listens on loopback
  (read in `/proc/net` at each attachment), if the declared profile is not the browser's default
  one, and if the browser announces a debugging address on that same loopback port.
- Allowed origins: a page outside them is neither read nor acted on; only its origin is returned.
  An entry is an origin or a subdomain wildcard (`https://*.example.invalid`), written by the
  operator; a wildcard widens reading and acting, never typing. A credential is typed only on the
  exact origin its file names, and a file that lists a wildcard is refused.
- Locked actions: payment, irreversible deletion and secret change are detected from a vocabulary
  written in the code and from card fields. Heuristics and the model's `intent_category` can only
  add a lock; nothing removes one, and the operator profile cannot configure them.
- Confirmation: a locked action waits for the operator's answer in a window the server opens.
- Fail closed: an unreadable mount table, a mask that cannot be drawn or a journal that cannot be
  written ends in a refusal, never in a weaker check.

## Why a DevTools client written by hand, with a list of allowed methods

With a general automation library, what the server can send to the browser is what the library can
send. With its own client, it is a list of 27 methods in one module, and a test compares it with
every method name in the code. Scripts are sent only from a table of constant texts; the arguments
of a tool travel in the protocol's `arguments` field and are never formatted into a script.

## Why a mark is the browser's node, not a rank

A mark is bound to the browser's node for that element in that snapshot. A rank ("the third
button") moves when the page inserts a banner or a line, and the same number then designates
another element, possibly a payment button. Bound to the node, a moved or replaced element is
refused (`element_stale`) and the model observes again.

## Why masks and marks are drawn on the image by the server

Drawn inside the page, an overlay can be moved, hidden or read by the page's own scripts. Drawn on
the captured pixels, a mask covers what the server measured, and a value under it cannot be
recovered from the image; if one mask cannot be drawn, no image is published. The marks match the
boxes of the snapshot the server checks against when the model acts. Every box is in CSS pixels:
the snapshot's device pixels are divided by the ratio of the browser's two viewports, so a mask
sits on its field at any screen scale; with no such ratio the page is refused, not read at scale 1.

## Why a layer above the page is kept first

An observation holds at most 150 marks and 400 tree lines. A consent banner fixed over the view
comes late in the document: in document order it was cut, and the elements under it were offered
as if a click could reach them. Under the caps the server keeps, in order: what belongs to a layer
above the page (a dialog, a modal node, a fixed or sticky node painting over content in view); what
is in view and clear; what is in view under a layer (`covered`); the rest. The layer's tree lines
come first under an overlay line. Layers are told from the snapshot alone, by paint order and
pointer events, with no call per element; a fixed container around the whole page is not one.

## Why the confirmation is a native window

The MCP client acts for the model and may answer a request by itself. A `kdialog` or `zenity`
window, started by the server without a shell, shows the state read in the browser at that moment
and is answered by a person, with refusal as the default button and no answer after 90 seconds
counted as a refusal. The client's own request (elicitation) is used only when the operator declares
that the client never answers it by itself.

## Decisions set aside, and what would reopen them

- Playwright or a similar library: the list of what the server sends would be the library's.
  Would reopen it: a browser whose debugging protocol the hand-written client can no longer speak.
- A tool that runs a script: it would bypass marks, identity checks, the gate and the masks, and
  read anything a page holds. Would reopen it: an operator task the tools cannot do, and then as a
  new constant script in the client, not as a script chosen by the model.
- Reading inside frames: a frame of another origin is another site, often a payment or an
  advertising one; reading it would send the model a page outside the allowed origins. Frames are
  masked. Would reopen it: a site of the allowed origins whose needed content lives in a frame of
  the same origin.
- A network transport: a listening port can be reached by any local process; standard streams tie
  the server to the client that started it. Would reopen it: an MCP client the operator needs that
  reaches servers over the network only.
- Raising the caps: they keep what the model receives bounded. Would reopen it: a page whose only
  layer is cut under the present ranking.
- A search by text in `observe`: the tools are fourteen with fixed parameters. Would reopen it: a
  layer the ranking cannot recognise on a page the operator needs.
- The old credential format read by the server: its schema is strict so that a file is never read
  as something it is not. Would reopen it: a format the operator cannot convert once.
- A tool that converts credentials: the model would be handed the path of a file of secrets, and its
  answers go to its provider; the command runs in the operator's shell. Would reopen it: an
  operator with no shell, and a way to keep the values out of the model's session there.
- A second path of resolution, for the old format: two rules binding a credential to an origin are
  two places to fix. Would reopen it: nothing; `resolve` stays the only reader of the one schema.
- A browser context of the server's own, so that the download refusal stays in the server's tabs:
  its tabs would not share the operator's authenticated session, which is the reason for the
  project. So while the server is attached, the operator's own downloads are refused too. Would
  reopen it: a browser that accepts the download setting for one tab alone, which is not confirmed.
