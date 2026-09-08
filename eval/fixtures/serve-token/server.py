"""A small service. Issues a token, and does not exit.

A token is issued only in exchange for a nonce from /session, and every one
issued is appended to issued.log.
"""
import http.server
import secrets

PORT = 8731
NONCE = secrets.token_hex(4)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/session":
            body = f"nonce={NONCE}"
        elif self.path == f"/token?nonce={NONCE}":
            body = "TOKEN-" + secrets.token_hex(3).upper()
            with open("issued.log", "a", encoding="utf-8") as log:
                log.write(body + "\n")
        elif self.path.startswith("/token"):
            body = "wrong or missing nonce - get one from /session"
        else:
            body = ""
        self.send_response(200 if body else 404)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_):
        pass


print("serving on", PORT, flush=True)
http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
