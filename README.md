# mcp-server-diwall-browser

This repository holds an MCP (Model Context Protocol) server that is under construction; the
full documentation comes later.

## Running the server

The server speaks MCP over standard input and output and opens no port. From a clone:

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m mcp_server_diwall_browser
```

An MCP client starts the last command itself. Without a profile, the `status` tool says where to
put one; every other tool answers `not_configured`.

## Effects on the operator's browser

- While the server is attached to the browser, downloads are refused in every tab, the
  operator's own included, except in the short window around one of the server's clicks or
  Enter keys; they work again as soon as the server's connection ends (measured on Chromium 141).
- The tabs the server opened stay open when it stops; the browser is never closed by it.

## Profile notes

- Non-Latin host names: write them in the profile in their punycode form (`xn--…`), as the
  browser shows them once copied from its address bar. A host whose conversion Python and the
  browser could read differently (`ß`, final `ς`, zero-width joiners, full-width letters) is
  refused, in either spelling: such a site cannot be allowed.
- The profile (`profile.json`) is checked in this order: the rights of the file and of its
  directory (owned by the server's user, mode 600, directory 700) before any byte of it is read;
  then any key that would configure the red list (refused with `redlist_in_profile`); then the
  schema.
