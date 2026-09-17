"""Serve the HTML fixtures on 127.0.0.1:8009 so `checker run` has targets.

    uv run python -m tests.serve_fixtures
"""

from __future__ import annotations

import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIRECTORY = Path(__file__).parent / "fixtures" / "pages"
PORT = 8009


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A002 - stdlib signature
        pass


def main() -> None:
    handler = functools.partial(QuietHandler, directory=str(DIRECTORY))
    with ThreadingHTTPServer(("127.0.0.1", PORT), handler) as httpd:
        print(f"serving {DIRECTORY} on http://127.0.0.1:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
