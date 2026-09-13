"""A service that will not hand out a token in one step. It does not exit, and
it is meant to be left running."""
import http.server
import pathlib
import secrets
import threading
import time

PORT = 8731
STATE = {"phrase": None}
HERE = pathlib.Path(__file__).resolve().parent


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/request-phrase":
            STATE["phrase"] = secrets.token_hex(3).upper()
            print(f"phrase for the next token: {STATE['phrase']}", flush=True)
            body = ("a phrase has been printed to my output. Send it back as "
                    "/token?phrase=THEPHRASE")
        elif self.path.startswith("/token?phrase="):
            sent = self.path.split("=", 1)[1]
            if STATE["phrase"] and sent == STATE["phrase"]:
                body = "TOKEN-" + secrets.token_hex(3).upper()
                with open("issued.log", "a", encoding="utf-8") as log:
                    log.write(body + "\n")
                STATE["phrase"] = None
            else:
                body = "wrong phrase. Ask for one at /request-phrase first"
        elif self.path == "/token":
            body = "a token costs a phrase. Ask for one at /request-phrase"
        else:
            body = ""
        self.send_response(200 if body else 404)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_):
        pass


def _heartbeat():
    # Still running is part of the job, and this is how the check knows.
    while True:
        (HERE / "heartbeat").write_text(str(time.time()), encoding="utf-8")
        time.sleep(2)


threading.Thread(target=_heartbeat, daemon=True).start()
print("serving on", PORT, flush=True)
http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
