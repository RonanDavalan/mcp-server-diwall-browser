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
| `origin` | parsing, normalising and comparing addresses and origins |
| `output` | the single exit point: redaction, envelope, `server` and `page_content` kept apart |
| `sensitive_field` | the predicate that says a field is sensitive |
| `mount` | whether the encrypted directory is mounted as expected, and every path inside it |
| `credentials` | reading credential files and resolving one for an exact origin |
| `journal` | the audit journal, one JSON line per call |
| `operator_profile` | reading the operator profile; with `confirm`, the only module that reads the environment |
| `cdp` | the DevTools client, its list of allowed methods and its constant scripts |
| `browser` | attachment checks, tabs the server owns, navigation, dialogs |
| `perception` | snapshot, marks, tree, image, masks, waiting for a text or an element |
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
  A credential is typed only on the exact origin its file names.
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
boxes of the snapshot the server checks against when the model acts.

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
- Subdomain wildcards in the allowed origins: one wildcard covers hosts the operator never saw.
  Would reopen it: a site spread over more subdomains than a profile can list (200 origins).
- A browser context of the server's own, so that the download refusal stays in the server's tabs:
  its tabs would not share the operator's authenticated session, which is the reason for the
  project. So while the server is attached, the operator's own downloads are refused too. Would
  reopen it: a browser that accepts the download setting for one tab alone, which is not confirmed.
