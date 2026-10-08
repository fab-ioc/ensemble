"""A stand-in for an older Ensemble hub run from a source checkout: it holds
the port, has no /api/version (404) and serves a page titled Ensemble.

    python dashboard.py --port 8765 [--no-title]
"""
import http.server
import sys

PORT = int(sys.argv[sys.argv.index("--port") + 1])
TITLE = "--no-title" not in sys.argv


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" and TITLE:
            body = b"<!doctype html><html><head><title>Ensemble</title></head><body></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
