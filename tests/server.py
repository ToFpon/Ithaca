"""Serveur HTTP local pour les tests : requêtes Range (segments, reprise), débit bridé, 404,
Content-Disposition, authentification Basic."""

from __future__ import annotations

import base64
import http.server
import os
import re
import threading
import time
import urllib.parse


class Handler(http.server.BaseHTTPRequestHandler):
    root = ""
    rate = 0                 # octets / s par connexion (0 = sans limite)
    auth = ""                # « user:pass » exigé si non vide
    requests: list = []

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head: bool):
        type(self).requests.append(self.path)
        if self.auth:
            expected = "Basic " + base64.b64encode(self.auth.encode()).decode()
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="test"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        name = urllib.parse.unquote(parts.path.lstrip("/"))
        path = os.path.join(self.root, name)
        if not name or not os.path.isfile(path):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Type", "application/octet-stream")
        if "as" in query:
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + urllib.parse.quote(query["as"][0]))
        self.end_headers()
        if head:
            return
        with open(path, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            chunk = 16384
            while left > 0:
                data = fh.read(min(chunk, left))
                if not data:
                    break
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(data)
                if self.rate:
                    time.sleep(len(data) / self.rate)


class Server:
    def __init__(self, root: str, rate: int = 0, auth: str = ""):
        handler = type("H", (Handler,), {"root": root, "rate": rate, "auth": auth, "requests": []})
        self.handler = handler
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{urllib.parse.quote(name)}"

    def set_rate(self, rate: int):
        self.handler.rate = rate

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
