# mcp-server-diwall-browser

**English** | [Français](docs/fr-FR/README.md) | [Deutsch](docs/de-DE/README.md) | [Español](docs/es-ES/README.md)

![The eye of Diwall MCP lights up the web: everything the model can act on gets a numbered frame, and the password field stays black](docs/images/scene-diwall-mcp.png)

An MCP server that gives a language model the eyes and hands of an operator in the operator's own
browser, through the Chrome DevTools Protocol: the model sees the same screenshot and the same
accessibility tree as the operator, and acts in the same authenticated session. It needs a
Chromium-family browser that the operator starts with a dedicated profile and a debugging port
bound to the loopback interface, and an encrypted directory (gocryptfs) for credentials, captures
and downloads.

## Before the first use

The server does not work without an encrypted directory: the profile of the browser it attaches to keeps your
signed-in sessions, and they must not sit on the disk in clear. There is no mode without encryption. After
installing, the first command, which you run yourself in a terminal, is `mcp-server-diwall-browser-setup`: it
reads your machine and ends on the next command to type.

- `mcp-server-diwall-browser-setup create --temporary` makes a volume in memory, for a trial. Closing it, stopping
  the machine or ending your session destroys it, and with it the sessions of the sites you opened in the
  browser, the audit journal and the evidence.
- `mcp-server-diwall-browser-setup create` makes a durable volume.
- `mcp-server-diwall-browser-setup open`, `browser` and `close` open the volume, start the browser on its profile,
  and stop both.

You type the password yourself, at the prompt of `gocryptfs`; the assistant never reads it. You sign in to your sites
yourself, in the browser it starts. After adding the server to your MCP client, restart the client: a session
that was already open does not see it. The steps, the refusals and the manual way are in the section "First launch" of
[docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md).

## Which sites the model reads

The list of sites in the operator profile bounds what the model reads and acts on, never where it navigates. The key
`origin_policy` chooses what the server does with an origin that the list does not hold:

- `strict` (the default, also when the key is missing): the origin is refused, and only the origin is returned.
- `discovery`: the server asks you in a window, once per origin: `Refuser` (the default answer), `Cette séance` or
  `Ajouter à la liste`, which writes that exact origin in your profile.
- `open`: every public site is read and acted on, and its content goes to the provider of the model; local addresses and
  names stay on the list. It is the freest policy and the riskiest, and it is yours to choose.

Payments, irreversible deletions and secret changes keep their confirmation, and a credential is typed only on the exact
origin it is bound to, in the three policies. The details are in section 5 of
[docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md).

## Install

On Linux the server ships as native packages. Download the one of your system from the
[Releases](https://github.com/RonanDavalan/mcp-server-diwall-browser/releases) page of the repository or from the [install guide of the website](https://mcp-diwall.davalan.fr/guides/install/), then install it from the
folder that holds it:

| System | Command |
|---|---|
| Debian 13, Ubuntu 24.04 and 26.04 | `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb` |
| Fedora 44 | `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm` |
| openSUSE Leap 16.0 | `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm` |
| Arch Linux | `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst` |

Installation, the start of the server by an ordinary account and removal are validated in containers on
these systems. A container shows neither the confirmation window, nor the mounting of the encrypted
directory, nor a browser. Debian 12 and Ubuntu 22.04 are not supported: their Python is older than 3.12.
The installation needs a network connection: the package creates a Python virtual environment and
installs the dependencies, at the versions the project tested. The manual page is `man mcp-server-diwall-browser`.
To install from a clone of the repository instead, follow [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md).

A 64-bit ARM machine takes the same `.deb`, since the package does not depend on the architecture: a Raspberry Pi 4 under Debian 13 was tried (see "What is tested, what is not"). The browser there is the distribution's Chromium.

## Connect it to an MCP client

The server speaks MCP over standard input and output. With a package installed, the command is
`mcp-server-diwall-browser` and the operator profile is read from its standard place:

```
claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser
```

From a clone of the repository, with Claude Code:

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE="$HOME/.config/mcp-server-diwall-browser/profile.json" \
  -- "$HOME/mcp-server-diwall-browser/.venv/bin/python" -m mcp_server_diwall_browser
```

For another MCP client, the generic `mcpServers` block. The client does not expand `~`, so this
command prints the block with your home directory in the paths, ready to paste into its
configuration:

```
cat <<EOF
{
  "mcpServers": {
    "diwall-browser": {
      "command": "$HOME/mcp-server-diwall-browser/.venv/bin/python",
      "args": ["-m", "mcp_server_diwall_browser"],
      "env": {
        "MCP_DIWALL_BROWSER_PROFILE": "$HOME/.config/mcp-server-diwall-browser/profile.json"
      }
    }
  }
}
EOF
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

- The full suite, on Debian 13 with Python 3.12.11 and the dependencies of `requirements.lock`: without a
  browser, 2100 tests pass, 1 is skipped (a test that runs as root only) and the 589 that need a browser are
  deselected; with Google Chrome 156.0.8078.47 (headless, blank temporary profile), 2689 tests pass and 1 is
  skipped, with no expected failure.
- Download tests: two failed at random, 7 and 8 times in 30 runs of each alone, because the server
  closed the download setting before the browser had chosen what to do with the download. After the
  change they failed 3 and 1 times in 130 runs of each alone; the 3 were a read, in the test itself, of
  a file the browser renames, which is now one helper that ignores the rename. Since then each of the
  two alone failed 0 times in 200 runs, twice over. A third test, which closed the setting at once and
  expected a late download to be refused, failed 4 times in 200 alone: the real browser can choose
  before the server closes the setting, so the test is replaced by tests on a simulated browser. The
  measures and what remains are in `docs/JOURNAL.md`.
- The operator replayed an earlier revision on Debian (Python 3.13.5) with Chrome
  154.0.8037.97: without a browser 1450 passed and 1 was skipped (a test that runs as root only);
  with it 119 passed and 6 failed as expected, the six of the page zoom, closed since;
  the two download tests alone, 0 failures in 40 runs each. The operator then replayed `ff8c433`
  (the revision before the click tests) on the same browser: without a browser 1546 passed, 1 was
  skipped and 188 were deselected; the three zoom and scale files, 125 passed; all 188 browser tests
  passed; the two download tests and the two tests that replace the unstable one, each alone, 0
  failures in 40 runs. The click tests were not replayed on it.
- `scripts/smoke_test.py` returns 0 against Google Chrome 155.0.8059.84 and 3 when no browser is found.
- A Raspberry Pi 4 Model B with 8 GB, Raspberry Pi OS on Debian 13 (aarch64), Python 3.13.5 and Chromium 155.0.8059.79, on 22 October 2026: the final `.deb` installed by `apt`; the first launch of the setup assistant end to end with the real `gocryptfs` (temporary and durable volumes, browser, close); `scripts/smoke_test.py` returning 0; the server driven through a real MCP client on a visible Chromium with a window, not headless, on a virtual X11 screen, with the native confirmation window of `zenity` opened and left to expire; then the package removed and purged, no file left. The complete suite there gave 2694 tests passed and 1 skipped in 15 minutes (five of them are measurements of that machine that the repository does not hold). On that network, the first page loaded by a freshly started Chromium took 15 to 26 seconds, which made three tests fail in a first run; with the browser cut off from the network, they all pass (guide, section 12).
- The marks and the masks at a screen scale of 1, 1.5 and 2, on a headless Chromium 141: the box of
  every mark in view lies within two pixels of the box the browser itself gives, every pixel of the
  password field of the demo page is under its mask, and the mask goes no further than two pixels
  beyond it (`tests/test_scale_integration.py`). Before the correction of this, a mask did not
  cover its field at a scale above 1. A real screen at 1.5 was not measured.
- The page zoom, at 100, 125, 150 and 200 % and at screen scales of 1 and 1.5, on a headless Chromium
  141 whose blank profile holds the zoom as its default: the image the model receives is the whole
  view, the password field of the demo page is under its mask, and so is the field of a page scrolled
  so that it sits near the bottom or in the middle of the view; the mask goes no further than two
  pixels beyond the field, and in the 16 placements measured no edge of a mask lay inside its field
  (`tests/test_zoom_integration.py`, `tests/test_zoom_scrolled_integration.py`). Before the
  correction, at 125, 150 and 200 %, 0 % of the field was covered and `capture` read `ok`. No image is
  published (`capture` reads `unavailable`, the tree is still served) when the browser gives no usable
  zoom, when the zoom changes during the capture, or when the image is more than two pixels from the
  size of the view on either axis (`tests/test_perception_zoom.py`, which needs no browser). On Chrome
  154 the operator measured the size of the image cut as the browser reads it (the view at each zoom, a
  pixel short in height at 125 %); the zoom tests were replayed on it later (below).
- A click of the server lands on its target at page zooms of 100, 125, 150 and 200 % and screen scales
  of 1 and 1.5, on a headless Chromium 141 whose blank profile holds the zoom as its default. The click
  is the server's own (`Actions.click`, with its gate and its limits); the test page counts what each of
  its buttons receives and a separate session reads the counts. At each of the eight combinations a
  button of 120 by 32 CSS pixels, a button of 16 by 16 between two decoys 4 pixels from it, a button two
  and a half views down, and the large button after the page was scrolled to put it in the middle of the
  view, each received exactly one click, no decoy received any and none fell outside a button. On a page
  scrolled in both directions the click lands on the button put in the middle of the view, and a button
  covered by a banner fixed to the view is refused (`element_obscured`) with nothing clicked
  (`tests/test_click_integration.py`, 264 tests; `tests/test_click_targets_page.py`, 51, and
  `tests/test_click_lookup_point.py`, 20, need no browser). The same check refuses a click sent to the
  point of the box multiplied by the zoom, divided by it, or multiplied by the screen scale (64 of 64
  such clicks reached no button), and a server that asks the browser about the point of the view. Before
  the correction, 20 of the 32 attempts at the first page failed, those that needed a scroll, and a button
  covered by the banner was accepted and the banner was clicked, at the eight combinations: the browser
  answers the node lookup for a point of the document, and the click gave it a point of the view. The
  measures are in `docs/JOURNAL.md`.
- A click on a page that moves when the pointer first moves (a menu that opens on hover, a banner that
  slides in). The test page shifts each target 60 CSS pixels down at the first mouse movement and puts an
  intruder button where the target was. At each of the eight combinations of page zoom and screen scale,
  for each of three buttons (a large one, a 16 by 16 one between two decoys, one two and a half views
  down), a click of the server either lands once on the target or is refused as covered
  (`element_obscured`) with nothing clicked, and in the 24 attempts it is refused every time
  (`tests/test_click_shifting_integration.py`, 80 tests; `tests/test_click_shifting_page.py`, 30, and
  `tests/test_click_moving_page.py`, 10, need no browser). Before the change the same 24 attempts returned
  no error and the click landed on the intruder. After a refusal, a click on a new observation lands.
  The server now asks the browser again what lies under the point after the movement and before the press,
  every time, so a target that is covered where it stands, by a button shown at the first movement, is
  refused too (`tests/test_click_covering_integration.py`, 3 tests, on Chrome 154.0.8037.97). The 24
  attempts above were measured on Chromium 141.0.7390.37. The measures, and those of a page that scrolls
  without stopping, are in `docs/JOURNAL.md`.
- A dense page with a consent banner fixed over its lower part (643 things to click): its three
  buttons are marked and listed first in the tree, the elements under it say `covered`, and a click
  on "Refuse" by its mark takes the banner away (`tests/test_ranking_integration.py`).
- The setup assistant, `mcp-server-diwall-browser-setup`: 50 tests on a simulated machine (mount tables, package
  managers, no `/dev/fuse`, no terminal, a LUKS volume); then, on 20 October 2026, with the real gocryptfs 2.5.1 in a
  pseudo-terminal, on throw-away volumes: `create --temporary`, the server reading `mounted`, `browser`
  (Google Chrome 155.0.8059.84 without a screen, attached, `profile_encrypted: true`), `close` (no volume left in
  memory, no file of the browser profile on the disk), then `create`, `close` and `open` of a durable volume; and
  in a clean Debian 13 container, where `check` gave `sudo apt install gocryptfs fuse3`, which succeeded.
- The command that carries an old credential file over (see the operator guide): on files of the
  test suite in a simulated volume, with a refusal tested for each way out, and no value in
  anything it prints. It was not tried on a real gocryptfs volume.
- A real gocryptfs 2.5.1 volume, a throw-away one made for the check (not the operator's own),
  mounted on Debian: `status` read `mounted` and `profile_encrypted: true`, and the server wrote
  its journal and created `downloads/` in it. Opera 136 (Chromium 152), started with its profile
  in that volume and a debugging port on the loopback interface, was attached; the server opened
  a page of a public shopping site, filled its search field, pressed Enter and observed the
  results. The typed text does not appear in the journal.
- The origin policy (`strict`, `discovery`, `open`): `tests/test_origin_policy.py` and `tests/test_origin_policy_server.py` on a simulated browser, and `tests/test_origin_policy_integration.py`, 12 tests of the server started by its entry point against Google Chrome 155.0.8059.84, with fake window programs: a portal that links to a second origin, a redirection to an unknown origin, the three answers of the window (the profile read back after `Ajouter à la liste`), local names refused under `open`, a payment that still asks its window, and the policy changed in the profile without restarting. The windows of the real `kdialog` and `zenity` were shown in a virtual screen and answered by keystrokes (Enter and Escape give the refusal); a person answered them on 22 October 2026 (the next entry).
- The confirmation window, shown on the operator's workstation and answered by a person on 22 October 2026
  (revision `ce970fb`), with the operator's own gocryptfs volume mounted and a local payment page, in Opera
  138.0.6044.24, once with `kdialog` 25.04.0 and once with `zenity` 4.1.90 (the server found only that tool):
  with each, a refusal returned `confirmation_denied` and the page received nothing; an acceptance made the
  click and the page received one request; with no answer, the request expired after 90.4 seconds
  (`confirmation_expired`) and the page received nothing. The journal holds one `requested` line and one
  line with the outcome for each of the six requests.
- Claude Code, started from a desktop terminal: `status` through Claude Code read
  `confirmation_channel: native`, so the display reached the server.
- The server process over standard input and output: it writes nothing but the protocol on its
  standard output, holds no listening socket and runs with a core file limit of zero.

Not tested:

- A 64-bit ARM machine other than that Raspberry Pi 4 (a Raspberry Pi 5, another distribution), a Wayland desktop session, and a person answering the confirmation window there.
- The setup assistant on a machine that has a swap area (its report is tested on a simulated list), and its
  `browser` command with Brave, Chromium or Opera.
- Brave, and Google Chrome builds other than the one the operator ran.
- The confirmation asked through a desktop MCP client (elicitation): tested with the client of
  the MCP SDK only.
- Chrome 154 with its profile kept inside the operator's own encrypted directory.
- The page zoom made by the Ctrl + key of a browser that is not headless. The protocol has no command
  for a zoom, so the tests write the default zoom of a blank profile; whether the browser keeps the
  two kinds in the same way is not confirmed. A screen whose scale really is 1.5 was not measured
  either; the click tests have the same two limits.
- The click tests on Chrome 154. The correction reads a property of the browser's node lookup measured
  on Chromium 141 (it answers for a point of the document, not of the view): if it differs on another
  version, the tests of a click on a scrolled page fail there. A click on an element inside a frame of a
  scrolled page was not measured.
- A control that acts on the press rather than on the click, put under the pointer by a script at the instant
  of the press: the press is checked after it is made, so the page's own handlers of the press have run. The
  guard of a key and of a text is a script of the page: a page that expects it goes round it, and it does not
  see the events of a frame (Google Chrome 156.0.8078.47, `docs/OPERATOR_GUIDE.md`, section 7).

Known limits:

- The encrypted directory protects as much as its password does: whoever has the password reads its content, and a weak or reused password protects little.
- The safeguards are those measured on the date of this version, by the tests and measurements this document names. A model more recent than the ones tried can find a way around them that no trial has seen.
- Whoever does not want these safeguards will find other MCP servers that drive a browser without them.
- While the server is attached to the browser, downloads are refused in every tab, the operator's
  own tabs included, except in the short window around one of the server's clicks or Enter keys.
  To download a file yourself, stop the server or the MCP client; downloads work again as soon as
  the server's connection ends.
- A download that a page starts more than half a second after a load ends is refused.
- Accepting a `confirm` or a `prompt` dialog asks the operator in a window, whatever its message says.
  Accepting a dialog whose message asks for a payment, a deletion or a secret change is always
  refused: no capture can be taken while a dialog is open. The operator answers such a dialog.
- A secret that a site writes in the path of an address is shown as written in the results; only the
  value of a query parameter with a name such as `token`, and the fragment, are redacted.
- Visibility is approximate: the server does not compute contrast or `overflow` clipping, so text a
  human cannot read can still reach the model.
- An observation holds at most 150 marks and 400 tree lines (`marks_truncated`, `tree_truncated`).
  Past that, the server keeps first what is in a layer above the page (a dialog, a modal node, a
  fixed or sticky node painting over content in view, such as a cookie banner), listed under an
  `overlay` line in the tree, then what is in view and clear, then what is in view under a layer
  (`covered: true`, and a click on it is refused), then the rest. A banner that overlaps nothing in
  view is not a layer and can still be cut.
- The content of frames is never read; frames are masked in the image.
- What the model provider keeps of what it receives is not confirmed by this project.

## Credits

mcp-server-diwall-browser is built in an asymmetric human–LLM collaboration. The roles are written
down as they were actually played; the full list is in [CONTRIBUTORS.md](CONTRIBUTORS.md).

- **Ronan Davalan**: architect and arbiter. Vision, security requirements, decisions, validation and testing.
- **Claude Code (Anthropic; Opus 5.5, Sonnet 5.5 and Haiku 5.5)**: systems engineer and lead developer. Server implementation, tests, documentation, the working framework, the text and upkeep of the project's website, the scene that illustrates the first release and its short clip, rendered in Blender on the workstation, driven through LibreModular-MCP, and a field report of the first launch from the Debian package.
- **Claude Cloud (Anthropic)**: wrote part of the code on branches, from prompts, reviewed before every merge.
- **Muse Spark 1.3 Contributor (Meta, through OpenCode)**: first use of the server by a model other than Claude, on a local instance of a web application, whose field report shaped the next fixes; the stylesheet and layout of the project's website, chosen among several trials, the text and the behaviour of the pages left untouched; two readings of the website, with wording proposals in four languages kept as drafts and defects found, which were corrected.
- **DeepSeek V4 Pro (DeepSeek, through OpenCode)**: independent security audit of the `v0.0.1` tag, from a blank session; four of its seven findings were confirmed at triage and corrected.
- **GLM 5.3 (Zhipu, through OpenCode)**: independent security audit of the same tag, from a blank session, with reproductions on a headless browser; three of its seven findings were confirmed at triage and corrected.
- **DeepSeek V4.1 Flash 1017 (DeepSeek, through OpenCode)**: the 3D emblem "the glass D", drawn in Blender on the workstation, in two pupil variants (a round pupil and a pointer); it is the eye of the scene that illustrates the first release.
- **Gemini (Google)**: architectural advisor and synthesizer; gave the project its name, **Diwall**, "dike" in Breton.
- **gemini-3.5-flash (Google, through OpenCode)**: tested the connection through a dedicated agent: the tools reach that agent only, and its shell asks before running.
- **Space Bunny Free (opencode)**: cleanup and pruning of the project's working framework at the start of the project.

License: MIT, see [LICENSE](LICENSE).
