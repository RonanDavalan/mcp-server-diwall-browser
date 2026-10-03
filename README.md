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
the `claude mcp add` command was checked against `claude mcp add --help`; the command itself was not
run by this project. The tests start the server exactly as these two blocks do:
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

- The full suite runs in this repository's test environment (Ubuntu 24.04, Python 3.13.14):
  without a browser, 1205 tests pass and the 54 that need a browser are skipped with the reason;
  with Chromium 141.0.7390.37 (headless, blank temporary profile), all 1259 pass.
- The operator replayed the suite of the previous revision on Debian with Chrome 154: 1203 passed
  and 1 skipped (a test that runs as root only), the 52 browser tests of that revision included.
- `scripts/smoke_test.py` returns 0 against Chromium 141 and 3 when no browser is found.
- The server process over standard input and output: it writes nothing but the protocol on its
  standard output, holds no listening socket and runs with a core file limit of zero.

Not tested:

- A real `kdialog` or `zenity` window: the tests use fake programs in their place.
- A real gocryptfs volume: the tests simulate the mount table and the directory.
- Brave, and Google Chrome builds other than the one the operator ran.
- The confirmation asked through a desktop MCP client (elicitation): tested with the client of
  the MCP SDK only.
- Whether a browser profile kept inside the encrypted directory works as expected: it cannot be
  tried without a mounted volume.

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
- The content of frames is never read; frames are masked in the image.
- What the model provider keeps of what it receives is not confirmed by this project.

License: not chosen yet.
