#!/usr/bin/env python3
"""Serve the built dashboard (dashboard/dist) for local use, instead of the Vite dev server.

    python ops/serve_dashboard.py --port 5173

Static files with a single-page-app fallback (/facebook, /facebook/auto-scraper -> index.html).
Binds to 127.0.0.1 only. Build first: cd dashboard && npm run build.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        path = pathlib.Path(self.translate_path(self.path))
        if not path.exists() and "." not in self.path.rsplit("/", 1)[-1]:
            self.path = "/index.html"  # client-side route
        return super().send_head()

    def end_headers(self):
        # index.html must not be cached (it names the current asset files); hashed assets can be.
        if self.path.startswith("/assets/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stdout.write("dashboard %s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5173)
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    dist = pathlib.Path(a.root) / "dashboard" / "dist"
    if not (dist / "index.html").exists():
        sys.exit(f"{dist} has no index.html: run 'npm run build' in dashboard/")
    handler = functools.partial(SpaHandler, directory=str(dist))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler)
    print(f"dashboard serving {dist} on http://localhost:{a.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
