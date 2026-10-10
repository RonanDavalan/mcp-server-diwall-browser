"""Point d'entrée en ligne de commande du serveur.

Pourquoi ce module existe :
    Le script de console et ``python -m mcp_server_diwall_browser`` ont besoin d'un endroit unique
    d'où partir, où le processus est rendu sûr avant que le premier secret puisse être lu et où le
    serveur est construit avec les seules vraies dépendances.
"""

from __future__ import annotations

from . import hardening, output
from .server import create_server, production_dependencies


def main() -> None:
    hardening.restrict_umask()
    # Avant que quoi que ce soit puisse lire un secret : un plantage ne doit laisser aucun fichier core.
    hardening.disable_core_dumps()
    output.configure_logging()
    server = create_server(production_dependencies())
    # Entrée et sortie standard seulement : le serveur n'ouvre aucun port.
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
