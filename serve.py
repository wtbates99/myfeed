#!/usr/bin/env python3
"""Serve the digest and log clicks for the learning ranker. Stdlib only.

Routes:
  /            the current digest
  /go?u=&t=    log the click, redirect to the article
  /archive/    past editions
  /robots.txt  keep crawlers out
"""

import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
CLICKS_LOG = HERE / "clicks.jsonl"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8484


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            f = HERE / "digest.html"
            if f.exists():
                self._send(200, f.read_bytes(), extra={"Cache-Control": "no-cache"})
            else:
                self._send(503, "No digest generated yet.")
        elif url.path == "/go":
            q = parse_qs(url.query)
            target = q.get("u", [""])[0]
            if not target.startswith(("http://", "https://")):
                self._send(400, "bad target")
                return
            ua = self.headers.get("User-Agent", "").lower()
            if "bot" not in ua and "crawl" not in ua and "spider" not in ua:
                with CLICKS_LOG.open("a") as f:
                    f.write(json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "url": target, "title": q.get("t", [""])[0],
                    }) + "\n")
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
        elif url.path == "/robots.txt":
            self._send(200, "User-agent: *\nDisallow: /\n", "text/plain")
        elif url.path == "/archive/":
            files = sorted((HERE / "archive").glob("*.html"), reverse=True)
            rows = "\n".join(f'<p><a href="/archive/{f.name}">{f.stem}</a></p>'
                             for f in files[:200])
            self._send(200, "<!doctype html><title>archive</title>"
                            "<body style='font-family:serif;max-width:30rem;"
                            f"margin:2rem auto'><h1>Past editions</h1>{rows}")
        elif url.path.startswith("/archive/"):
            name = Path(url.path).name  # basename only: no traversal
            f = HERE / "archive" / name
            if f.is_file() and f.suffix == ".html":
                self._send(200, f.read_bytes())
            else:
                self._send(404, "not found")
        else:
            self._send(404, "not found")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"serving on :{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
