# Journal

## 2026-10-02 — Repository skeleton and security foundation (no browser)

Done:

- Project skeleton: `pyproject.toml` (Python >= 3.12, `mcp==1.25.0`, `websockets==15.0.1`,
  `pillow==12.0.0`, `pydantic==2.12.5`, dev extras `pytest==9.1.1`, `pytest-asyncio==1.4.0`,
  build backend `setuptools==84.0.0`), `.gitignore`, placeholder `README.md`, `requirements.lock`.
- Entry point that refuses to start until the tools are wired: `python -m mcp_server_diwall_browser`
  exits with code 2 (`tests/test_main.py`).
- Security modules, each with its tests and counter-proofs: `hardening`, `origin`, `output`,
  `sensitive_field`, `mount`, `credentials`, `journal`, `operator_profile`.
- Code-base invariants in `tests/test_invariants.py`: core dumps disabled before a credential file
  is opened, single definition of each security function, no write outside `mount.py`, private and
  link-refusing journal files.

Proof:

```
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # 400 passed (Python 3.13.14 and 3.12.3)
.venv/bin/python -m compileall -q src  # no output, exit code 0
```

Remaining:

- CDP client, browser attachment, perception, gate, confirmation, actions, evidence, tools and
  server wiring; until then the entry point exits with code 2.
- `profile_dir_is_default` and the other browser-related error codes are declared but not raised
  by any code yet.

## 2026-10-02 — CDP client, attachment to the operator's browser, tab ownership (P2)

Done:

- `cdp.py`: the only module that talks to the browser. `CdpConnection` over `websockets`
  (single reader task, answers matched by integer id, events dispatched without blocking an
  answer, `wait_event` that listens from its creation). `ALLOWED_METHODS` is checked before a
  byte is sent (`DisallowedMethodError`); `Runtime.evaluate` and `Runtime.callFunctionOn` only
  go through `call_internal`, with the exact text of `INTERNAL_SCRIPTS` (empty, read-only) and
  values passed as protocol arguments. `Page.navigate` and `Target.createTarget` refuse any
  address that is not http, https or `about:blank`. Only method names, ids and error codes are
  logged; the library's frame log (which carries parameters) is held above DEBUG; the
  environment proxy is never used (`proxy=None`).
- `browser.py`: `/proc/net/tcp` and `tcp6` parsing and loopback verification, dedicated profile
  check (also under `$XDG_CONFIG_HOME`), `BrowserAttachment.attach` in the required order
  (loopback, profile, `/json/version` on 127.0.0.1 in a worker thread, announced WebSocket host
  and port, connection, `Browser.getVersion`, `Target.setDiscoverTargets`), `TabRegistry`
  (ownership, adoption of windows opened by an owned page, navigation, frame info read on every
  call, dialogs).
- Test doubles in `tests/fixtures/`: `fake_cdp.py` (WebSocket and `/json/version` on one
  loopback port, records every message), `page_server.py`, `browser_launcher.py`,
  `pages/basic.html`.
- Invariants: page scripts written only in `cdp.py` and never formatted (INV-2 extended), the
  WebSocket and the unchecked transport used only by `cdp.py`, new single definitions.

Proof:

```
.venv/bin/python -m pytest -q                       # 585 passed, 11 skipped: no browser on PATH
PATH=<dir with a chromium link>:$PATH .venv/bin/python -m pytest -q   # 596 passed, browser tests included
```

Measured against Chromium 141.0.7390.37 (headless, test profile):

- `Browser.getVersion` reports `Chrome/141.0.7390.37` (not `HeadlessChrome`).
- `Accessibility.getFullAXTree`: `frameId` is only on the `RootWebArea` node; `InlineTextBox`
  nodes have no `backendDOMNodeId`; ignored nodes have no `name` nor `properties`; the tree of
  the main frame does not include the `srcdoc` frame.
- A window opened with `noopener` still carries `openerId` (with `canAccessOpener` false); a
  window opened from a same-origin `srcdoc` frame carries the tab's target as `openerId`;
  `window.open` without a user gesture is blocked.
- A call on a vanished session answers `-32001`; the main frame id equals the target id.

Remaining:

- The kernel of the test container has no IPv6 (`/proc/net/tcp6` absent): a missing `tcp6` is
  read as an empty table only when the kernel refuses `AF_INET6` sockets.
- `ALLOWED_METHODS` was rebuilt from the needs named in the P2 prompt: the operator compares it
  with section 5 of the common base.

## 2026-10-02 — Perception: snapshot, marks, tree, image, masks (P3)

Done:

- `perception.py`: one observation reads the main document twice
  (`Accessibility.getFullAXTree`, `DOMSnapshot.captureSnapshot` of `documents[0]` only), merged
  by backend node id. A node is shown only if the tree does not ignore it and its box passes
  the five visibility conditions (box, visibility, effective opacity over element ancestors,
  2 px, on the page); a hidden node takes its subtree with it and counts once in
  `omitted_not_visible`. Elements by role (or clickable `generic`), numbered in document order,
  150 at most (viewport first); `ElementRef` for the gate, a cleaned public view for the model.
  Every `input`, `textarea` and `select` goes through `sensitive_field.is_sensitive` (via
  `ATTRIBUTE_ARGUMENTS`); a sensitive value is emptied before leaving the module; nothing under a
  field is read. Text tree of 400 lines at most. Frames are never read and are masked.
- Image: masks drawn on the full capture, reduction to 1600 px (`LANCZOS`), masks laid again,
  marks on a separate layer erased over the masks. Fail closed (no image, `capture_unavailable`)
  when a mask fails, a shown sensitive field has no usable box, or a mask box moved between the
  snapshot and the capture. `capture_masked_png` for the evidence.
- Origin gate before any read and second reading of origin and loader after; one retry, then
  `page_changed_during_capture`. Reads give up at once when a dialog opens (`browser.read`).
- `wait_for` (`load`, `text`, `element`) by light readings every 300 ms; snapshot store of three
  per tab (`snapshot_stale`).
- `browser.py`: `read`, `DialogBlocking`, `wait_loaded`, `Tab.dialog_url`, `Tab.loading`.
- Test pages `visibility`, `visibility-limit`, `sensitive`, `hostile`, `iframe`, `long`,
  `crowded`, `fields`, `drift`, `dialog`; `page_model.py` (simulated pages), a hand-written
  snapshot in `snapshots/login.json`, `raw_cdp.py` for the test-side scripts; INV-2 test
  (no detection spelling in `perception.py`).

Proof:

```
.venv/bin/python -m pytest -q                                          # 671 passed, 30 skipped
PATH=<dir with a chromium link>:$PATH .venv/bin/python -m pytest -q    # 701 passed
```

Measured against Chromium 141.0.7390.37:

- An element with `display: none` has no node at all in the accessibility tree; an
  `aria-hidden` one is there, ignored (`ariaHiddenElement`). A text node carries its parent's
  computed style. Layout bounds are in document coordinates.
- A password field shows bullets in the tree and its value in clear in `inputValue`.
- While a JavaScript dialog is open, every call on the page session waits, `Page.getFrameTree`
  included.
- `Autofill.trigger` fills a card field in headless mode; there is no protocol trigger for the
  password manager.
- `long.html`: 100 marks, 304 tree lines. A full observation of `basic.html` takes 70 ms (median
  of 10); its 2400×1200 capture weighs 23 033 bytes, 19 929 once reduced to 1600×800.

Remaining:

- Colour contrast and clipping by `overflow` or `clip-path` are not computed (known limit,
  frozen by `test_known_limits_colour_and_clipping_are_not_computed`).
