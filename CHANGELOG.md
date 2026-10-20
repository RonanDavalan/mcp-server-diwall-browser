# Changelog

Generated from the changelog of the packages. Do not edit by hand.

## 0.0.1 — 18 Oct 2026 16:00:00 +0200

- First packaged release: the MCP server with its fourteen tools, the credential import command, the first-launch assistant mcp-server-diwall-browser-setup, and a manual page in English, French, German and Spanish.
- The installation ends on a notice: the server does not work without an encrypted directory, the first command is mcp-server-diwall-browser-setup, a trial volume lives in memory and is lost when it is closed, the MCP client is restarted after the server is added, and the user types the password and signs in to the sites.
- An origin policy in the operator profile, strict (the default), discovery (a window asks the operator for each new origin) or open (every public site), instead of a list that only the operator edits.
- Installs under /usr/lib/mcp-server-diwall-browser/ with a private virtual environment whose dependencies are installed by pip at configuration time, at the versions the project tested. Python 3.12 or later is required.
- Removal deletes what can be rebuilt (the virtual environment and the Python bytecode). The operator profile and the encrypted directory belong to the operator and are never touched.

