"""Local-only web and CLI entry point. No external model or network call."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import audit

ROOT = Path(__file__).resolve().parent
MAX_REQUEST = 2_100_000


class Handler(BaseHTTPRequestHandler):
    def allowed_host(self):
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def send_data(self, status, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def respond(self, status, data):
        self.send_data(status, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {"error": "invalid host"})
        routes = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        item = routes.get(urlsplit(self.path).path)
        if not item:
            return self.respond(404, {"error": "not found"})
        self.send_data(200, (ROOT / "web" / item[0]).read_bytes(), item[1])

    def do_POST(self):
        if not self.allowed_host():
            return self.respond(403, {"error": "invalid host"})
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            return self.respond(403, {"error": "invalid origin"})
        if self.headers.get("X-Audit-Request") != "1" or self.headers.get_content_type() != "application/json":
            return self.respond(403, {"error": "invalid request"})
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= MAX_REQUEST:
                return self.respond(413, {"error": "request too large"})
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("body must be an object")
            if self.path == "/api/demo":
                records = audit.demo(request.get("top_k", 2), request.get("guard_mode", "retrieved"))
            elif self.path == "/api/import":
                records = audit.parse_jsonl(request.get("jsonl"))
            else:
                return self.respond(404, {"error": "not found"})
            return self.respond(200, {"records": records, "summary": audit.summarize(records)})
        except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
            return self.respond(400, {"error": str(exc)[:300]})

    def log_message(self, *_):
        pass


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audit unlearning and retrieval traces locally")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8768)
    sample = sub.add_parser("demo")
    sample.add_argument("--top-k", type=int, default=2)
    sample.add_argument("--guard-mode", choices=["retrieved", "trusted"], default="retrieved")
    read = sub.add_parser("audit")
    read.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "serve":
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    else:
        rows = audit.demo(args.top_k, args.guard_mode) if args.command == "demo" else audit.parse_jsonl(args.path.read_text(encoding="utf-8-sig"))
        print(json.dumps({"records": rows, "summary": audit.summarize(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
