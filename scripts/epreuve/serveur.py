#!/usr/bin/env python3
"""Serveur de la page d'épreuve du paiement : écoute sur la boucle locale, une ligne par POST reçu.

Pourquoi ce module existe :
    L'épreuve de la fenêtre de confirmation doit prouver qu'un refus ou une expiration n'envoie
    rien à la page. Un POST arrivé ici est la seule preuve d'un clic exécuté ; ce serveur n'écrit
    que son heure et son chemin, jamais le corps de la requête.
"""

from __future__ import annotations

import argparse
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PAGE = Path(__file__).with_name("paiement.html")


class Handler(BaseHTTPRequestHandler):
    def _reply(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply(PAGE.read_bytes())

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with self.server.post_log.open("a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} POST {self.path}\n")
        self._reply("<!doctype html><html lang=\"fr\"><meta charset=\"utf-8\"><title>Paiement reçu</title>"
                    "<h1>Paiement reçu</h1>".encode())

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=0, help="0 : un port libre choisi par le système")
    parser.add_argument("--post-log", type=Path, required=True, help="fichier où s'écrit une ligne par POST reçu")
    parser.add_argument("--port-file", type=Path, required=True, help="fichier où s'écrit le port choisi")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    server.post_log = args.post_log
    args.post_log.touch()
    args.port_file.write_text(str(server.server_address[1]), encoding="utf-8")
    print(f"écoute sur 127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
