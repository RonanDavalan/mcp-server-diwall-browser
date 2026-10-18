# Changelog

Generated from the changelog of the packages. Do not edit by hand.

## 0.0.1 — 18 Oct 2026 16:00:00 +0200

- First packaged release: the MCP server with its fourteen tools, the credential import command, and a manual page in English, French, German and Spanish.
- Installs under /usr/lib/mcp-server-diwall-browser/ with a private virtual environment whose dependencies are installed by pip at configuration time, at the versions the project tested. Python 3.12 or later is required.
- Removal deletes what can be rebuilt (the virtual environment and the Python bytecode). The operator profile and the encrypted directory belong to the operator and are never touched.

