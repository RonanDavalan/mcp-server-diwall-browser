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
