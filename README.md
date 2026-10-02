# mcp-server-diwall-browser

This repository holds an MCP (Model Context Protocol) server that is under construction.
It is not usable yet and makes no promise about features; the full documentation comes later.

## Profile notes

- Non-Latin host names: write them in the profile in their punycode form (`xn--…`), as the
  browser shows them once copied from its address bar. A host whose conversion Python and the
  browser could read differently (`ß`, final `ς`, zero-width joiners, full-width letters) is
  refused, in either spelling: such a site cannot be allowed.
- The profile (`profile.json`) is checked in this order: the rights of the file and of its
  directory (owned by the server's user, mode 600, directory 700) before any byte of it is read;
  then any key that would configure the red list (refused with `redlist_in_profile`); then the
  schema.
