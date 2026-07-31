"""Minimal static file server for the StacksNG demo video.

Serves demo_assets/web/ on localhost so testreel's browser chrome can show
a real address (http://localhost:PORT/) instead of file://, and so the
console scene's real <input>/<button> elements can be genuinely driven by
testreel's type/click steps rather than faked with a JS timer.

Usage:
    python server.py [port]   # default port 8420
"""

from __future__ import annotations

import http.server
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8420


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        pass  # keep stdout quiet


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[server] serving {WEB_DIR} at http://localhost:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
