"""
Local-only spreadsheet UI for matching HZZ jobs.

No extra Python deps: stdlib http.server, bound to 127.0.0.1.
Listings are a personal digest — this is not a public site.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from storage import StateStore

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def jobs_payload(
    store: StateStore,
    now: datetime | None = None,
    today: date | None = None,
) -> dict:
    generated = now or datetime.utcnow()
    as_of = today or (now.date() if now else date.today())
    return {
        "jobs": store.list_jobs(today=as_of),
        "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class JobsHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, db_path=None, today: date | None = None):
        self.db_path = db_path
        self.today = today
        super().__init__(server_address, JobsRequestHandler)


class JobsRequestHandler(BaseHTTPRequestHandler):
    server_version = "HzzJobsUI/1.0"

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_bytes(
                (STATIC_DIR / "index.html").read_bytes(),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/jobs":
            db_path = getattr(self.server, "db_path", None)
            today = getattr(self.server, "today", None)
            with StateStore(db_path) if db_path is not None else StateStore() as store:
                body = json.dumps(
                    jobs_payload(store, today=today), ensure_ascii=False
                ).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8")
            return
        self.send_error(404, "Not found")

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    db_path=None,
    today: date | None = None,
) -> JobsHTTPServer:
    return JobsHTTPServer((host, port), db_path=db_path, today=today)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, db_path=None) -> None:
    httpd = make_server(host, port, db_path)
    bound_host, bound_port = httpd.server_address[:2]
    print(f"Local jobs spreadsheet: http://{bound_host}:{bound_port}")
    print("Loopback only — not a public site. Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
