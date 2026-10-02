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

## Decisions

Each entry: what is decided, why, the test that would fail if it stopped holding, and what was
set aside.

### A refused path inside the volume has its own error code

- Decided: a link leading out of the volume, a link inside it, and a directory or file of the
  volume that is not private are refused with the new code `volume_path_refused`; the precise
  reason stays in `server.mount_reason` (`path_outside_volume`, `symlink`, `dir_not_private`,
  `bad_mode`, `bad_owner`, `not_regular_file`). `mount_not_gocryptfs` keeps its meaning: the
  mounted file system is not a gocryptfs volume of the server's user.
- Why: these refusals were reported as `mount_not_gocryptfs`, which tells the operator to look
  at the mount when the mount is fine and one path inside it is wrong. Error codes are stable and
  never renamed, so a code is added rather than an existing one changed.
- Control: `test_refused_paths_inside_a_mounted_volume_have_their_own_code`, with
  `test_path_code_check_catches_refusals_reported_as_a_wrong_volume` (a volume that reports them
  the old way fails it); `test_a_volume_of_the_wrong_kind_keeps_its_own_code`.
- Set aside: reusing `mount_unreadable` or `encrypted_dir_undetermined`, which describe the state
  of the volume, not one path in it.

### camelCase names are cut into words before the sensitive-field comparison

- Decided: `sensitive_field` cuts camelCase names (`userPin` into `user pin`, `BICCode` into
  `bic code`) before comparing words, and `codedeverification` joins the compact substrings.
  The cases `userPin`, `pinCode`, `otpCode` and `Code de vérification`, which were listed as
  misses, are now positive rows of the field table, with `smsOTP`, `userPWD`, `BICCode` and
  `accountIBAN`. The remaining misses (`codepin`, `numcarte`, `N° de carte`) stay listed.
- Why: a secret that reaches a tool output goes to a third party, while an ordinary field masked
  by mistake only costs one value; the misses were on the dangerous side.
- Control: the field table (`test_field_table_row`, `test_field_table_holds_for_the_real_predicate`),
  `test_camel_case_check_catches_a_predicate_that_does_not_cut_words` (the predicate without the
  cut fails the table), `test_false_positive_rate_on_one_hundred_common_field_names` still at zero,
  and `test_tricky_names_are_not_masked` (`pinterest`, `spinner`, `shipping` and their camelCase
  forms, `passportNumber`, `bypassCache`, `opinion`, `bicycle`, `nirvana`, …).
- Set aside: matching sensitive words as substrings of whole names, which would mask `spinner`,
  `shipping` and `opinion`; adding `carte` as a word, which would mask `carte_cadeau`-like fields
  without a measured need.

### A non-Latin host that the two IDNA versions could read differently is refused

- Decided: a non-ASCII host is accepted only if its IDNA 2003 conversion comes back unchanged
  (after NFC); otherwise `invalid_host`. The README tells the operator to write non-Latin hosts
  in punycode in the profile.
- Why: measured before the change, the parser was not closed on these names. `faß.test` became
  `fass.test` (the browser, under IDNA 2008, opens `xn--fa-hia.test`), `ς.test` became the sigma
  form `xn--4xa.test`, and a zero-width joiner was dropped (`a‍b.test` became `ab.test`): a profile
  allowing `faß.test` allowed another domain. The IDNA 2008 punycode `xn--fa-hia.test` was already
  refused. The safer option for a security component was taken over keeping the behaviour.
- Control: `test_a_host_the_two_idna_versions_read_differently_is_refused` (refusal of `faß.test`,
  `straße.example.test`, `ς.test`, the joiners, a full-width letter and `xn--fa-hia.test`;
  acceptance of `café.test`, its decomposed spelling, `MÜNCHEN.test`, `bücher.example.test`),
  with `test_host_check_catches_a_parser_that_trusts_idna_2003`.
- Set aside: an IDNA 2008 implementation (not in the standard library, and the dependency list is
  closed); mapping `ß` to `ss` like IDNA 2003 (it designates another domain).

### The profile's rights are checked before its content is read

- Decided: the order stays: rights of the file and its directory on the open descriptor (owner,
  mode 600, directory 700) before any byte is read, then the red-list keys, then the schema. The
  order is written in the docstring of `load_profile` and in the README.
- Why: the profile lists the allowed sites; content that someone else may write must not be read,
  let alone quoted in an error. No sentence of the module or the README stated another order
  (the original prompt text that it contradicted is not in the repository).
- Control: `test_content_of_a_profile_with_wrong_rights_never_appears_in_the_error` (a file
  readable by others, a directory open to others, a file of another user: the refusal is
  `permissions` and no byte of the content is in it), with
  `test_rights_check_catches_a_loader_that_reads_the_content_first`; the existing
  `test_redlist_key_is_refused_before_any_other_validation`.
- Set aside: validating the schema first to give a richer message; it reads content that may
  have been written by someone else.

### Everything in the repository is in English except operator messages

- Decided: comments, docstrings, the README, `docs/`, commit messages and test names are in
  English. In French: the `message` field of results meant for the operator, the text of the
  confirmation window, and the operator-facing notices (`server.notices`, whose example in the
  specification of the tab registry is French) and the notice drawn in a frame mask
  (`cadre non montré au modèle`, specified in French). The rule that a file never mixes two
  languages applies to comments and docstrings, not to these strings.
- Why: the operator's rule, restated; measured, no comment or docstring of `src/` or `tests/` was
  in French, nor any prose line of the README or the journal outside quoted code.
- Control: `test_comments_docstrings_and_documentation_are_in_english` (comments and docstrings of
  every Python file, and the prose of `README.md` and `docs/` with code spans removed), with
  `test_language_check_catches_french_prose` (a French comment, a French docstring, a French
  documentation line, each on a copy, make it fail).
- Set aside: translating the notices into English, which the specification wrote in French.

### Four protections added beyond the first specification are kept

- Decided: kept, each with a test and its counter-proof:
  - duplicate JSON keys are refused in the profile and in credential files (which value would win
    is a parser detail the operator cannot see): `test_a_duplicated_key_in_the_profile_is_refused`
    and `test_a_duplicated_key_in_a_credential_file_is_refused`, against a parser where the last
    value wins (`test_duplicate_check_catches_a_parser_where_the_last_value_wins`,
    `test_duplicate_check_catches_a_credential_parser_where_the_last_value_wins`);
  - the profile is capped at 256 KiB (a file that should hold a few lines is read whole in memory
    on every check): `test_a_profile_larger_than_the_cap_is_refused`, against a loader without the
    cap (`test_size_check_catches_a_loader_without_a_cap`);
  - `Origin.__post_init__` refuses a hand-built origin the parser would refuse (an origin built in
    code must not bypass the parser): `test_hand_built_origins_are_checked`, against a class
    without the checks (`test_origin_check_catches_a_class_without_validation`);
  - `output.current_operation_id()` gives the tool's operation identifier to the journal, so a
    journal line and a tool result can be matched: `test_the_operation_identifier_inside_a_tool_is_the_one_returned`,
    against a decorator that hands out two identifiers
    (`test_operation_identifier_check_catches_two_identifiers`).
- Why: each closes a hole or carries a fact another module needs; removing one would weaken a
  check, which the operator's rules forbid.
- Set aside: merging the two identical duplicate-key hooks of `operator_profile` and
  `credentials` into one; they read different files with different refusals, and both are now
  pinned by a test.

### The HTTP stack that comes with the MCP library is never imported by the package

- Decided: the `mcp` dependency stays (its interface is the decided SDK), and no module of the
  package imports `starlette`, `uvicorn` or `sse_starlette`, directly or through
  `importlib.import_module` or `__import__`.
- Why: the server speaks over standard input and output and opens no listening port; importing
  the HTTP stack is the first step towards serving over it.
- Control: `test_no_module_imports_an_http_server_stack`, with
  `test_http_stack_check_catches_an_import` (four forms of import, each on a copy).
- Set aside: removing the dependency, which would mean leaving the decided SDK.

### Credential file names stay in the error messages (maintained)

- Decided: a refused credential file is named in the operator message (`service.json`,
  `chmod 600 service.json`, the two names of an ambiguous binding); its keys and values never
  are.
- Why: the specification of the credential files asks for it; the name is the label the
  operator gave to the file they must fix, while the keys and values stay out (unknown keys are
  refused without being named).
- Control: `test_old_format_file_is_refused_loudly` in `tests/test_credentials.py`
  (`"legacy.json" in error.message`), `test_file_readable_by_others_is_refused_with_the_fix`,
  `test_two_files_bound_to_the_same_origin_are_ambiguous`, and on the other side
  `test_unknown_key_is_refused_without_naming_it` and
  `test_not_bound_message_names_only_the_origin_and_the_expected_name`.
- Set aside: replacing names by numbers, which would leave the operator unable to find the file
  to fix.

### The list of allowed DevTools methods holds what the code sends, nothing more

- Decided: `cdp.ALLOWED_METHODS` holds 26 methods. Added for the actions:
  `Accessibility.getPartialAXTree` (identity of an element at the moment of acting),
  `DOM.getContentQuads` and `DOM.scrollIntoViewIfNeeded` (where to click),
  `Input.dispatchMouseEvent` (clicks and wheel), `Browser.setDownloadBehavior` (downloads into
  the volume). Kept although not in the initial list: `Inspector.enable`, without which the
  browser never sends `Inspector.targetCrashed` and a crashed tab stays listed as usable. Not
  added although in the initial list: `Target.activateTarget`, `DOM.getDocument`,
  `DOM.resolveNode`, `DOM.getBoxModel`, which no code sends.
- Why: an allowed method that nothing uses is a capability offered for nothing to whoever finds a
  way to make the server send it. The test-side raw client (`tests/fixtures/raw_cdp.py`) is the
  only way the tests reach other methods; no method is added for a test.
- Control: `test_the_list_of_allowed_methods_is_exactly_the_one_the_code_needs` (equality of
  sets), with `test_list_check_catches_one_more_method` (each of the four unused methods, and
  `Target.getTargets`) and `test_list_check_catches_one_method_less`.
- Set aside: adding the 29 methods of the initial list at once.
