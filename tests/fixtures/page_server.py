"""Serve the test pages over HTTP on 127.0.0.1.

Why this module exists:
    The browser tests load pages written for them, from a real HTTP origin,
    without reaching the network. Two servers on two ports give two origins.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any

PAGES_DIR = Path(__file__).resolve().parent / "pages"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature of the base class
        pass


class PageServer:
    def __init__(self, directory: Path = PAGES_DIR) -> None:
        self._directory = directory
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> PageServer:
        handler = functools.partial(_QuietHandler, directory=str(self._directory))
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def port(self) -> int:
        assert self._httpd is not None
        return self._httpd.server_address[1]

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, name: str) -> str:
        return f"{self.origin}/{name}"

    def __enter__(self) -> PageServer:
        return self.start()

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.stop()
