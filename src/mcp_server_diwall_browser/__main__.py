"""Command-line entry point of the server.

Why this module exists:
    The console script and ``python -m mcp_server_diwall_browser`` need a
    single place to start from, where the process is made safe before the
    first secret could be read and the server is built with the real
    dependencies only.
"""

from __future__ import annotations

from . import hardening, output
from .server import create_server, production_dependencies


def main() -> None:
    hardening.restrict_umask()
    # Before anything could read a secret: a crash must leave no core file.
    hardening.disable_core_dumps()
    output.configure_logging()
    server = create_server(production_dependencies())
    # Standard input and output only: the server opens no port.
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
