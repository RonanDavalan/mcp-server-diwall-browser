"""Command-line entry point of the server.

Why this module exists:
    The console script and ``python -m mcp_server_diwall_browser`` need a
    single place to start from. Until the tools are wired, starting must fail
    loudly instead of pretending to serve.
"""

from __future__ import annotations

import sys


def main() -> None:
    sys.stderr.write("mcp-server-diwall-browser: the server is not wired yet\n")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
