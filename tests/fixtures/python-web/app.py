"""Minimal Python web fixture (stdlib only) for OPENLAB end-to-end tests."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8000"))

PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>OPENLAB Python Fixture</title>
<style>body{margin:0;font:15px/1.6 system-ui;background:#0d0f12;color:#e8eaed;padding:40px}
h1{font-size:24px}button{padding:8px 14px;border-radius:8px;border:1px solid #2a2f36;background:#151a20;color:#e8eaed}
#log{margin-top:12px;color:#7dd3fc}</style></head>
<body><h1>Python fixture app</h1>
<p>Served by ThreadingHTTPServer, started for real by OPENLAB.</p>
<button id="tick">Run counter</button>
<div id="log"></div>
<script>let n=0;document.getElementById('tick').onclick=()=>{n+=1;document.getElementById('log').textContent='counter = '+n;};</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):  # keep the console quiet
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"python fixture listening on {PORT}", flush=True)
    server.serve_forever()
