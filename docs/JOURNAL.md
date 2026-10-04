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

### The origin label helper keeps its public name; reads that stop on a dialog stay a keyword

- Decided: `browser.origin_label` stays public (the perception uses it to name a refused origin),
  and `main_frame_info(tab, *, stop_on_dialog=False)` keeps its keyword. Both changes were made by
  the perception against the rule of adding only; they are kept because the private name was
  used by a second module, and the keyword defaults to the former behaviour, so every caller
  written before it reads the same. Any later change to these modules is an addition.
- Why: neither identifier is fixed by the specification (they are not a module, a tool, a
  parameter of a tool, an error code nor a profile key); renaming back would make the
  perception import a private name.
- Control: `test_the_origin_label_helper_is_only_used_by_its_public_name` (no module or test uses
  `_origin_label`), with `test_rename_check_catches_the_old_name`; the browser and perception
  tests that call `main_frame_info` with and without the keyword.
- Set aside: a second, private copy of the helper in the perception (two copies of one rule).

### `counters.redacted_fields` counts the filled sensitive fields

- Decided: the counter counts the visible sensitive fields whose value was not empty and was
  withheld; an empty sensitive field is marked `sensitive` in its element reference but adds
  nothing. Written in the docstring of `perception.build`.
- Why: the perception specification asked both that every sensitive field increment the counter
  and that it be 1 on `sensitive.html`, which holds three sensitive fields of which one is filled;
  only this reading satisfies the measured case, and it tells the model that a value exists and
  was withheld.
- Control: `test_redacted_fields_counts_the_filled_sensitive_fields` (three sensitive fields, two
  filled, one plain field: the counter is 2), with
  `test_redacted_count_check_catches_a_count_of_every_sensitive_field`; the browser test
  `test_a_password_filled_by_the_page_never_leaves` (1 on `sensitive.html`).
- Set aside: counting every sensitive field (3 on `sensitive.html`).

### Tree nodes are read as measured: frame on the root only, DOM identifier sometimes absent

- Decided: no code change. Measured on Chromium 141, `Accessibility.getFullAXTree` puts `frameId`
  on the `RootWebArea` node only, and `InlineTextBox` nodes have no `backendDOMNodeId`. The
  perception never reads `frameId` from a tree node (the `frameId` read by the browser module
  comes from page events, not from the tree), and reads `backendDOMNodeId` with a type check, so
  a node without it is passed over (its children still count).
- Why: a design that relied on a per-node frame or identifier would break on the measured
  browser; the fact was written down by the browser tests, and the operator's browser
  (Chrome 154) may differ.
- Control: `test_the_perception_never_reads_the_frame_of_a_tree_node`, with
  `test_frame_check_catches_a_reading_of_frame_id`; `test_tree_nodes_without_a_dom_identifier_are_skipped_without_error`
  (text runs, a paragraph and a generic node without identifier), with
  `test_identifier_check_catches_a_builder_that_trusts_every_node`.
- Set aside: requesting the tree frame by frame to get identifiers everywhere (frames are not
  read at all).

### An open dialog stops every action but answering it, at once

- Decided: while a dialog is open in a tab, `TabRegistry.ensure_no_dialog` refuses any action at
  once with `element_obscured` (the dialog covers the whole page) and `action_expected` set to
  `handle_dialog`, with a message asking to handle the dialog first; every page call of the
  identity check and of the gestures goes through `TabRegistry.read`, which gives up as soon as a
  dialog opens. Answering the dialog needs no page read: its origin comes from the address in the
  opening event (`Tab.dialog_url`), its message from the same event.
- Why: measured on Chromium 141, while a JavaScript dialog is open every call on the page session
  waits, `Page.getFrameTree` included; only `Page.handleJavaScriptDialog` answers. An action that
  read the page as usual would wait for the whole call delay and fail as an unreachable browser.
  No new error code: `element_obscured` is the existing code of an element covered by something
  else.
- Control: `test_an_action_while_a_dialog_is_open_is_refused_at_once` on a fake that leaves every
  page call but the dialog answer unanswered, with `test_dialog_check_catches_a_direct_frame_read`
  (a version that reads the frame tree directly times out);
  `test_a_read_gives_up_and_the_dialog_can_still_be_answered`.
- Set aside: a new error code; `snapshot_stale`, which tells the model to observe again, while
  observing is exactly what cannot be done until the dialog is answered.

### Three checks written outside their module are moved back to it

- Decided: read again as a third party's code, three pieces of logic of the attachment and the
  perception were not in their single module. They now are, by additions:
  `origin.split_host_port` (the browser module had its own copy to read the announced WebSocket
  address), `origin.origin_allowed` (the perception tested membership of the allowed list inline,
  and the actions need the same test), `output.bounded_text` (the perception had its own removal
  of control and format characters and length bound).
- Why: each security rule exists in one place; two copies drift and one is forgotten.
- Control: `test_security_definitions_exist_once_in_their_module` now lists the three functions;
  `test_an_origin_is_allowed_only_when_equal_to_one_of_the_list` with
  `test_allowance_check_catches_a_prefix_comparison`; `test_split_host_port` and
  `test_split_host_port_refuses_what_a_browser_could_read_otherwise`;
  `test_page_text_is_cleaned_and_bounded` with
  `test_text_check_catches_a_cleaner_that_keeps_format_characters`. A malformed bracket in the
  announced address is now `debug_endpoint_undetermined` (it was reported as not loopback).
- Set aside: leaving the copies with a cross-reference comment.

### No specification label in the code

- Decided: two section comments of the invariant tests named an invariant by its specification
  label; they now describe the check. A test keeps labels of the specification, session numbers
  and words of project history out of comments, docstrings and test names. The earlier entries of
  this journal that carry such labels are left as they are, the journal being append-only.
- Why: the operator's conventions keep those labels out of the code; they belong to the reports.
- Control: `test_no_specification_label_in_comments_docstrings_or_test_names`, with
  `test_label_check_catches_a_label` (a requirement label in a comment, an invariant label in a
  docstring, a session number in a test name, a history word in a comment).
- Set aside: rewriting past journal entries.

### Page scripts reach a node through `DOM.resolveNode`

- Decided: `DOM.resolveNode` joins the allowed methods. The scripts of the fixed table that work
  on a node (`is_connected`, `contains`, `select_text`, `set_select_option`) run with
  `Runtime.callFunctionOn` on the page object of that node, and the browser gives that object only
  through this method. The list now holds 27 methods, frozen by an equality test.
- Why: the actions need the object of a marked node to check it is still in the page, that the
  node under the pointer or the focused node is inside it, and to select its text or an option;
  `backendNodeId` alone is not accepted by `Runtime.callFunctionOn`.
- Control: `test_the_list_of_allowed_methods_is_exactly_the_one_the_code_needs`, with
  `test_list_check_catches_one_more_method` (the four other methods of the initial list that no
  code sends still make it fail).
- Set aside: `DOM.getDocument` with a selector query (a selector would come from page data) and a
  script that finds the node by itself (a script formed with a value).

### The identity check also asks whether the node is still in the page

- Decided: between `DOM.describeNode` and `Accessibility.getPartialAXTree`, the check resolves the
  node and runs `is_connected` on it; a detached node is `element_stale` like a vanished one. The
  order of the four specified checks is kept, and all of them run again after a granted
  confirmation, just before the gesture.
- Why: a node removed from the document can still be described for a while; acting on it would
  act on nothing the human sees.
- Control: `test_a_target_replaced_by_a_clone_is_stale_and_nothing_is_clicked` (fake and real
  browser), `test_a_change_of_the_element_during_the_question_stops_the_click`.
- Set aside: checking only the role and name, which a clone keeps.

### Enter gives the focus only after the identity check

- Decided: for `press_key` with `Enter`, the mark is verified first, then the element gets the
  focus, then the gate is asked, then the key is sent. The specification lists the focus first.
- Why: giving the focus runs the page's own handlers; it is an effect on the page, and it must
  not reach an element that changed since the snapshot.
- Control: `test_enter_on_a_changed_element_gives_it_no_focus`, with
  `test_counter_proof_a_focus_before_the_check_reaches_the_changed_element`.
- Set aside: the order as written, which focuses an unverified element.

### Enter in a field of a form is a possible submission for the gate

- Decided: the gate counts `Enter` as a submission when the element is a submit control or any
  field inside a form (`form_backend_id` set); `is_submit_like` keeps its meaning for a click.
- Why: the browser submits a form on Enter from any of its text fields; a payment form whose card
  field is focused would otherwise be submitted without confirmation.
- Control: `test_enter_in_a_payment_form_is_armed` (fake and real browser),
  `test_a_card_field_in_the_form_arms_its_submission`.
- Set aside: widening `is_submit_like` itself, which the perception computes for clicks.

### A gesture that opens a dialog counts as done

- Decided: when a dialog opens during a gesture (after its first event), the gesture is reported
  as done, with `dialog_open` true and a notice that asks for `handle_dialog`; no settling is
  waited for. A dialog already open before the first event refuses the action at once.
  `handle_dialog` with no open dialog is `unsupported_element`.
- Why: measured on Chromium 141, the mouse release that opens a `confirm()` is not answered until
  the dialog is closed; waiting for it would block the action until the call delay.
- Control: `test_a_dialog_opened_by_the_click_is_said_and_left_to_the_dialog_tool`,
  `test_dialogs_opened_by_a_click_are_answered_through_the_gate`,
  `test_handling_a_dialog_when_none_is_open_is_refused`.
- Set aside: a new error code for a missing dialog.

### Accepting an armed dialog is refused: no evidence can be taken while it is open

- Decided: an `accept` whose dialog message arms the gate journals its request, then fails with
  `capture_unavailable` before any question: the evidence before the action cannot be taken while
  the dialog blocks the page, and an irreversible action without evidence does not leave. Dismissing
  stays always possible; an unarmed dialog is accepted without question.
- Why: measured on Chromium 141, `Page.captureScreenshot` waits like every other page call while
  a dialog is open; the specification refuses an armed action whose evidence cannot be taken.
- Control: `test_a_dialog_saying_delete_permanently_arms_the_gate`,
  `test_dialogs_opened_by_a_click_are_answered_through_the_gate` (real browser).
- Set aside: using the image of the last snapshot as evidence (taken before the dialog, it does
  not show the state at the time of the action); accepting without evidence.

### Settling waits for a load the gesture started, never for a quiet network

- Decided: after the gesture, the action waits up to 0.5 s for the main frame to start loading
  (the loading state is kept from the browser's events at all times), then, if it is loading, up
  to 10 s for it to stop; past that, a notice says the page had not finished loading. Both delays
  are parameters of `ActionContext`.
- Why: the specification gives three seconds for a load started by the gesture and ten seconds
  for the load event; waiting three seconds after every click that starts no load would slow every
  action without protecting anything.
- Control: `test_a_load_started_by_the_click_is_waited_for`, `test_a_quiet_page_is_not_waited_for`,
  `test_a_load_that_never_ends_is_said_in_a_notice`.
- Set aside: waiting for network silence, which never comes on many pages.

### `unavailable` is a confirmation state of the journal

- Decided: the journal accepts `unavailable` in `gates.confirmation`, and the `gate` of an action
  outcome carries it when no window could be opened.
- Why: the final journal line must say why an armed action did not leave; `denied` would claim a
  human said no.
- Control: `test_without_consent_pay_now_does_nothing` (three refusals, each with its state).
- Set aside: folding it into `denied` or `expired`.

### Confirmations asked by the operator's setting are journaled first, without evidence

- Decided: an unarmed change on an origin of `confirm_each_change` writes its `requested` line
  (with `red_list` false) before asking, like an armed action; the evidence before and after is
  kept only for armed actions.
- Why: a question asked to the operator is an event worth a trace; the evidence is required for
  irreversible actions, and keeping images of every ordinary change would fill the volume.
- Control: `test_the_operator_setting_asks_for_an_unarmed_change_on_its_origin_only`.
- Set aside: evidence for every confirmed change.

### A credential is resolved before any question, and again at typing time

- Decided: `fill` with `credential_ref` resolves the credential against the origin read in the
  browser before the gate is asked, then resolves it again from the origin read right after the
  focus, just before typing. Only the second value is typed.
- Why: a credential on an origin it is not bound to is refused without any confirmation; asking
  the operator first would offer consent where none can apply. The second reading binds the value
  to the page that receives the keys.
- Control: `test_a_credential_on_a_neighbouring_origin_is_refused_without_confirmation` (other
  port, other scheme, sub-domain; implicit and by name), `test_the_credential_origin_is_the_one_read_at_typing_time`
  with `test_counter_proof_an_origin_taken_from_the_snapshot_types_on_the_wrong_site`.
- Set aside: reading the origin once, at the start of the action.

### The download setting is given before every gesture, and measured to end with its connection

- Decided: `DownloadManager.prepare` runs before every click and every Enter, as specified, and
  the notices of settled downloads are added to the outcome of the action that is finishing (a
  download that ends later is said by the next action).
- Why: measured on Chromium 141, `Browser.setDownloadBehavior` ends with the connection that set
  it (after a reconnection, a download goes to the browser's default directory), and the browser
  creates the download directory, parents included, when it does not exist.
- Control: `test_the_download_setting_ends_with_the_connection_that_set_it` (real browser),
  `test_a_click_prepares_downloads_into_the_volume`, `test_a_click_with_the_volume_closed_forbids_downloads`.
- Set aside: setting the behaviour once at attachment, which a reconnection silently undoes.

### The download setting is closed when the action ends

- Decided: `DownloadManager.forbid` sends `Browser.setDownloadBehavior` with `deny` in a `finally`
  at the end of every click and every Enter, refused or not, and forgets the directory. When the
  gesture started a load, the action first waits at most `settle_download_start_s` (0.5 s) for a
  download to begin, because the download a load turns into begins a moment after the load ends.
  A download already begun is left to end: it is tracked by its identifier and settled by the
  next action's notices as before. The server still has to call `forbid` once at attachment
  (server wiring).
- Why: the setting given before a gesture names a directory of the volume and stayed active
  after it. A page can start a download without any gesture; with the volume closed in between,
  the browser creates the missing directory, parents included, under the mount point (measured
  earlier), so a file of the page would be written outside the volume, in clear. Measured on
  Chrome 154 and Chromium 141 behaviour of the real browser tests: `deny` sent once a download
  has begun does not cancel it (it ends in the volume); `deny` sent before the download has begun
  refuses it, so a download that begins after the grace window is refused (closed on failure).
- Control: `test_downloads_are_forbidden_again_when_the_action_ends`,
  `test_downloads_are_forbidden_again_when_the_gesture_is_refused`,
  `test_a_page_download_between_two_actions_goes_nowhere` (real browser: the volume directories
  are removed after the action, a page download then creates nothing anywhere),
  `test_a_download_begun_by_the_click_ends_in_the_volume_after_the_setting_is_closed` (real
  browser, a download that takes 1.5 s). Counter-proofs: `test_counter_proof_a_setting_left_open_is_seen_by_the_checks`,
  `test_counter_proof_a_setting_left_open_writes_outside_the_volume` (real browser: the directory
  is recreated and a file lands in it), `test_counter_proof_without_the_wait_a_download_that_begins_late_is_refused`.
- Set aside: waiting for every running download to end before closing (not needed, `deny` does
  not cancel a begun download); closing the setting only on success (a refused gesture leaves it
  open); a timer that closes it later (a window in which a page download still lands).
- Limit: a download that a page starts more than the grace window after a gesture, for example
  after a script fetches the file first, is refused. The operator downloads it by hand.

### Three requirements without a mechanical check get one

- Decided: `tests/test_dependencies.py` (every dependency pinned to one exact version, the only
  extra is the development one, the installed versions are the pinned ones);
  `test_only_the_confirmation_module_starts_a_process` (the server never starts a browser: the one
  process it starts is the confirmation window); `test_no_module_listens_on_a_port_or_relays_one`
  (no server module, no `bind` or `listen`, so the debug port is not relayed).
- Why: a review of the specification labels cited by the first four prompts against the tests
  found these three held by the absence of code, with nothing that would fail if code appeared.
- Control: each has a counter-proof that copies the package, adds the forbidden code to one
  module and sees the check name it.
- Set aside: forbidding the `socket` import, which `browser.py` (IPv6 support probe) and
  `journal.py` (host name) use legitimately.

### The simulated page never gives the identity of a removed node to a new one

- Decided: the page model of the tests keeps the highest identity it gave and numbers new nodes
  above it.
- Why: a browser never reuses a node identity within a document; a fake that did made a replaced
  node look like the original and hid the stale-element case.
- Control: `test_a_target_replaced_by_a_clone_is_stale_and_nothing_is_clicked`.
- Set aside: none.

### The download setting is closed at attachment, and given back when the server stops

- Decided: the server's connection to the browser is built in one place (`server.ServerState`),
  which calls `DownloadManager.forbid()` right after attaching, before any action, and again on
  every new attachment after a lost connection. When the server stops (end of its input), the
  same place calls `restore_default()` and closes the connection; the tabs it opened stay open.
- Why: between the attachment and the first action, a page download would follow the browser's
  own setting, outside the volume. Measured on Chromium 141: `deny` is the browser's setting, not
  a tab's; while the server is attached, a download the operator starts in a tab of their own is
  refused too, and works again once the server's connection has ended.
- Control: `test_downloads_are_forbidden_from_the_attachment_on`, with
  `test_counter_proof_a_wiring_that_never_forbids_leaves_the_setting_open`;
  `test_a_lost_connection_is_replaced_and_the_setting_closed_again`;
  `test_when_its_input_closes_the_process_gives_downloads_back_and_leaves_tabs_open` (the real
  process); `test_while_the_server_is_attached_the_operators_own_downloads_are_refused_too` (real
  browser, the measured fact frozen).
- Set aside: a setting scoped to the server's tabs (the protocol's setting is the browser's or a
  browser context's, and the server's tabs share the operator's context).
- Review of the two commits this relies on: no defect found in the closing of the setting. The
  checks of the second commit match their words anywhere in a module's text, comments included,
  which errs on the side of refusing.

### The server checks the arguments of a tool itself; a refused one has its own code

- Decided: each tool declares its arguments as a strict model that refuses unknown keys; the
  server validates them before the tool runs. A refusal leaves through the single exit point,
  with the new code `invalid_arguments` and the names of the refused parameters (never their
  values), and is journaled like any call.
- Why: measured on the SDK in use (1.25.0), a tool registered the plain way drops a parameter it
  does not know and runs, and its own validation error leaves as a text outside the exit point.
  No existing code says that a parameter was wrong.
- Control: `test_an_invented_approval_parameter_is_refused_and_does_nothing` (`approved`,
  `confirm`, `override`), `test_a_malformed_parameter_is_refused_through_the_exit_point`,
  `test_the_sdk_alone_would_ignore_an_unknown_parameter`.
- Set aside: the SDK's own refusal (outside the exit point, values of the arguments in its text);
  `internal_error` (says nothing useful).

### Every call is journaled once, from the server, after its result

- Decided: the registered tool of the server writes the final line of every call, `status` and
  `observe` included, from the finalized result: `origin` from `server.origin` (actions now say
  the origin they checked), `result`, `mutating` from the operator's table, `intent` with the
  typed marker, the gate state the action reports, the client's name and version from the
  session. An armed action's `requested` line, written by the action before asking, is never
  written again. Without a profile there is no volume to write in: such calls are counted in
  `operations_not_journaled` with the ones the journal could not write.
- Why: one place, so no tool can forget it; the line describes what was returned.
- Control: `test_a_session_of_ten_tools_writes_ten_lines_without_any_credential`,
  `test_an_armed_action_writes_its_request_then_its_result_and_every_state_is_journaled` (the six
  confirmation states), `test_with_the_volume_closed_calls_are_counted_as_not_journaled`,
  `test_the_client_name_cannot_break_a_journal_line`.
- Set aside: a line written before the call (it could not say the result).

### The parameters are exactly the operator's table

- Decided: `press_key` takes no `intent_category` and `handle_dialog` takes no `intent`, as in the
  table, although the actions accept them; `wait` takes `for` under that name.
- Why: no parameter is added to a fixed surface.
- Control: `test_the_parameters_are_those_of_the_operators_table`.
- Set aside: exposing the extra parameters the actions have.

### The environment is read in the profile module only

- Decided: the browser module asked the process environment for the home and configuration
  directories itself; it now asks `operator_profile.environment_dirs`. Only the profile module and
  the confirmation window read the environment.
- Why: a variable read anywhere is a switch nobody reviews.
- Control: `test_no_environment_option_or_flag_turns_a_check_off`, with
  `test_switch_check_catches_an_environment_option_or_flag` (an environment read, a command-line
  option, an option string that turns something off, a test flag).
- Set aside: none.

### A tab with an open dialog is listed at once

- Decided: listing the tabs stops on an open dialog and takes the tab's origin from the address
  of the dialog's opening event.
- Why: the page answers no call while a dialog is open; the listing waited for the whole call
  delay on such a tab.
- Control: `test_a_tab_with_an_open_dialog_is_listed_at_once`, with
  `test_counter_proof_a_listing_that_reads_the_frame_as_usual_waits_behind_the_dialog`.
- Set aside: none.

### What the decisions on dialogs and downloads change for the model

- Decided: the descriptions of `click`, `fill`, `select_option`, `press_key` and `scroll` say that
  an open dialog refuses them at once (`element_obscured`, `action_expected` `handle_dialog`); the
  description of `handle_dialog` and the guide's limits say that accepting an armed dialog is
  refused with `capture_unavailable` and that the operator answers it; the description of `click`
  and the guide's limits say that a download a page starts more than half a second after a load
  ends is refused, and that the operator downloads it.
- Why: the model must know the outcome before it meets it, and what to say to the operator.
- Control: `test_the_descriptions_say_what_a_dialog_and_a_late_download_mean`,
  `test_the_limits_say_what_the_dialogs_and_the_downloads_cannot_do`,
  `test_a_dialog_open_in_the_tab_refuses_every_other_action_at_once`,
  `test_accepting_an_armed_dialog_is_refused_and_dismissing_it_works`.
- Set aside: none.

### Attempts that never reach the browser are not counted

- Decided: the ceilings count an action at the entry of the action pipeline, after the server is
  attached; a call refused before (no profile, no browser) is not counted.
- Why: the pipeline of the actions counts first thing, and the tools call it as it is; a call that
  cannot reach a page cannot loop on one.
- Control: `test_the_ceilings_hold_through_the_tools`.
- Set aside: counting twice to count those too.

### The operator's own downloads stay refused while the server is attached

- Decided: the download setting given at attachment stays the browser's, not a tab's. To download
  a file of their own, the operator stops the server or the MCP client; downloads work again as
  soon as the server's connection ends. The operator guide says it (dedicated profile, known
  limits), and so do the architecture (decisions set aside) and the README.
- Why: leaving the browser's setting open between two actions lets a page write a file outside the
  encrypted directory.
- Control: `test_while_the_server_is_attached_the_operators_own_downloads_are_refused_too`
  (Chromium 141 here, Chrome 154 on the operator's machine).
- Set aside: a browser context of the server's own, whose tabs would not share the operator's
  authenticated session. Would reopen it: a browser that accepts the setting for one tab alone,
  which is not confirmed.

### The version announced when a session opens stays the SDK's

- Decided: the server keeps announcing the SDK's version at initialization (`1.25.0`); its own
  version is `server_version` in `status`. The operator guide sends the operator to `status`.
- Why: the announcement can only be changed by writing a private attribute of the SDK, and a tool
  already gives the version.
- Control: `test_the_server_version_comes_from_status_not_from_the_announcement` (the real
  process).
- Set aside: writing the SDK's private attribute.

### The documentation says that a call refused before reaching the browser is not counted

- Decided: the operator guide states it as it is: "a call refused before reaching the browser does
  not count against the ceilings" (operator profile, troubleshooting, known limits).
- Why: the decision on counting stays; the operator must know it to read the counters of `status`.
- Control: `test_the_ceilings_hold_through_the_tools`.
- Set aside: none.

### An observation says which document and which view it describes

- Decided: the `server` part of `observe` carries `loader_id`, the document the snapshot was taken
  from, and `viewport` with `x`, `y`, `width`, `height` and `scale`. `counters` keeps its seven
  keys.
- Why: the new checks of each tool's result found that the tool returned neither, although the
  perception's specification lists them. Without them, the model cannot tell that two observations
  describe two documents, nor which part of the page the image shows. The specification of the
  checks names six counters; the seven exist and none is removed.
- Control: `test_observe_states_exactly_what_the_perception_specifies`, with
  `test_observe_check_catches_a_result_without_counters`.
- Set aside: none.

### Names under the reserved suffixes pass the check for real data

- Decided: the check of tracked files for real data allows `example.invalid`, `example.test`, any
  name under `.test` or `.invalid`, loopback and the documentation networks; an address literal is
  judged by the private-address check, and a test vector that is no host name passes.
- Why: these names can never designate a real host, and the tests of the address parser need
  several of them.
- Control: `test_no_tracked_file_holds_real_data`, with
  `test_real_data_check_catches_a_name_a_private_address_or_a_real_host` and
  `test_real_data_check_leaves_the_example_values_alone`.
- Set aside: allowing the two example domains only, which the parser's tests do not follow.

### The demo image is the only capture the repository tracks

- Decided: `docs/images/observe-demo.png`, written by `scripts/smoke_test.py --save-image` from the
  local demo page, is tracked for the README; any other tracked image is refused.
- Why: the README shows what the model receives; the image comes from a page whose whole content
  is in the repository, so it holds nothing of the operator's.
- Control: `test_no_forbidden_file_is_tracked`, with `test_forbidden_file_check_catches_each_kind`.
- Set aside: a README without an image.

### The lock lists the test tools too

- Decided: `requirements.lock` is the list of the packages installed in the tested environment,
  test tools included, the package itself excluded.
- Why: the lock lists everything installed, transitive dependencies included.
- Control: `test_every_dependency_is_pinned_and_locked_at_the_same_version`, with
  `test_lock_check_catches_a_loose_pin_and_a_lock_that_differs`.
- Set aside: a lock of the runtime dependencies only.

### The boxes of a snapshot are read in CSS pixels, whatever the screen scale

- Measured before the correction (Chromium 141.0.7390.37, headless, `--force-device-scale-factor`
  at 1, 1.5 and 2, window 1280 x 800, blank profile, pages served on 127.0.0.1). The box of the
  password field of the demo page, in CSS pixels as `getBoundingClientRect` and
  `DOM.getContentQuads` give it, and as the server held it:

  | scale | browser, CSS pixels | server before | `viewport.height` / `content_height` before | image |
  |---|---|---|---|---|
  | 1 | (98.7, 153.4, 252.0 x 27.0) | same | 713.0 / 713.0 | 1280 x 713 |
  | 1.5 | (98.7, 154.1, 252.0 x 27.3) | (148.0, 231.2, 378.0 x 41.0) | 713.33 / 1070.0 | 1600 x 892 |
  | 2 | (98.7, 154.4, 252.0 x 27.5) | (197.4, 308.9, 504.0 x 55.0) | 713.0 / 1426.0 | 1600 x 891 |

  A long page scrolled by 500 CSS pixels (document height 3092): `content_height` was 3092, 4637 and
  6184 at the three scales, the marks in view were 25, 17 and 12, and at 1.5 the box of
  "Link number 9" was (54.0, 475.3, 152.1 x 27.0) where the browser's is (36.0, 316.9, 101.4 x 18.0).
- Cause, as suspected and now measured: `DOMSnapshot.captureSnapshot` gives `bounds`, `contentWidth`
  and `contentHeight` in device pixels (the numbers above are the CSS ones times the scale), while
  `Page.getLayoutMetrics` gives `cssVisualViewport` in CSS pixels and `visualViewport` and
  `contentSize` in device pixels. The perception read the first as if they were the second: a mask
  laid with those numbers did not cover its field, and the image was published as masked.
- Decided: the scale is the ratio of `visualViewport.clientWidth` to `cssVisualViewport.clientWidth`,
  both already in the answer the perception reads; `Dom` takes it (`device_scale`) and divides the
  boxes and the content size by it, so that everything the class exposes is in CSS pixels, where
  the viewport, the image mapping and the clicks already were. Measured with a pinch zoom of 2
  (`Emulation.setPageScaleFactor`) at a scale of 1.5: the snapshot's boxes do not move, the two
  viewports become 960 x 535 and 640 x 356.67, and their ratio stays 1.5. A browser that gives
  neither viewport, or a ratio outside 0.1 to 20, is refused with `SnapshotFormatError`: reading it
  as a scale of 1 would put a mask in the wrong place without a sign. After the correction the
  demo page at 1.5 gives (98.7, 154.1, 252.0 x 27.3) for the password field and 713.3 for
  `content_height`; the long page gives 3091.3, 3092 and 3092 and 25 marks in view at the three scales.
- Not measured: the page zoom of the browser (Ctrl and +), which the ratio is meant to follow as
  well, and the reading on the operator's own screen (Opera 136 at 1.5), which was not repeated here.
- Control: `test_boxes_masks_and_sizes_are_in_css_pixels_at_every_scale` (simulated browser at 1, 1.5
  and 2), `test_the_marks_and_the_mask_of_the_demo_page_are_where_the_browser_has_the_elements`,
  `test_the_marks_of_a_scrolled_long_page_are_where_the_browser_has_the_elements`,
  `test_the_password_field_of_the_demo_page_is_covered_by_its_mask_and_the_mask_goes_no_further` and
  `test_the_viewport_and_the_content_are_in_css_pixels` (real browser at the three scales, the boxes
  read apart from the server and the marks and the mask looked for in the published image within
  two pixels), `test_every_snapshot_read_in_the_perception_is_given_its_factor`. Counter-proofs:
  `test_scale_check_catches_a_perception_that_ignores_the_factor` and the three
  `test_audit_catches_*` tests, which run the old reading and fail at 1.5 and 2 only.
- Set aside: `window.devicePixelRatio` read by a script (one more constant script and one more
  round trip, and it would not follow a pinch zoom the way the snapshot does); the ratio of
  `contentSize` to `cssContentSize` (rounded to whole pixels: 1070 / 713 = 1.5007); refusing the
  image whenever the scale is not 1 (the operator's screen is at 1.5: the tool would be unusable).

### Under the caps, what is above the page is kept first, and what it covers says so

- Measured before the change, on `tests/fixtures/pages/dense-banner.html` (643 things to click: a
  sticky header, a menu of 96 links, a menu of 132 and eight filters under a banner fixed over the
  lower 300 pixels of the view, 400 results below; the three buttons of the banner last in the
  document), Chromium 141.0.7390.37, window 1280 x 800. With the choice of the time (the view
  first, then document order): 150 marks and 400 tree lines, both truncated; none of the banner's
  four elements marked or in the tree, so "Refuse" could not be named; the same after a scroll of
  500 pixels. With the new choice: the four elements of the banner are marked (marks 147 to 150)
  and are the first lines of the tree under the overlay line; 47 elements carry `covered` (the
  eight filters and 39 of the menu behind the banner); after the scroll the sticky header joins
  the overlay lines. Nothing from below the view is marked while elements of the view are left out.
- Decided: when marks exceed 150 or tree lines exceed 400, the server keeps, in this order, what
  belongs to a layer above the page, with all it holds; what is in view and not covered; what is in
  view and covered, which carries `covered: true` (the key is there only when true); the rest in
  document order. The marks kept are numbered from 1 in document order. In the tree the layer
  comes first under `overlay "layer above the page"`, indented under it from the shallowest of its
  lines, then the rest in document order. The caps do not change; the overlay line is one of the
  400 lines. A page under the caps is read as before: no `covered`, no overlay line.
- What is a layer, and what is covered, written out because the specification leaves it to the
  code: a layer is a node of role `dialog` or `alertdialog`, a node with `aria-modal`, or a node of
  computed position `fixed` or `sticky`, visible, with its box in view, with something to read or
  act on in view outside it, under its box, painted before it. The last condition keeps out a
  fixed node that hides nothing, and above all a fixed container around the whole application,
  which would otherwise take every element and end the rule that the view comes first. An element
  in view is covered when the centre of its box lies in a box of a layer that is not its own,
  painted after it, and that takes the pointer: the boxes of a layer are those of its topmost
  nodes whose computed `pointer-events` is not `none`, so a transparent wrapper that lets clicks
  through covers nothing.
- Measured on the dense page: the boxes of a fixed node in the snapshot are in document
  coordinates (the banner's top is at 411 with no scroll and 911 after a scroll of 500), and the
  paint order the snapshot gives puts the fixed banner (8) and the sticky header (7) after the
  positioned menu (5). The snapshot now also asks for the computed `position` and `pointer-events`
  of each node, and uses the paint order it already asked for; with none, the document order
  stands in. A page with more than 100 positioned nodes has the first 100 examined.
- Not decided by the text, taken by the session: `covered` and the overlay line follow the caps,
  as PER-46 puts them under the ranking. The alternative is to state `covered` on every
  observation; it would change the shape of `elements` on pages that never reach a cap.
- Control: `tests/test_perception_ranking.py` on a simulated page (the buttons of a banner marked
  and last, `covered` on the elements behind it and nowhere else, the overlay line and its
  indentation, a sticky header that is a layer only once content is under it, a dialog by its role,
  a modal node, a dialog out of view, a fixed node painted behind the content, a wrapper that lets
  the pointer through and one that takes it, nothing changed under the caps, the order of the
  choice itself) and `tests/test_ranking_integration.py` in a real browser (the same, plus a
  click on "Refuse" by its mark that takes the banner away, clicks on three covered filters refused
  as `element_obscured`, the page scrolled). Counter-proofs: the choice of the time is kept in the
  tests as a function passed in place of the real one, and with it the banner has no mark, no
  overlay line and "Refuse" cannot be named (`test_ranking_check_catches_the_old_choice_that_dropped_the_banner`,
  `test_tree_check_catches_the_old_choice_that_dropped_the_banner_lines`,
  `test_ranking_check_catches_the_old_choice_on_the_dense_page`,
  `test_banner_check_catches_the_old_choice_that_left_no_mark_on_refuse`,
  `test_ranking_check_catches_the_old_choice_on_the_scrolled_page`); a perception that never looks
  under the banner flags nothing (`test_covered_check_catches_a_perception_that_never_looks_under_the_banner`
  in both files); a rank that puts the covered with the clear is caught
  (`test_choice_check_catches_a_rank_that_puts_the_covered_with_the_clear`).
- Set aside: raising the caps; a search by text in `observe` (the surface of the tools is fixed);
  one `DOM.getNodeForLocation` per element to know what is covered (a round trip per element, and
  the snapshot already holds what is needed); taking every fixed or sticky node for a layer.
- Limits, written in the guide: a banner that overlaps nothing in view is not a layer, so it is
  cut like any element if the view holds more than the cap; the cover is the box of the layer, not
  the pixels it paints, so an element under a transparent part of a layer's box is flagged covered;
  the colour and the transparency of a layer are not computed.

### An old credential file is carried over by a command of the operator, not read by the server

- Decided: `python -m mcp_server_diwall_browser.credential_import --source <absolute path>
  --name <label> [--origin <origin> ...]` converts one file of the old format (keys
  `origines_autorisees`, `username`, `password`, `totp_cle`, `ntfy_topic`, optional `checksum`) into
  `credentials/<name>.json`, inside its own process, printing one line of JSON (`written`, `origins`,
  `carried`, `not_carried`, `origins_differ_from_source`, `origins_assumed_https`) or one line
  `error: <code>` with exit code 1. It is not an MCP tool: the fourteen stay fourteen, and no module
  of the package imports it. Its steps and refusals are in the operator guide, section 6.
- Why: retyping the values passes them through the operator's editor and eyes and the session of the
  model that says what to type. The server must not read the old format either, which would undo the
  strict schema of `credentials`.
- Taken by the session, where the text left it open:
  - the volume gets one new way to read: `Volume.read_file_at`, a private file anywhere under the
    mount point, resolved first, on the volume's device, with no link followed below the volume's
    directory; the reading it shares with the project's files is one function. A link that leads out
    of the volume is `source_not_in_volume`, a link inside it is `source_invalid`;
  - a new file is published whole by `Volume.publish_new`: written to a hidden file of the same
    directory, in mode 600, and given its name by a hard link, which fails when the name is taken. On
    a file system that cannot link, the name is taken first, exclusively, and removed if the write
    fails. The hard link was not tried on a real gocryptfs volume;
  - `credentials` offers `valid_name`, `loads_strict`, `validate` and `credentials_directory` instead
    of the command copying them; `resolve` decides nothing differently, and a test requires
    `validate` and `resolve` to agree on ten kinds of content;
  - a missing `credentials/` directory is `credential_invalid`, there being no code for it;
  - the hosts of the old file must be plain host names (no port, no capital, no scheme): `shop.example.invalid:8443`
    is refused as `origin_invalid` unless origins are given, rather than read as a port;
  - an empty user name or password counts as absent; one of another type is `source_invalid`;
  - `not_carried` counts every key of the file left behind except the list of hosts, which becomes the
    origins; the checksum counts;
  - a name that is taken is the one refusal that adds a word to its line: `error: target_exists
    <base name>`;
  - the command line is read by an `argparse` parser whose error says only `invalid_arguments`, since
    its own message repeats what was typed; the help is not offered;
  - the invariants that keep a command line and standard output out of the package name this one
    command as their exception, limited to its three options and its one line of result.
- Control: `tests/test_credential_import.py` (the file produced, mode 600 in a 700 directory, the six
  keys of the line, no value in anything printed for a success and for every refusal, the schema of
  the server unchanged, the neighbours of the origin refused, a refusal and no file created for a
  taken name, a link as source, as target and as a directory, a source outside the volume or off its
  device, rights for group or others, a wrong checksum, nothing to carry, a bad name, a bad origin,
  hosts that are not plain, a closed volume, a source of the wrong shape, a failing write, an
  argument line that is not the three options, the process itself), `tests/test_volume_files.py`,
  `tests/test_credentials_api.py`. Counter-proofs: an output that does not go through the redaction
  repeats a value (`test_printing_check_catches_an_output_that_does_not_go_through_the_redaction`); a
  refusal that repeats a value; a schema that would accept the old format; a conversion that does not
  verify the checksum; a publication that leaves half a file; a reader that judges the path as written
  and follows a link out; a publication that renames over a taken name; a command that reads before
  lowering the core file limit; a second way to write; a way out to the network or to a program.
- Set aside: reading the old format in the server; a tool that converts; a second path of resolution
  (the three are in the architecture, with what would reopen them); deleting the source after the
  conversion (not asked; the old file stays); replacing a file that exists.

## 2026-10-02 — Security gate, human confirmation, actions, evidence and downloads

Done:

- `gate.py`: the hard-coded vocabulary of the three locked categories, whole-word matching after
  normalisation, the card-field and declared-category signals (the model's category only adds),
  and `Pacer` (250 ms between actions, 200 actions and 50 pages per process by default, the
  profile read on every call).
- `confirm.py`: the text of the window built from the browser state, `kdialog` or `zenity` started
  without a shell with refusal as the default button when the tool offers it, the 90 s watchdog
  that kills the window, the client's elicitation only on the operator's declaration, one answer
  for one action.
- `cdp.py`: the five constant scripts of the actions, and `DOM.resolveNode`.
- `browser.py`: internal scripts that give up when a dialog opens.
- `actions.py`: click, fill (text or credential), select an option, keys, scroll, dialogs and
  manual evidence, through one pipeline: ceilings, identity of the mark, allowed sites, gate,
  journal line and evidence before an armed action, the human's consent, identity again, the
  gesture, settling, evidence after.
- `evidence.py`: captures and downloads written through the volume facade only, downloads named
  by the browser with an identifier and renamed from the suggestion reduced to safe characters.
- Tests: `tests/test_gate.py` (88-row table, 100-label measure), `tests/test_confirm.py` (fake
  `kdialog` and `zenity`), `tests/test_evidence.py`, `tests/test_actions.py` (fake browser),
  `tests/test_actions_integration.py` (Chromium), new invariants (no script formed in the actions,
  private evidence and download files).

Proof:

```
.venv/bin/python -m pytest -q                       # 1045 passed, 45 skipped: no browser on PATH
PATH=<dir with a chromium link>:$PATH .venv/bin/python -m pytest -q   # 1090 passed, browser tests included
.venv/bin/python -m compileall -q src               # no output, exit code 0
```

Measured:

- On 100 common button labels, the gate arms 14: the 12 a careful reader would lock, and 2 false
  positives (`Effacer le formulaire`, `Supprimer le filtre`), no miss. Known misses of the starting
  vocabulary outside that list: `Reset your password`, `Regenerate API key`, `Finaliser l'achat`,
  `Valider mon panier`.
- Against Chromium 141.0.7390.37: the download setting ends with its connection; the browser
  creates a missing download directory; the mouse release that opens a dialog is not answered
  until the dialog closes; a page capture waits while a dialog is open.

Remaining:

- The tools that expose these actions to the model, and the final journal line of each tool,
  are not wired yet.
- A download started by a page between two actions goes to the directory prepared for the last
  action; if the volume is closed in the meantime, the browser would create that directory on the
  unencrypted disk below the mount point.

## 2026-10-03 — The MCP server, its fourteen tools, the journal of every call and the guide

Done:

- `server.py`: `Dependencies` (everything the tests replace), `production_dependencies()`, the
  one factory `create_server`, the browser session (attachment, download setting closed at once
  and given back at the end), the check of every tool's arguments, the journal line of every call,
  the choice of the confirmation window or the client's own confirmation.
- `__main__.py`: umask, core files off, log to standard error through redaction, then the server
  over standard input and output.
- `tools/`: the fourteen tools of the operator's table, one file each, through the exit point.
- `guide.py`: twelve rules with their consequence in the first lines, then the reference (tools,
  error codes, limits).
- `mount.Volume.holds`, `operator_profile.environment_dirs`, the code `invalid_arguments`, the
  origin and the evidence path in action results, a listing of tabs that does not wait behind a
  dialog.
- Tests: `tests/test_server_session.py`, `tests/test_server.py` (in memory, through the SDK's
  client), `tests/test_server_process.py` (the process over standard streams, its sockets and
  limits), `tests/test_server_integration.py` (Chromium), `tests/test_guide.py`.

Proof:

```
.venv/bin/python -m pytest -q                       # 1151 passed, 52 skipped: no browser on PATH
PATH=<dir with a chromium link>:$PATH .venv/bin/python -m pytest -q   # 1203 passed, browser tests included
.venv/bin/python -m compileall -q src               # no output, exit code 0
.venv/bin/python -m mcp_server_diwall_browser < /dev/null   # exit code 0, nothing on standard output
```

Measured:

- SDK 1.25.0: a plain tool drops an unknown parameter and runs; a result can be marked as an
  error (`isError`); the client announces elicitation when it has a handler for it, and the server
  asks through `Context.elicit` with a model of one boolean; the server's version given at
  initialization is the SDK's own.
- Against Chromium 141.0.7390.37: while the server is attached and holds `deny`, a download in a
  tab of the operator is refused too; it works again once the server's connection has ended.

Remaining:

- The detailed documentation and the guides of the operator.

## 2026-10-03 — Documentation, the invariants gathered, acceptance on a fresh clone

Done:

- `README.md`: connecting the server to an MCP client, the image the model receives, what is
  tested and what is not, the known limits.
- `docs/OPERATOR_GUIDE.md`: the final layout, then what is needed, installation, the dedicated
  browser profile, the encrypted directory, the operator profile, credential files, what leaves the
  machine, what the server never does, the confirmation, backups, one troubleshooting entry per
  error code with the messages of the code, the known limits.
- `docs/ARCHITECTURE.md`: the two layers, one step, the modules, the safeguards, the reasons for
  the main choices, the decisions set aside and what would reopen them.
- `scripts/smoke_test.py` and the demo page `docs/demo/index.html`; `docs/images/observe-demo.png`
  written by it.
- `observe` says which document and which view it describes (`loader_id`, `viewport`).
- `tests/test_tools_results.py`: the result of `wait`, `select_option`, `press_key`, `close_tab`
  and `observe`, each check with a defective tool it catches; the version read from `status`.
- `tests/test_invariants.py` gathers the invariants (exit point of every production tool, nothing
  on standard output, DevTools methods and scripts, environment reads, forbidden tracked files);
  `tests/test_layout.py` checks the layout and the hygiene of the repository (module names,
  pins and lock, no optional import, no real data, comments in English without history, module
  headers). Each check has its counter-proof.
- `.github/workflows/ci.yml`: Python 3.12, install, `compileall`, the suite without browser tests.
- `requirements.lock` regenerated from the tested environment.
- The parsing test of IPv6 sockets compares addresses, not their text, which differs between
  Python 3.12 and 3.13.

Proof:

```
.venv/bin/python -m pytest -q                       # 1205 passed, 54 skipped: no browser on PATH
PATH=<dir with a chromium link>:$PATH .venv/bin/python -m pytest -q   # 1259 passed, browser tests included
.venv/bin/python -m compileall -q src               # no output, exit code 0
```

On a fresh clone, following the README and the operator guide, with Python 3.12.3:

```
.venv/bin/python -m pytest -q -m "not browser"      # 1205 passed, 54 deselected
.venv/bin/python scripts/smoke_test.py              # exit code 3 without a browser, 0 with Chromium 141
PATH=<dir with a chromium link>:$PATH .venv/bin/python -m pytest -q   # 1259 passed
```

Measured:

- Python 3.12.3 writes an IPv4-mapped IPv6 address as `::ffff:7f00:1`, Python 3.13.14 as
  `::ffff:127.0.0.1`; the server's loopback check reads the mapped address in both.
- Chromium 141 started with `--remote-debugging-port=9222` listens on `127.0.0.1:9222` only.
- Read in the source of the MCP SDK 1.25.0: its client starts a server with `HOME`, `LOGNAME`,
  `PATH`, `SHELL`, `TERM` and `USER` only. The native window needs `DISPLAY` or `WAYLAND_DISPLAY`,
  so a connection made that way needs one of them in its environment block.
- The server announces `mcp-server-diwall-browser 1.25.0` when a session opens; `status` says
  `0.0.1`.

Remaining:

- Pin the actions of the workflow by commit hash before any publication; they are referenced by
  version tag.
- Run the operator guide on Debian with a real gocryptfs volume, a real confirmation window and the
  browser profile in the encrypted directory; none could be run in this environment.
- Check with Claude Code which environment it gives the server, and whether the confirmation
  window opens from it.

## 2026-10-03 — The keys of the viewport follow the specification

### `observe` states the viewport with the keys the specification names

Decided: the `viewport` of the `server` part of `observe` carries `width`, `height`, `scroll_x`,
`scroll_y` and `content_height`, in CSS pixels. It replaces the `x`, `y`, `width`, `height` and
`scale` that the previous entry on this subject chose.

Why: the perception specification (PER-14) names these five keys; the session that added the
viewport did not have that text and wrote the keys from the browser's own names. The image scale is
not lost: the `image` key already carries it. `content_height` is what tells a model that the page
goes on below the view, which `scale` never did.

How to check that it holds: `tests/test_tools_results.py::test_observe_states_exactly_what_the_perception_specifies` serves a page of 2000
pixels scrolled by 300 and expects exactly `{"width": 800, "height": 600, "scroll_x": 0,
"scroll_y": 300, "content_height": 2000}`; on the code before this change it fails.

Rejected: keeping `x`, `y` and `scale` next to the specified keys. A key the specification does not
name is a key a client may come to depend on, and the five keys of the specification are enough.

`counters` keeps its seven keys: the six of PER-14 and `marks` and `tree_lines`, which the perception
already gave before this change. Removing them would break a contract in use; the specification
lists the six it requires, not the only ones allowed.

## 2026-10-03 — A first run on a real volume, a real browser and a long public page

### What was run

A throw-away gocryptfs 2.5.1 volume was mounted on Debian and the server was started on an operator
profile that points at it, with Opera 136 (Chromium 152) launched on a profile kept in that volume
and a debugging port on the loopback interface. Through the MCP SDK client, the server opened the
home page of a public shopping site, filled the search field, pressed Enter, scrolled and
observed. `status` read `mounted`, `profile_encrypted: true` and `confirmation_channel: native`;
the journal received one line per call and the typed text appears in it as `<typed>` only. Through
Claude Code, `claude mcp list` showed the server connected and `status` read
`confirmation_channel: native`.

### Two findings, not yet diagnosed and not fixed

- On the results page, the observation held 150 marks (`marks_truncated`) and 400 tree lines
  (`tree_truncated`) with 1005 elements left unmarked. The buttons of the cookie banner that covered
  the lower half of the view were not among the marks, so the model could not answer the banner.
  This is the cap the specification sets; the limit is now written in the README and the guide.
- In the image of that page, several boxes do not sit on the element their mark names: the box of
  the search field is narrower than the field, and after a scroll of 500 pixels the boxes of the
  price filters lie about 200 pixels below where the filters are drawn. Marks 143 to 150 are drawn
  over the banner that covers the elements they name. The cause is not measured: the page may have
  changed layout between the snapshot and the capture, or the boxes may be computed in another
  scroll state. On the local demo page of the smoke test the boxes are aligned. Nothing was changed.

How to check that it holds, when it is looked at: run the same sequence against the same page with
`scripts/smoke_test.py --save-image` style captures before and after a scroll, and compare each
box with the element's position read from the accessibility tree in the same observation.

## 2026-10-03 — The mask check on Chrome 154, the download intermittence and the page zoom

The browser available when this was written is Chromium 141.0.7390.37 (preinstalled, Linux). The
failures reported on Chrome 154.0.8037.97 could not be replayed as such; what could be measured, and
what could not, is said under each heading.

### The mask check took anti-aliased text for a mask that overflows

What was measured, before changing anything. `docs/demo/index.html` observed through `Perception` at
three screen scales on Chromium 141, the published image read back, every group of pixels of exactly
the mask colour `(40, 40, 40)` listed (8-connected groups, whole image; the box is the field's
`DOM.getContentQuads` box scaled to the image):

| scale | image | box of the password field (image pixels) | mask group | groups apart from it |
|---|---|---|---|---|
| 1 | 1280×713 | (98.7, 153.4)–(350.7, 180.4) | 7084 px, bbox (98, 153)–(351, 181), touches the box | 0 |
| 1.5 | 1600×892 | (123.4, 192.7)–(438.4, 226.9) | 11060 px, bbox (123, 192)–(439, 227), touches the box | 0 |
| 2 | 1600×891 | (123.4, 193.0)–(438.4, 227.4) | 11060 px, bbox (123, 193)–(439, 228), touches the box | 6 (sizes 2, 1, 1, 1, 1, 1) |

- The mask does not overflow on this browser: it extends at most 0.7 pixel past the box on each side,
  which is the rounding outward of a box with fractional edges. Nothing in production was changed.
- The six groups apart from the mask at scale 2 sit at rows 41 to 66, a hundred and more pixels above
  the field; each is surrounded by `(0, 0, 0)` and `(255, 255, 255)` pixels and by pixels of 37 to 41
  grey, which is the anti-aliasing of the black heading and paragraph text. So the colour `(40, 40, 40)`
  does occur in page text by chance on a browser, here outside the 12-pixel window and so not seen
  by the check.
- The message of the failure, `the mask goes 12.7 pixels beyond the box`, is exactly what one text
  pixel of the mask colour gives at the left edge of the window at scale 1: the box starts at
  x = 98.7, the window at `floor(98.7) − 12 = 86`, and 98.7 − 86 = 12.7. The word "Password" is in
  the same `<label>` as the field, a few pixels to its left, so its glyphs are inside the window.
  Reproduced on the real image of scale 1 by painting one pixel `(40, 40, 40)` at (86, 165): the
  check as it was gives `the mask goes 12.7 pixels beyond the box`; the corrected check gives
  nothing. This shows the mechanism with the number; it is not a replay of Chrome 154's own pixels,
  which were not available here (the operator's range of columns 36 to 350 is wider than the field
  at these coordinates and is consistent with the glyphs of the label, which is an inference, not a
  measurement).

Decided: `mask_found_apart` (in `tests/test_scale_integration.py`) takes as the mask only the group of
pixels of the mask colour that is joined, by a side or a corner, to the inside of the box, looked for
inside the same 12-pixel window. The tolerance (2), the window (12) and the colour are unchanged. A
failing check still fails on a mask that goes more than the tolerance beyond the box, and on a mask
that does not cover it.

Why the joined group and not the rows wider than half the box: the group is measured on the four
sides the same way, so a mask that overflows to the left or right is seen as well as one that
overflows above or below; a row wider than half the width sees the vertical overflow only.

Rejected: raising the tolerance or narrowing the window, which would let a real overflow through; and
changing the mask colour, which would only move the chance of a collision.

How to check that it holds: `pytest -q tests/test_mask_check.py` (no browser). The same cases run
on the real check and on two checks written in the test with a defect. The check as it was first
written fails the exact-mask-with-page-pixels case; a check that only looks inside the box fails the
four overflow cases. Both defects were also introduced one at a time in `mask_group` by hand and the
cases failed. With a browser, `pytest -q tests/test_scale_integration.py` passes at 1, 1.5 and 2.

### The page zoom: sizes and boxes hold, the image and its masks do not

Measured on Chromium 141.0.7390.37 only (Chrome 154 was not available), on the demo page, at page
zooms of 100, 125, 150 and 200 % and at screen scales of 1 and 1.5 (eight browsers, each with a blank
profile).

How the zoom was made: the browser's own description of its protocol (`/json/protocol` on the
debugging port, read on the loopback interface) has no command or event that mentions a zoom, so a
page zoom cannot be set through the protocol. A zoom level written in the profile's preferences
before the start (`partition.default_zoom_level.x`, the power of 1.2 that gives the factor) reaches
the page: `devicePixelRatio` is zoom times screen scale (1.25, 1.5, 2 at a scale of 1; 1.875, 2.25, 3 at
1.5), `innerWidth` falls to 1024, 853 and 640, and `Page.getLayoutMetrics` reports `zoom` 1.25, 1.5 and
2. A per-host zoom entry for `127.0.0.1` had no effect, and the keyboard route (Ctrl +) was not tried;
whether the browser keeps the two kinds of zoom in the same way is not confirmed. The measuring
probe opened its own connection and called methods that the server's list does not allow
(`DOM.querySelector`, `Page.captureScreenshot` without a clip); the list bounds the server, not a
measurement.

What was read, at every combination:

- `Page.getLayoutMetrics`: `visualViewport.clientWidth` stays in device pixels of the whole window
  (1280 at a scale of 1, 1920 at 1.5) and `cssVisualViewport.clientWidth` falls with the zoom (1024,
  853.33, 640 at a scale of 1). Their ratio is exactly `devicePixelRatio`, so `_device_scale` is
  the screen scale times the zoom.
- `DOMSnapshot.captureSnapshot` bounds: in device pixels. The left edge of the user-name field is
  106.7 CSS px; the snapshot gives 133.38, 160.05, 213.39 at a scale of 1 and 200.06, 240.08, 320.09
  at 1.5 for zooms of 125, 150, 200 %: the ratios are 1.25, 1.5, 2 and 1.875, 2.25, 3. The document
  size it gives (`contentWidth`, `contentHeight`) is in the same unit.
- `DOM.getContentQuads` and `getBoundingClientRect` agree to the hundredth, in CSS pixels, at every
  zoom; the boxes the server rebuilds from the snapshot (`bounds` divided by the ratio) agree with
  them too, as do `viewport.width`, `height` and `content_height` with what the page says.
- Clicks: the point taken from the quads reaches the same element by `DOM.getNodeForLocation` and by
  a real `Input.dispatchMouseEvent`, with the same client coordinates, for three elements at each of
  the eight combinations (24 of 24). This was outside what was asked and is not guarded by a test.

What does not hold: the image. The server cuts the screenshot with a clip given in CSS pixels
(`cssVisualViewport`). The browser reads that clip in CSS pixels times the zoom. At 100 % the two
are the same. At 125, 150 and 200 % the image is 1/zoom of the view, from its top-left corner
(1024×570 of a 1280×713 view at 125 %), and the page is painted in it at zoom times the screen
scale, while the server places its masks and marks as if it were painted at the screen scale alone.
The proof, read from the painted page and not from a box the browser states: with the clip
multiplied by the reported zoom, the image is the whole view and the top, bottom and right edges of
the unmasked user-name field are painted within 0.4 pixel of its box times `devicePixelRatio`, at all
six zoomed combinations; with the server's clip, no border is found within 8 pixels of the box
at 125 and 200 % (at either scale) and none for the top edge at 150 %.

The gap, measured with the real server (`Perception.observe` on the demo page) as the share of the
painted password field that is mask colour in the published image:

| zoom | screen scale 1 | screen scale 1.5 |
|---|---|---|
| 100 % | 100.0 % | 100.0 % |
| 125 % | 0.0 % | 0.0 % |
| 150 % | 0.0 % | 0.0 % |
| 200 % | 0.0 % | 0.0 % |

At every zoom other than 100 % the field is published uncovered, and `capture` reads `ok`. At 125 %
and a scale of 1 the field is painted at (123, 191)–(437, 225) of the 1024×570 image and the mask is
drawn at (98, 153)–(350, 181): they do not touch. The existing scale tests cannot see this, and the audit
is clean at every zoom: it compares the outlines and masks with the browser's box times the ratio
of the image to the view, which is the server's own placement, not the painted element.

Not corrected, by decision of the brief: it is the operator's to decide. What the measurement says of
the way to close it: the clip multiplied by the zoom that `Page.getLayoutMetrics` reports gives an
image of the whole view with the masks and marks at the right ratio (image width over
`cssVisualViewport.clientWidth` is then zoom times screen scale). What was not measured: a scrolled
page at a zoom (the offset of the clip would have to follow the same unit), and `captureBeyondViewport`.
A server that refused to publish an image when `zoom` is not 1 would also close it, by failing closed.

How to check that it holds: `pytest -q tests/test_zoom_integration.py` (a browser is needed; it skips
without one). It guards what holds at the eight combinations: the zoom reaches the page; the browser
paints an element at its box times the pixel ratio; the viewport and the boxes of the demo page and of
a scrolled long page are in CSS pixels, with a counter-proof that fails when the zoom is left out of the
reading. It records what does not hold as six expected failures (the mask over the password field at
the three zooms and two scales), and the two combinations at 100 % pass as the control; the check of the
mask share is itself tried without a browser on drawn images and on a check that accepts a mask anywhere.
Remove the expected failure when the image follows the view.

### Two download tests that failed at random: the setting was closed before the browser had chosen

Measured on Chromium 141.0.7390.37, each run with a fresh browser. The two tests are
`test_a_download_that_begins_a_moment_after_the_click_ends_in_the_volume` (late) and
`test_a_download_begun_by_the_click_ends_in_the_volume_after_the_setting_is_closed` (slow).

Before any change, each test alone, in a new process, 30 times:

| test | passed | failed | how it failed |
|---|---|---|---|
| late | 23 | 7 | 6 × `condition not reached in time`, 1 × `FileNotFoundError` on a `.crdownload` file |
| slow | 22 | 8 | 8 × `condition not reached in time` |

The first kind is the one the operator saw. The second is another cause, in the test itself, below.

What was waited for, what the browser had done, and when. A probe outside the repository replayed the
bench of the two tests (fresh browser per run) and wrote on one clock the browser's download and loading
events, the server's calls on the download setting and its own waits, and every change in the volume's
download directory, then kept watching 18 seconds after the click returned. 40 runs of each case:

- late: 35 ended in the volume, 5 did not; slow: 36 did, 4 did not.
- In all 9 failures the server had done what it was meant to: `_settle` returned that a load had
  started, `wait_begin` returned `True` on `Browser.downloadWillBegin` within a millisecond of the
  event, and `deny` was then sent 0.0 to 0.2 ms after the announcement. In 8 of the 9 the browser
  sent no `Browser.downloadProgress` at all; in the ninth (slow) it sent one `inProgress` 3 ms after
  `deny` was sent. In all 9, no file was ever created in the 18 seconds and neither a completion nor a
  cancellation came: the download was refused, silently.
- In the 71 successes, the first `inProgress` came between 1.3 ms before and 4.6 ms after the moment
  `deny` was sent (late) and between 0.3 ms before and 5.0 ms after (slow; six runs sent `deny` about
  290 ms late, see the end). The first change in the download directory was seen between 1.5 ms before
  and 12.8 ms after the announcement in the 56 runs where the probe logged it (the other 15 late runs
  ended before it did). So a download for which `deny` arrived after the browser had chosen is not
  cancelled, as the earlier measurement said; one for which it arrived before is. And the first
  progress is not the moment of the choice: in the successes it can follow `deny`, and in the ninth
  failure it followed `deny` and the download died all the same.

The three causes the brief names, separated by these numbers: the browser is not slow to write (the
files of the successes appear within 13 ms of the announcement, the failures never write in 18 s); the
server does see the event (`wait_begin` returned `True` in every failure, 0.0 to 0.1 ms after it); the test does not wait less
than the server's own delay (it waits 10 s, the server's waits add to 0.8 s). The cause is a fourth one: the
browser announces a download before it has chosen the behaviour that applies to it, and the server
closed the setting at the announcement.

Proved by intervention, not by the correlation alone: with the setting closed only after the first
progress of the announced download (a wait of at most one second), late ended in the volume 40 times of
40 and slow 40 of 40; with a fixed wait of 5 ms or of 50 ms before `deny`, late 40 of 40 each. The
unchanged code, in the same probe: 35 of 40 and 36 of 40.

Decided (production, `evidence.py`): `DownloadManager.wait_begin` returns, once a download is announced,
only when the browser has reported a first progress for every download announced since the last
`prepare`, or when the same `timeout_s` has passed again (a warning is logged then). The setting is
closed right after, as before, by the same `forbid`. The call site in `actions.py` does not change.

Why this and not less: the setting keeps naming the same directory of the volume, for a few
milliseconds more, and only when a download has been announced; a download that is never taken up
costs at most one more `settle_download_start_s` (0.5 s) and the setting is closed anyway. No control is
weaker: no file can land outside the volume, and `forbid` still runs in the `finally` of every click.

How to check that it holds:

- `pytest -q tests/test_download_acknowledgement.py` (no browser): a fake browser that announces a
  download and applies the setting in force `0.05` s later. The manager keeps the download; a manager
  written in the test that returns at the announcement, as it did, loses it. On the code before the
  change three of these five tests fail (the other two are the counter-proof, which asserts the loss,
  and the check that a download never taken up does not hold the setting open).
- Each test alone, in a new process, after the change: late 130 runs, no `condition not reached`;
  slow 130 runs, 1 `condition not reached` (the first 30 runs), none in the 100 that followed. The probe
  on the changed code: slow 100 of 100, late 60 of 60. The tests with a trace of the download path
  (plugin outside the repository), slow 120 of 120, and 80 of 80 while four processes were started to
  load the processor (the load itself was not measured during the run, only at the start).
- The whole browser suite, 10 times each, on the code before and after: 2 of 10 runs failed before (one
  of each test), 0 of 10 after.

What is not explained: the one `condition not reached` of the slow test after the change (1 in 130
isolated runs, 0 in 360 traced or probed runs). Not diagnosed: the failing run was not traced. One
reading, not confirmed: the first progress is a weaker sign than the wait assumes (the ninth failure
above had one and died), so the browser may choose a little after it and a `deny` that follows it by a
fraction of a millisecond can still win rarely. What remains to try: a traced loop of the slow test
until it recurs; and, if it is that window, wait for the file the browser creates in the volume
instead of the first progress (not tried: it needs the manager to look in the volume, which the manager
does not do today).

The test that raises: 1 of the 30 late runs before the change, and 3 of the 100 after, ended in
`FileNotFoundError` from the test's own probe: it lists the files of the download directory, then reads
one; the browser renames `<id>.crdownload` to `<id>` between the two. The download itself ended in the
volume. Not changed, because the brief forbids changing an existing test to make it pass. The change
that would remove it is in `complete()` of both tests: read inside a `try` that ignores `OSError` and
go on to the next file.

Also measured, not changed: in 6 of the 36 successful slow runs `deny` left about 290 ms after the
announcement. `_settle` polls every 50 ms for a load in progress; a navigation that turns into a
download starts and ends loading within one poll, so it reports no load started and waits for its own
0.3 s limit before the wait for the download is skipped. These runs were safe, because the download had
been taken up long before; they cost the action 0.3 s.

A third test is intermittent too, with or without the change: `test_counter_proof_without_the_wait_a_download_that_begins_late_is_refused`
sets the wait to zero, so the setting is closed as soon as the load ends. Alone, alternating the code
before and after the change 80 times each: 4 failures of 80 before, 4 of 80 after. (A first run of 60 and
60 gave 0 and 3: the difference was chance, which the alternating run settled.) Not traced; the end of the
load and the announcement come within a few milliseconds of each other, the end of the load first in the
timelines above, and when the announcement comes first the browser may keep the download and the test
finds a file. Not changed. A fact worth keeping: since Python 3.12 `asyncio.wait_for(x, 0)` raises
`TimeoutError` even when `x` could finish at once, so `wait_begin(0.0)` returns `False` at once with the
code before and after the change.

Rejected: lengthening the tests' timeouts (the failures never complete in 18 s, so no wait of the test is
short); a retry around the tests, or removing an assertion; a fixed sleep before `deny` (it removes the
failure here but is a guess at the browser's speed on another machine, where the wait for the
browser's own acknowledgement is not); never sending `deny` between a gesture and the download
(it would leave the setting open when no download comes); changing `wait_begin` to return as soon as the
file exists (the file name is chosen by the browser and the volume is not read by the manager).

### What is done, with the command that shows it, and what remains

Done (Chromium 141.0.7390.37, Python 3.13.14; the suite without a browser also on Python 3.12.3):

- The mask check no longer takes page text for a mask: `pytest -q tests/test_mask_check.py` (22 passed,
  no browser) and `pytest -q tests/test_scale_integration.py tests/test_mask_check.py` (43 passed).
- The page zoom is measured, and what holds is guarded: `pytest -q tests/test_zoom_integration.py`
  (37 passed, 6 expected failures that record the mask gap).
- The cause of the random download failures is removed in the code and kept by a test:
  `pytest -q tests/test_download_acknowledgement.py` (5 passed).
- `pytest -q -m "not browser"`: 1451 passed. `pytest -q`: 1570 passed, 6 expected failures.
  `python -m compileall -q src`: no output.

Remains:

- Replay on Chrome 154: `pytest -q tests/test_scale_integration.py tests/test_zoom_integration.py`. The four
  scale tests that failed there have not been seen to fail or pass on that browser here; the mechanism
  was reproduced on Chromium 141 by painting one pixel of the mask colour into a real image.
- The decision on the page zoom, which the operator takes with the numbers above. Until then the
  operator guide and the README say to keep the zoom at 100 %.
- The one remaining `condition not reached` of the slow download test (1 in 130 alone); the read in
  `complete()` of the late download test that fails when the browser renames its file (3 in 100); the
  intermittence of the counter-proof test (4 in 80). None is traced to its end.
- The test browser tries to reach Google services in the background (the environment's proxy refused
  378 connections during 80 runs of one test); the launcher does not disable background networking.

## 2026-10-04 — The image follows the view at any page zoom; three repairs of tests

Measured on Chromium 141.0.7390.37 (headless, as root in a container, Python 3.13.14). Chrome 154 was not
available here; what the operator measured on it is quoted as such, and what was not replayed on it is
said under each heading.

### The cut of the screenshot is in CSS pixels times the page zoom

Decided (production, `perception.py`): the clip given to `Page.captureScreenshot` is the scroll offset
and the size of the view, in CSS pixels, each multiplied by `visualViewport.zoom` of the same
`Page.getLayoutMetrics` that gave the view (`_screenshot_clip`). The decoded image must be within 2
pixels, on either axis, of `visualViewport.clientWidth` by `clientHeight`, the view in device pixels
(`_image_fits_view`). A zoom that is absent, not a number, not finite or not above zero gives no
screenshot call at all (`_page_zoom`). In each case `capture` reads `unavailable`, the notice says so and
the tree is still served. The second reading, the one that compares the boxes before and after the image,
compares the zoom of the two readings too.

Why: the browser reads the clip in CSS pixels times the zoom, so a clip in plain CSS pixels cut the top
left part of the view at any zoom other than 100 % and the masks, placed for the whole view, landed
beside their fields. Measured here with a clip in CSS pixels, the size of the image on the demo page:

| zoom | view in device pixels, scale 1 | clip in CSS pixels | clip times the zoom |
|---|---|---|---|
| 100 % | 1280 × 713 | 1280 × 713 | 1280 × 713 |
| 125 % | 1280 × 713 | 1024 × 570 | 1280 × 713 |
| 150 % | 1280 × 713 | 853 × 475 | 1280 × 713 |
| 200 % | 1280 × 713 | 640 × 356 | 1280 × 713 |

At a screen scale of 1.5 the view is 1920 × 1070 and the clip in CSS pixels gives 1920 × 1070, 1536 × 855,
1280 × 713 and 960 × 534; the clip times the zoom gives 1920 × 1070 at 100, 125 and 200 % and 1919 × 1070 at
150 %. `visualViewport.zoom` and `cssVisualViewport.zoom` were both equal to the page zoom (1, 1.25, 1.5, 2);
the first is the one read, as the operator measured it on Chrome 154 (1, 1.25, 1.5, 2 at 100 to 200 %, images
of 780 × 493, 780 × 492, 780 × 493 and 780 × 493 with the clip times the zoom, in a window of 780 × 493).

How the cut relates to the view, measured on `long.html` (it has a scrollbar), unscrolled and scrolled
by 500 CSS pixels, at the eight combinations, against an unclipped capture of the same view: the image
with the clip times the zoom is the top left crop of the painted view, pixel for pixel (mean absolute
difference 0.0 in 16 of 16), and it differs by 2.1 to 5.1 from the same view stretched to its size. It is
1 pixel narrower than `visualViewport.clientWidth` at a screen scale of 1.5 (1896 against 1897) and as wide
as the view at a scale of 1. The unclipped capture is wider than the view by the scrollbar (1280 against
1265 at a scale of 1); `clientWidth` is the width without it, which is what the clip covers.

Why a guard and not a fallback: a zoom read as 1 when it is missing, or an image taken for good because the
server's own arithmetic says it should be, is the defect that was measured (0 % of the field covered, `capture`
`ok`). The size of the image is the one thing that is checked against what the browser states about the
view, and it fails closed.

Controls, all of them also run on a defective version that must fail:

- `pytest -q tests/test_perception_zoom.py` (91 tests, no browser, on a simulated page that answers at a
  zoom, `tests/fixtures/zoomed_page.py`): the clip is the offset and size times the zoom for the four zooms
  and two scales; the mask covers the field and a block of a colour found nowhere else shows where the
  browser paints it; the check fails on a clip whose offset is not multiplied and on a clip without the
  zoom; with the size guard removed, a clip without the zoom publishes a misplaced mask; a zoom that is
  absent, 0, negative, NaN, infinite, text, null, a boolean or a list gives no image and no screenshot
  call; an image 3 pixels from the view on either axis, in either direction, is withheld and one 2 pixels
  from it is not; bytes that are not an image give no image; a browser that goes wrong three ways in a row (a
  zoom left out, a zoom of 0, an image of the wrong size) never publishes one; a zoom that changes between
  the two readings withholds the image, and the check fails on a reader that sees the same zoom twice; the
  evidence image is cut the same way.
- `pytest -q -m browser tests/test_zoom_integration.py tests/test_zoom_scrolled_integration.py` (40 and 64
  tests): the six cases of the mask test pass without the expected failure; a new test requires that the
  image be published, because the old one passes when none is; on a page scrolled so that the password field
  is near the bottom of the view or in the middle of it, at the four zooms and two scales, the block of the
  colour is where the browser paints it, the mask covers the field and goes no further than 2 pixels beyond
  it, and no edge of the mask lies more than a pixel inside the field. The same check fails on a clip whose
  offset is not multiplied by the zoom (every case at a zoom other than 100 %), on a clip without the zoom
  (the image is withheld, `capture` `unavailable`), and, with the guard removed, on that clip (a misplaced
  mask is published) and on a zoom read as 1.
- Against the module as it was at the start of the session, the three checks of the image fail at every
  zoom other than 100 % (18 of 24 runs; the 6 at 100 % pass).

Rejected: refusing every image when the zoom is not 100 % (the zoom of a site is kept from one session to
the next, and the control is possible); keeping the advice to leave the zoom at 100 % as the only protection
(it rests on the operator); a wider tolerance than 2 pixels for the size; reading an absent zoom as 1.

### A box is placed with the view the browser states, not with the size of the image

The image is a crop of the view, not the view stretched to its size, and it can be a pixel short of it
(above). `render` placed a box with the ratio of the image to the view (`image.width / viewport.width`),
which is a little smaller than the browser's ratio when the image is a pixel short: a box at the far edge
moved toward the origin by up to a pixel. Measured with the real server, on a page whose password field
ends 24 CSS pixels from the right edge of the view, scrolled so that the field is near the bottom, at a
screen scale of 1.5 (the image is reduced to 1600 pixels; deviations of the mask's edges from the painted
field in pixels of the published image, negative on the right edge meaning the mask stops inside the field):

| zoom | right edge before | right edge after | left edge before | left edge after |
|---|---|---|---|---|
| 100 % | −0.46 | +0.54 | −0.98 | −0.98 |
| 125 % | +0.13 | +0.13 | −0.77 | −0.77 |
| 150 % | −0.27 | +0.73 | −1.55 | −0.55 |
| 200 % | −0.08 | +0.92 | −1.12 | −0.12 |

Before, 3 of the 4 right edges stopped inside the field (by under half a pixel here; the error is the
distance of the field from the origin times 1/1897, so it would reach a pixel at the far edge: computed, not
measured). After, none of the 16 placements measured (the two vertical places, the four zooms, the two
scales) has an edge inside its field; at a scale of 1 the two were the same (at most 1 pixel outward, by the
rounding that always goes outward). On a first version of the page, whose field ended at the right edge of
the view, the right edge of the mask stopped 0.84 pixel short of the box before and after: that is the column
the image lacks (1 pixel at full size), which no mask can cover.

Decided: `render` takes `device_view`, the size of the view in device pixels from `visualViewport` of the
same metrics (`_device_view`), and places boxes with its ratio to the view in CSS pixels. Without it, the
image is taken for the view as before. Control: `pytest -q tests/test_perception_zoom.py`, a check on four
boxes whose far edges fall half way between two device pixels, with an image a pixel short on one axis or
both: placed with the view the browser states, every mask covers its box; placed with the size of the image,
the four are a pixel short (the check fails).

Rejected: passing `_device_scale` (the factor used for the snapshot) as the ratio. Tried first, it passes
the same measures, and it breaks `test_audit_catches_a_mask_that_misses_its_field_when_the_factor_is_ignored`
of the earlier session: when that test makes `_device_scale` return 1, the boxes and the ratio are wrong
together and the mask lands right by chance, so the test no longer sees the defect it stands for. The size of
the view is a second statement of the browser that does not share the defect. Not changed: the rounding of
the height at the reduction to 1600 pixels (`round(height × scale)`), under half a pixel at the bottom edge
of the image at the most; no inward edge was measured.

### What the guide and the README said about clicks at a zoom

They said that the clicks are right at a page zoom. The only basis in the repository is the probe of the
earlier entry (24 of 24, no test). The sentence is gone; the documents say the probe was made and that no
test replays it. Not measured now either.

### What was not measured

The zoom made by the Ctrl + keys in a browser that is not headless (the protocol has no command for a zoom;
the tests write the default zoom of the profile, as before); a screen whose scale really is 1.5; the tests of
this entry on Chrome 154 (the operator measured the size of the cut there, the browser tests were not
replayed). The command for the operator: `pytest -q -m browser tests/test_zoom_integration.py tests/test_zoom_scrolled_integration.py tests/test_scale_integration.py`.

### Three repairs of tests

The three modifications of existing tests that were allowed, and no other: the expected failure of the mask
test is gone (with the sentence of its module header that spoke of a known gap); the `complete()` probe of the
two download tests; the racy counter-proof test is replaced.

The probe. The two download tests listed the download directory and read a file in it; the browser renames
`<id>.crdownload` to its final name, and the read raised `FileNotFoundError`, an error of the probe. The read is
now one helper (`holds` in `tests/fixtures/download_probe.py`) that answers false when the file is not there or
goes away; the size or the content the tests wait for, and their delay, are as before. Tested without a
browser (`tests/test_download_probe.py`): a file that goes away between the listing and the read gives false and
does not raise; a file of the right size or content gives true; one of the wrong size or content gives false;
the same check on a probe without the protection fails with `FileNotFoundError`. Each test alone, in a new
process, 200 times: `test_a_download_begun_by_the_click_ends_in_the_volume_after_the_setting_is_closed` 0
failures in 200, `test_a_download_that_begins_a_moment_after_the_click_ends_in_the_volume` 0 failures in 200
(before the change to the launcher below); and again on the final head, with the options of the launcher below, 0
failures in 200 for each. The earlier entry's reopening condition (a failure on Chrome 154, or three in 130
here) did not occur.

The counter-proof. `test_counter_proof_without_the_wait_a_download_that_begins_late_is_refused` closed the
setting at once after the click and expected a download that begins 0.2 s later to be refused. Alone, 200
runs: 4 failures (2 in 100 with a trace, 2 in 100 without; the earlier entry had 4 in 80 before the change
and 4 in 80 after). The trace (a plugin outside the repository that stamped the browser's download events,
the server's calls on the setting, the click and the files of the volume with their modification times, on
one clock): in 10 of the 100 traced runs the browser announced the download (`Browser.downloadWillBegin`),
always after the server had sent the call that closes the setting; in 8 of them it refused the download (no
progress, no file); in the other 2 (runs 35 and 98) it had already written the file: its modification time
is 624.83 and 603.24 ms on the clock, while the server saw the end of the load at 627.08 and 604.08 ms and sent
the closing at 628.00 and 604.57 ms (the file is 2.3 and 0.8 ms before the end of the load, 3.2 and 1.3 ms
before the call). In the 100 runs the call left 0.14 to 0.92 ms after the end of the load and took 0.68 to
6.07 ms to be answered. The page's response and the end of the load come together, so the browser can choose
before the server can close: that a close made at once refuses is not true of a real browser, and the test's
premise (its comment said the refusal did not depend on a race) was wrong.

Decided: the test is removed, and the property it stood for is tested where the browser's timing is not in
play, on the simulated browser that chooses after a set delay (`tests/test_download_acknowledgement.py`): a
download announced after the setting is closed is refused (no wait, nothing in the volume); one announced 50
ms after the gesture, with the wait, is taken up; and the check of the refusal, run on a manager that never
closes the setting, sees the download taken. Rejected: a retry around the test, a longer wait, or an
assertion that tolerates one file (each hides what the measure shows).

The launcher. `tests/fixtures/browser_launcher.py` (the launcher of the tests; nothing else changes) now
starts the browser with `--disable-background-networking`, `--disable-component-update` and
`--disable-sync`, and with `--no-proxy-server` and `--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE
127.0.0.1`. The environment's proxy counts the connections it refuses, shown after a command: for one run of
the 188 browser tests, 202 before any change; 181 with the three options asked for, which ask the browser
not to connect and left two attempts at each start (`www.google.com:443` and `android.clients.google.com:443`;
tried and not removed by `--no-pings`, `--disable-domain-reliability`, `--metrics-recording-only`, the
phishing-detection and Safe Browsing options, `--disable-default-apps` and `--disable-features` with a list
of services); 3 with the last two options added, all three from `scripts/smoke_test.py`, which has its own
launcher and is not touched. Resolving nothing but `127.0.0.1` is by itself not enough with a proxy in the
environment (the proxy resolves the name), hence both options. One run of each count; the variance was not
measured. The 188 tests pass with the options. Rejected: touching the launcher of `scripts/smoke_test.py` or
the browser options of the operator's own launch.

### What is done, with the command that shows it, and what remains

Done (Chromium 141.0.7390.37; Python 3.12.3 on a fresh clone of the branch, with `requirements.lock`):

- The cut is in CSS pixels times the zoom, the image is checked against the view, the zoom is compared at
  both readings: `pytest -q tests/test_perception_zoom.py` (91 passed, no browser).
- The six cases of the mask test pass, and the scrolled page holds at every zoom and scale:
  `pytest -q -m browser tests/test_zoom_integration.py tests/test_zoom_scrolled_integration.py` (104 passed,
  none expected to fail).
- The masks are placed with the view the browser states: the same file, and
  `pytest -q -m browser tests/test_scale_integration.py` (the earlier counter-proofs still fail where they must).
- The two download tests no longer fail on the rename: `pytest -q tests/test_download_probe.py` (2 passed);
  0 in 200 for each alone, twice. The replacement of the racy test:
  `pytest -q tests/test_download_acknowledgement.py` (9 passed).
- `pytest -q -m "not browser"`: 1547 passed, 188 deselected. `pytest -q`: 1735 passed in 161.98 s.
  `python -m compileall -q src`: no output. No method was added to `ALLOWED_METHODS`; the probes of this entry
  used only methods of the list (`Page.getLayoutMetrics`, `Page.captureScreenshot` with and without a clip,
  `Runtime.evaluate`), on a connection of their own.

Remains:

- Replay on Chrome 154: `pytest -q -m browser tests/test_zoom_integration.py tests/test_zoom_scrolled_integration.py tests/test_scale_integration.py`.
  Complete when the 188 tests of `-m browser` pass there; if the scrolled-page tests report a mask that stops
  more than a pixel inside its field, the rounding of the cut on that version differs from the one measured here.
- The zoom of the Ctrl + keys in a browser that is not headless. Complete when the same measure
  (`visualViewport.zoom`, the size of the image, the mask over the field) is made after pressing the keys in a
  headed browser and agrees with the profile's default zoom, or differs and is written down.
- A test that replays a click at a page zoom. Complete when `tests/` has a browser test that clicks a marked
  element at the four zooms and two scales and reaches it (the probe of the earlier entry did, 24 of 24).
- A screen whose scale really is 1.5 and not forced by `--force-device-scale-factor`. Complete when the
  scale tests are replayed on one.

## 2026-10-04 — A click lands on its target at every page zoom; the node lookup of a click reads a point of the document

Measured on Chromium 141.0.7390.37 (headless, as root in a container, Python 3.13.14 and 3.12.3). No browser was on
the `PATH` (`command -v chromium chromium-browser google-chrome google-chrome-stable brave-browser` printed nothing,
exit code 1). This environment provides a Chromium outside the `PATH`; a link named `chromium` on the `PATH` of the
session's shell, outside the repository, let the launcher of the tests find it, and the launcher is unchanged.
Chrome 154 was not available here: what the operator reported on it is quoted as such, and what was not replayed on it
is said under each heading.

Starting point: `realisation/p9-masque-et-zoom` at `919ed8c`, as the brief said; the environment named another branch and
the brief was followed. On that revision here: `pytest -q -m "not browser"` 1547 passed and 188 deselected (as root, so
the test that runs as root only passes), and `pytest -q -m browser` 188 passed in 116.51 s. The operator's own measure of
the same revision, as given with the brief: 1546 passed, 1 skipped and 188 deselected without a browser; 125 passed in
the three zoom and scale files and 188 of 188 browser tests on Google Chrome 154.0.8037.97 (Debian, Python 3.13.5); the
two download tests and the two tests of `tests/test_download_acknowledgement.py` that replace the unstable one, each
alone, 0 failures in 40 runs; with the cut of the screenshot made to leave the zoom out, 30 of the 104 cases of the two
zoom files failed.

### The click had no test; the first one found that it did not work on a scrolled page

The earlier entry recorded a probe that reached the right element 24 times out of 24 at the eight combinations of page
zoom and screen scale, and said no test replays it. That result agrees with what follows: every attempt that needs no
scroll passed on the server as it was. The new test, `tests/test_click_integration.py`, clicks by `Actions.click`, on the rig of
`tests/test_actions_integration.py` (the class and the fixture imported, with its `launched` fixture replaced by the
browser of the zoom and scale under test), the buttons of `tests/fixtures/pages/click-targets.html`: a button of 120 by
32 CSS pixels at (320, 500), one of 16 by 16 between two decoys of its size 4 pixels away on each side, and one placed at
2.5 heights of the view down (`250vh`, so at least two heights at every zoom). The page counts the clicks of each button
and, apart, the clicks that reach none (`window.__clicks`, `window.__stray_clicks`); a separate session reads them. A
click landed when its target received exactly one click, no other button any, none fell outside a button, and the click
returned no error (`assert_landed`, a function of those counts alone, `tests/fixtures/click_targets.py`). The fourth
case is the large button after the page was scrolled to put it in the middle of the view, before the observation.

On the server as it was (the production file of `919ed8c`), 8 combinations by 4 cases, 20 of the 32 attempts failed:

| case | what it is | failed at | passed at |
|---|---|---|---|
| `small` | 16 by 16, between its decoys; in view at every zoom | none | 8 of 8 |
| `large` | 120 by 32 at (320, 500) | 150 % and 200 %, at both scales (4) | 100 % and 125 %, at both scales (4) |
| `far` | at 2.5 heights of the view | 8 of 8 | none |
| `large-after-scroll` | `large`, the page scrolled first | 8 of 8 | none |

`large` fails at 150 and 200 % because the view is then 475 and 356 CSS pixels high and the button ends at 532: the
server has to scroll it in. The 12 attempts that passed are the 12 that needed no scroll, which is why the probe saw
24 of 24. Nothing was clicked in any failure: the server fails closed, but on a scrolled page it could not click.

What the browser said at a zoom of 100 % and a scale of 1, from a trace added for one run to `_click_at` and removed:

- `far`: quads `[320, 356.5, 440, 356.5, 440, 396.5, 320, 396.5]`, point (380, 376), scroll offset (0, 1426), the
  greatest the page allows. `DOM.getNodeForLocation(380, 376)` answered `No node found at given location`; the
  resulting `CdpError` left `Actions.click` (the exit point reports an exception it does not expect as
  `internal_error`; that was not run here). `document.elementFromPoint(380, 376)` gave `BUTTON#far`.
- `large-after-scroll`: quads `[320, 340, 440, 340, 440, 372, 320, 372]`, point (380, 356), offset (0, 160). The lookup
  gave node 8, the `body`; the target is 9: `element_obscured`.

Which point the lookup reads, on probes with a connection of their own (outside the server's list):

- A scan down a column at a scroll of 150 CSS pixels: the lookup says `small` for y in [310, 326), the box of the button
  in the document, at the five combinations scanned (100/1, 125/1, 150/1, 125/1.5, 150/1.5), where the box lies at view
  y [160.00, 176.00), [159.60, 175.60), [160.00, 176.00), [160.13, 176.13) and [159.78, 175.78). It is a point of the
  document in CSS pixels, at fractional zooms and scales too.
- On a page of 4000 by 2000 CSS pixels scrolled to (1300, 700), at the eight combinations: the view point gave no node
  8 times of 8, the view point plus the scroll offset, rounded once, gave the right button 8 times of 8, and the view
  point with only x moved gave no node 8 times of 8. Both axes are document coordinates.
- `cssVisualViewport.pageX` and `pageY` give the scroll offset exactly (1300.27, 700.27 at 125 % and 1.5);
  `cssLayoutViewport.pageX` and `pageY` are whole numbers (1301, 701). The server's `_viewport` reads the first.
- A first probe passed a point that was not a whole number (318.4) and did not find the button at the three combinations
  where the scroll offset is fractional (125/1, 125/1.5, 150/1.5); the cause was not examined, the server rounds its point.

The old reading was not only a refusal. On a page scrolled by 100 CSS pixels each way, with a banner fixed to the view
over the middle of a tall button, the server asked about the document point (300, 250), which lies on the button and
under no banner, accepted, and the click landed on the banner. That is `test_a_server_that_asks_for_the_node_at_the_view_point_clicks_the_banner_over_a_covered_button`, 8 of 8 combinations.

### Decided: the lookup is asked at the point of the document, and the offset is read strictly

Decided (production, `actions.py`): in `_click_at`, `DOM.getNodeForLocation` is asked at the middle of the box plus
the scroll offset of the view, rounded once; the mouse events keep the view point (with the offset in the lookup only,
the click lands in all 32 attempts of the first page and the 8 of the page scrolled in both directions). The offset is
`Actions._viewport`, which already read it for `scroll`, now with `strict=True` for the click: an offset that is
missing, null, a boolean, text or not finite is refused with `element_obscured` and a message that says the position
of the page could not be read, before any lookup or mouse event. The scroll keeps its lenient reading (an absent
offset reads 0, enough to see whether the view moved).

Why: the lookup answers for a point of the document and the box and the mouse events are in the view; on a page that is
not scrolled they coincide, which is all the earlier probe could see. Why strict: a click that cannot place its lookup
does not know what lies under the point it would click; reading 0 would ask at the view point again, silently.

The contract does not change: `Page.getLayoutMetrics` is already in `ALLOWED_METHODS` (nothing was added to it), no
error code, no shape of a result. The cost is one more `Page.getLayoutMetrics` per click.

Controls, each also run on a defective version that must fail:

- `pytest -q -m browser tests/test_click_integration.py` (264 tests). Against the production file of `919ed8c`, 36 of
  them fail: the three tests of the real path, 20 + 8 + 8 (the four cases, the page scrolled in both directions, the
  covered button); the 228 others, the counter-proofs included, pass. With the fix, all 264 pass.
- The counter-proof of the fix: the same check on a server whose offset reads 0 fails exactly where the page ends
  scrolled (20 of the 32 attempts of the first page, all 8 on the page scrolled in both directions) and passes where it
  does not; with the defect neutralised (the "defective" server reading the real offset) the 20 turn into
  `DID NOT RAISE`. A server that leaves out only the horizontal offset fails on the page scrolled in both directions, at the 8
  combinations.
- `pytest -q tests/test_click_lookup_point.py` (20 tests, no browser, so that the suite the continuous integration runs
  guards the fix): a simulated browser whose node lookup is wrapped to answer for a point of the document, as measured,
  on four pages (not scrolled, scrolled down, across, both ways); on the production file of `919ed8c` the three
  scrolled ones fail. A missing, text, null or boolean offset gives `element_obscured` with neither the lookup nor a
  mouse event sent; with the strict reading ignored, a missing, text or boolean one clicks.

Rejected: a lookup by `document.elementFromPoint` in an internal script (it reads a point of the view by the DOM
specification and would need no offset, but by that specification it does not enter frames, which was not tried, and it
replaces the method the written design of the click names; kept as the alternative if the lookup reads another point
on Chrome 154); adding the offset to y alone (x was measured to be a document
coordinate too, and a test fails on it); fixing the page or the test instead of the server.

Not measured: a click on an element inside a frame of a scrolled page; the two reads (the box, then the offset) are not
one atomic call, as the box and the lookup were not, and a page that scrolls itself between them was not tried; Chrome
154. If the lookup reads a point of the view on a version, the tests of the real path fail there for every scrolled
case, and so does the counter-proof that neutralises the offset.

### The tests

Pages and checks (`tests/fixtures/click_targets.py`, `tests/test_click_targets_page.py`, 51 tests, no browser): the page
is held to what the browser tests rely on (names, counters, sizes, places, no reference to anything outside it), each
check with variants that have the defect and are refused; the check that a click landed is refused on a click that
missed, doubled, hit a decoy, hit the page, or came back with an error.

The wrong points (`tests/test_click_integration.py`): a raw session sends the click to the point of the box, which it
reads as the server does, multiplied by the zoom, divided by it, and multiplied by the screen scale. At every
combination where the factor is not 1, 64 attempts, the click reached the page and no button (64 of 64) and the check
refused it; where the factor is 1 (32 attempts) the point is the right one and the check accepts it, and the raw
clicker at the point of the box lands at all 32 (case by combination), so that "all the wrong points fail" is not a
clicker that always misses. A click one button away from the small target (20 pixels either side) lands on the decoy
there, at the 8 combinations: the decoys are live.

Decisions, with their reason, their control and what was rejected:

- The large button is below the middle of the view. Two requirements pull one button apart: at 100 % the view is 713 CSS
  pixels high, so the button has to lie below 357 to be brought to the middle by a scroll; at 200 % it is 356, so it has
  to lie above to be wholly in view. Control: `test_the_target_page_is_laid_out_at_every_zoom_and_scale_as_the_click_checks_assume`
  and the premise of the scrolled case, which fails when the page is not scrolled. Rejected: keeping it at 310 and
  scrolling as far as the page allows (the offset is 0 at 100 %, and the offset is the point).
- `scrolled` of `tests/test_zoom_scrolled_integration.py` is not imported, as the brief said it would be: it scrolls to
  the element `password`, which the page must not have. `scroll_to_the_middle` repeats its method (scroll, wait until
  the position stops, then check where it stopped). Rejected: a `password` field in the page; patching `scroll_script`.
- `pytest.raises(AssertionError)` wraps `assert_landed`, a function of the counts, and not the whole attempt; the
  browser work and the premises (the zoom and the scale reached the page, the page is scrolled) run outside it. Reason:
  inside, a premise that fails or a probe that errors would make a counter-proof pass for the wrong reason.
- The observation of the click tests is made without an image (`Rig.observe`): the image is not part of the click, and
  the zoom files cover it.
- A second page, `tests/fixtures/pages/click-scrolled.html` (two buttons and a banner fixed to the view, shown by the
  address), is beyond the three buttons the brief listed. Reason: the fix touches the check that keeps a click from
  landing on what covers its target, and that check, and the horizontal offset, had to be shown to hold. Rejected:
  stopping at the three buttons and leaving the change to that check unguarded.
- `server_click` turns any exception of `Actions.click` into data for the check (the code of a `ToolError`, else the
  name of the exception), because the defect let a `CdpError` out; the mark is looked up before, outside the `try`, so a
  missing mark still fails as itself.
- The probes use the connection of `tests/fixtures/raw_cdp.py`, which the server's list does not bound. Among the methods
  they call, `DOM.querySelector` is not in `ALLOWED_METHODS`; `Runtime.evaluate` is, but with scripts of their own.
  Nothing was added to the list.

### Findings outside the click

- The observation leaves out a node that has no box of its own, with all that lies under it (`_walk` in `perception.py`,
  written so). Measured on Chromium 141: a page whose body has no height because its children are placed out of the flow
  gave `omitted_not_visible: 1` and no mark, though its buttons were on screen (the first draft of the click page did
  that, and its body now has a height of its own); and a page with a wrapper of no height holding a dialog fixed to the
  view,
  `<div id="modal-root"><div role="dialog" aria-label="Dialog" style="position:fixed;left:200px;top:200px;width:300px;height:120px"><button type="button" style="position:absolute;left:20px;top:20px">Confirm</button></div></div>`
  after a paragraph, gave `marks: 0`, and the same page with `style="height:40px"` on the wrapper gave `marks: 1`. This
  is not changed here: it is a decision of the perception, outside the click. How often real sites do it is not
  confirmed. The operator decides; the guide lists it among the known limits.
- The simulated browser of the action tests (`tests/fixtures/fake_cdp.py`, `hit`) reads the point of the node lookup as
  one of the view, which is the reading that was wrong. No existing test clicks on a scrolled simulated page, which is
  why they passed; they are unchanged. `tests/test_click_lookup_point.py` wraps the lookup instead of changing the
  fixture. A future test of a click on a scrolled simulated page in `tests/test_actions.py` has to take this into account.

### What is done, with the command that shows it, and what remains

Done (Chromium 141.0.7390.37; Python 3.12.3 on a fresh clone of the branch, with `requirements.lock`):

- A click of the server lands on its target at the four zooms and two scales, on a page that needs no scroll, one that
  needs the click to scroll, and one scrolled first, in both directions, and a covered button is refused:
  `pytest -q -m browser tests/test_click_integration.py` (264 passed).
- The page and the check have their own tests: `pytest -q tests/test_click_targets_page.py` (51 passed, no browser).
- The node lookup of a click is guarded without a browser: `pytest -q tests/test_click_lookup_point.py` (20 passed).
- `pytest -q -m "not browser"`: 1618 passed, 452 deselected in 72.08 s. `pytest -q`: 2070 passed in 271.63 s (Python 3.12.3, fresh clone at `0619fa3`). `python -m compileall -q src`: no output.
  No method was added to `ALLOWED_METHODS`.

Remains:

- Replay on Chrome 154: `pytest -q -m browser tests/test_click_integration.py`, then `pytest -q -m browser`. Complete when
  the 452 tests of `-m browser` pass there. If the real-path tests fail on every scrolled case, the lookup reads another
  point on that version; the alternative is the lookup by an internal script (above).
- The zoom of the Ctrl + keys in a browser that is not headless, and a screen whose scale really is 1.5: the click tests
  have the same two limits as the zoom tests. Complete when the click tests are replayed after pressing the keys in a
  headed browser and on such a screen.
- A click on an element inside a frame of a scrolled page. Complete when a browser test scrolls `iframe.html` and clicks
  an element of its frame, and either lands or is refused.
- A page that scrolls itself between the read of the box and the read of the offset. Complete when a browser test makes
  the page scroll at that moment and the click is refused or lands, never lands elsewhere.
- The rule of the observation about a node with no box. Complete when the operator has decided to keep it or to change
  it, and a test holds the decision on the two pages above.

## 2026-10-04 — A click is checked again after the pointer moved, before it is pressed

Point of departure: `4997ee1`. Chromium 141.0.7390.37 (headless, as root with `--no-sandbox`, a link named `chromium`
outside the repository), Python 3.13.14 with `requirements.lock`; Chrome 154, on which the operator replays, was not
available here.

### What was measured before the change

The click checked what lies under its point once, before its first mouse event, then sent `mouseMoved`, `mousePressed`
and `mouseReleased` to that point. A new page, `tests/fixtures/pages/click-shifting.html`, moves each of the three
targets (large, small between two decoys, far) 60 CSS pixels down at the first `mousemove` of the document, once, and
shows a button of another name where the target was. On `4997ee1`, by `Actions.click`, at the eight combinations of page
zoom (100, 125, 150, 200 %) and screen scale (1, 1.5): 24 of 24 attempts returned no error and the intruder received the
click (`large received 0 clicks ... large-intruder received 1 clicks`, the same for `small` and `far`, eight times each),
the page having moved its target in every attempt (read apart, `marginTop` of the target is `60px`).

### The change and its decisions

`actions.py` only. After `mouseMoved` and before `mousePressed` the click reads the box of the target and the scroll
offset again (`_recheck_before_press`); if either differs from what the first check used, it asks
`DOM.getNodeForLocation` again at the view point it is about to press, plus the offset read again, and refuses with
`element_obscured` unless the node is the target or one of its descendants. If the box can no longer be read the click is
refused the same way. The press and the release go in a second call of `_gesture` marked `started`, so that a dialog
opened by `mousePressed` stays a notice and not a refusal, as before; a dialog opened by the movement ends the click.

- **Refusal, not a second aim.** Reason: the point the click was verified for is the one it presses; moving it to the new
  place of the target would send a click to a point the operator never saw checked, and the server has no way to say the
  page moved on purpose. After a refusal a click on a new observation lands (tested at the 24 combinations). Rejected:
  pressing at the target's new centre.
- **The second lookup is conditional on a change.** `tests/test_click_lookup_point.py` asserts that a click on a static
  page asks `DOM.getNodeForLocation` exactly once, and the set of internal scripts is fixed by `tests/test_cdp.py`; neither
  test could be edited, and no script could be added. An unconditional second lookup failed 7 of the existing tests
  (`asked == [one point]`, `(160, 115)` twice) and nothing else. So the lookup is repeated only when the box or the offset
  changed. Cost: a target covered by something that opens over it without the target or the view moving is not caught.
  Proposed alternative, for the operator to decide: make the second lookup unconditional and change that one assertion to
  read the first lookup only; it removes the residual above. Rejected: an internal script for `document.elementFromPoint`
  (it changes the set `test_cdp.py` pins, and it replaces the method the click names).
- The two extra readings (`DOM.getContentQuads`, `Page.getLayoutMetrics`) are the same methods the click already calls.
  No method was added to `ALLOWED_METHODS`, no error code, no result form changed; the gate, the confirmation, the download
  check and the journal of `_gesture` are untouched.

### What was measured after

- `pytest -q -m browser tests/test_click_shifting_integration.py`: 80 passed (24 land-or-refuse; refusal, nothing clicked,
  at the eight combinations; 24 second tries after a refusal; 24 counter-proofs). All 24 attempts are refused, none lands.
- The counter-proof neutralises `_recheck_before_press` and runs the same attempts: at the 24 combinations the click
  returns no error, lands on the intruder, and the check raises `AssertionError`.
- `pytest -q tests/test_click_moving_page.py`: 6 passed, no browser: a simulated page that shifts its target or scrolls at the
  first `mouseMoved` gets a refusal and no `mousePressed`; a server without the check fails the same test; a static page is
  pressed and looked up once; an element with no box after the movement is refused.
- `pytest -q tests/test_click_shifting_page.py`: 30 passed, no browser: the page contract (13 broken variants refused) and
  the judgement of a click (11 wrong outcomes refused, 4 right ones accepted).
- `pytest -q -m "not browser"`: 1654 passed, 532 deselected in 83.65 s. `pytest -q -m browser`: 532 passed in 264.20 s.
  `pytest -q`: 2186 passed in 346.46 s. `python -m compileall -q src`: no output.

### A page that moves without stopping, measured, not a test

`click-targets.html` plus the script `setInterval` every 4 ms, `scrollBy(0, 4 * direction)`, the direction reversed at both
ends (1000 CSS pixels a second); six attempts per combination and button, 144, by `Actions.click`, a fresh tab each. A
result is "landed" (no error, one click on the target, nothing else), "refused" (`element_obscured`, nothing clicked) or
"no error, nothing landed on the target".

| | landed | refused | no error, click elsewhere or lost | landed on another button |
|---|---|---|---|---|
| `4997ee1` | 64 | 0 | 80 | 0 |
| after | 55 | 82 | 7 | 0 |

By button, after: large 13 landed, 31 refused, 4 lost; small 0, 47, 1; far 42, 4, 2. The 7 that remain are clicks the page
moved in the instant between the second reading and the press; the "elsewhere" in every case was no button. This is
the window that is left, and its order of magnitude on this machine.

### Latency

Median of 20 clicks on `click-targets.html` at zoom 100 % and scale 1, by `Actions.click`, the pacer on a fake clock so
that its delay is not counted: 318.41 ms on `4997ee1`, 318.42 ms after (min 315.74 / 315.67, max 330.53 / 331.25). The
figure is almost entirely the 0.3 s the call waits for a load a click may start (`settle_start_s`); the two extra readings
are not measurable against it. No surcharge above 100 ms.

### Not changed, and why

`perception.py` and the visibility rule are untouched. The test double `fake_cdp.py` is untouched; `test_click_moving_page.py`
makes its page move by wrapping the mouse handler. The probes of the integration tests read through
`tests/fixtures/raw_cdp.py` (`DOM.querySelector` is not in the list).

### What remains

- Replay on Chrome 154: `pytest -q -m browser tests/test_click_shifting_integration.py`, then `pytest -q -m browser`.
  Complete when the 532 pass there.
- The zoom of the Ctrl + keys in a headed browser, and a screen whose scale really is 1.5: they need a person and a screen.
- The unconditional second lookup (above). Complete when the operator has decided whether to loosen the one assertion of
  `test_click_lookup_point.py` that counts lookups, and a test holds a target covered without moving.
- The window between the second reading and the press. Complete when the operator accepts the measured 7 of 144, or asks
  for a different gesture (a single dispatch of the three events is not in the protocol the list allows).
