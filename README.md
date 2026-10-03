# mcp-server-diwall-browser

An MCP server that gives a language model the eyes and hands of an operator in the operator's own
browser, through the Chrome DevTools Protocol: the model sees the same screenshot and the same
accessibility tree as the operator, and acts in the same authenticated session. It needs a
Chromium-family browser that the operator starts with a dedicated profile and a debugging port
bound to the loopback interface, and an encrypted directory (gocryptfs) for credentials, evidence
and downloads.

## Connect it to an MCP client

The server speaks MCP over standard input and output. With Claude Code:

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE=~/.config/mcp-server-diwall-browser/profile.json \
  -- ~/mcp-server-diwall-browser/.venv/bin/python -m mcp_server_diwall_browser
```

For another MCP client, the generic `mcpServers` block:

```json
{
  "mcpServers": {
    "diwall-browser": {
      "command": "~/mcp-server-diwall-browser/.venv/bin/python",
      "args": ["-m", "mcp_server_diwall_browser"],
      "env": {
        "MCP_DIWALL_BROWSER_PROFILE": "~/.config/mcp-server-diwall-browser/profile.json"
      }
    }
  }
}
```

Adapt `~/mcp-server-diwall-browser` to where you cloned the repository, and the profile
path to where you wrote your operator profile. The variable can be left out when the operator
profile is at its standard place, `~/.config/mcp-server-diwall-browser/profile.json`. The form of
the `claude mcp add` command was checked against `claude mcp add --help`, then run by the operator
on Debian: `claude mcp list` showed the server connected. The tests start the server exactly as
these two blocks do:
`python -m mcp_server_diwall_browser` with `MCP_DIWALL_BROWSER_PROFILE` set.

Everything that comes before (installing, starting the browser, opening the encrypted directory,
writing the operator profile) is in [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md). How it works
and why is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What the model sees

![The demo page as the model receives it](docs/images/observe-demo.png)

The image above was produced by `scripts/smoke_test.py` from the local demo page
(`docs/demo/index.html`). The numbered marks are drawn by the server: an action names an element
by its mark. The password field, which the page filled itself, is masked by a solid rectangle, and
its value is withheld from the text the model receives (`redacted_fields` is 1).

## What is tested, what is not

Tested:

- The full suite runs in this repository's test environment (Ubuntu 24.04), with Python 3.13.14
  and, with its dependencies from `requirements.lock`, with Python 3.12.3: without a browser, 1451
  tests pass and the 125 that need a browser are skipped with the reason; with Chromium
  141.0.7390.37 (headless, blank temporary profile), 1570 pass and 6 are expected failures that
  record a known gap (the page zoom, in the limits below).
- Download tests: two failed at random, 7 and 8 times in 30 runs of each alone, because the server
  closed the download setting before the browser had chosen what to do with the download. After the
  change they failed 3 and 1 times in 130 runs of each alone; the 3 are a read, in the test itself, of a
  file the browser renames. A third test, `test_counter_proof_without_the_wait_a_download_that_begins_late_is_refused`,
  fails about 5 times in 100 alone, on the code before the change as on the code after it. The
  measures and what remains are in `docs/JOURNAL.md`.
- The operator replayed the suite on Debian with Chrome 154.0.8037.97, before the three changes
  below: 1258 passed and 1 skipped (a test that runs as root only), the 54 browser tests included.
- `scripts/smoke_test.py` returns 0 against Chromium 141 and 3 when no browser is found.
- The marks and the masks at a screen scale of 1, 1.5 and 2, on a headless Chromium 141: the box of
  every mark in view lies within two pixels of the box the browser itself gives, every pixel of the
  password field of the demo page is under its mask, and the mask goes no further than two pixels
  beyond it (`tests/test_scale_integration.py`). Before the correction of this, a mask did not
  cover its field at a scale above 1. A real screen at 1.5 was not measured. The page zoom was, and
  it does not hold for the image (see the limits).
- A dense page with a consent banner fixed over its lower part (643 things to click): its three
  buttons are marked and listed first in the tree, the elements under it say `covered`, and a click
  on "Refuse" by its mark takes the banner away (`tests/test_ranking_integration.py`).
- The command that carries an old credential file over (see the operator guide): on files of the
  test suite in a simulated volume, with a refusal tested for each way out, and no value in
  anything it prints. It was not tried on a real gocryptfs volume.
- A real gocryptfs 2.5.1 volume, a throw-away one made for the check (not the operator's own),
  mounted on Debian: `status` read `mounted` and `profile_encrypted: true`, and the server wrote
  its journal and created `downloads/` in it. Opera 136 (Chromium 152), started with its profile
  in that volume and a debugging port on the loopback interface, was attached; the server opened
  a page of a public shopping site, filled its search field, pressed Enter and observed the
  results. The typed text does not appear in the journal.
- Claude Code, started from a desktop terminal: `status` through Claude Code read
  `confirmation_channel: native`, so the display reached the server.
- The server process over standard input and output: it writes nothing but the protocol on its
  standard output, holds no listening socket and runs with a core file limit of zero.

Not tested:

- A real `kdialog` or `zenity` window: the tests use fake programs in their place.
- The operator's own gocryptfs volume: the tests simulate the mount table and the directory, and
  the check above used a throw-away volume.
- Brave, and Google Chrome builds other than the one the operator ran.
- The confirmation asked through a desktop MCP client (elicitation): tested with the client of
  the MCP SDK only.
- Chrome 154 with its profile kept inside the operator's own encrypted directory.
- A refusal in the confirmation window, answered by a person.

Known limits:

- While the server is attached to the browser, downloads are refused in every tab, the operator's
  own tabs included, except in the short window around one of the server's clicks or Enter keys.
  To download a file yourself, stop the server or the MCP client; downloads work again as soon as
  the server's connection ends.
- A download that a page starts more than half a second after a load ends is refused.
- Accepting a dialog whose message asks for a payment, a deletion or a secret change is always
  refused: no evidence can be taken while a dialog is open. The operator answers such a dialog.
- Visibility is approximate: the server does not compute contrast or `overflow` clipping, so text a
  human cannot read can still reach the model.
- An observation holds at most 150 marks and 400 tree lines (`marks_truncated`, `tree_truncated`).
  Past that, the server keeps first what is in a layer above the page (a dialog, a modal node, a
  fixed or sticky node painting over content in view, such as a cookie banner), listed under an
  `overlay` line in the tree, then what is in view and clear, then what is in view under a layer
  (`covered: true`, and a click on it is refused), then the rest. A banner that overlaps nothing in
  view is not a layer and can still be cut.
- At a page zoom other than 100 % (the browser's Ctrl +), the published image shows another part
  of the page and the masks do not cover their fields: measured at 125, 150 and 200 %, at screen
  scales of 1 and 1.5, on Chromium 141, 0 % of a password field was covered and `capture` still read
  `ok`. The sizes, the boxes and the clicks are right. Keep the zoom of the browser at 100 % until
  this is decided (`docs/JOURNAL.md`). Not measured on Chrome 154.
- The content of frames is never read; frames are masked in the image.
- What the model provider keeps of what it receives is not confirmed by this project.

License: not chosen yet.
