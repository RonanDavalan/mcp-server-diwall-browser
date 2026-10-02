# mcp-server-diwall-browser

This repository holds an MCP (Model Context Protocol) server that is under construction.
It is not usable yet and makes no promise about features; the full documentation comes later.

## Profile notes

- Non-Latin host names: write them in the profile in their punycode form (`xn--…`), as the
  browser shows them once copied from its address bar. A host whose conversion Python and the
  browser could read differently (`ß`, final `ς`, zero-width joiners, full-width letters) is
  refused, in either spelling: such a site cannot be allowed.
