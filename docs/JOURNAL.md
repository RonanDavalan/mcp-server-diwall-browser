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
