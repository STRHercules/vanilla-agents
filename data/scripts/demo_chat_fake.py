"""DEV ONLY (V-050): a pretend Discord / Slack / ntfy on this PC, for trying Options > Chat replies and taking screenshots without a
real bot. Start it, then start the server with VANILLA_CHAT_FAKE=http://127.0.0.1:<port> (chat replies then talk only to this).

  python scripts/demo_chat_fake.py --port 8972
  curl -X POST 127.0.0.1:8972/say -d '{"service": "discord", "user": "111111111111111111", "text": "@Varn please add a test"}'
  curl 127.0.0.1:8972/sent          (what Vanilla Agents answered)

It answers the same URL shapes as the real services (channels/<id>/messages?after=, conversations.history?oldest=, <topic>/json?poll=1),
keeps everything in memory, and accepts any token. Never use it for real."""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

EPOCH = 1420070400000
LOCK = threading.Lock()
MSGS = {"discord": [], "slack": [], "ntfy": []}
SENT = []


def _sid():
    return str((int(time.time() * 1000) - EPOCH) << 22 | (len(MSGS["discord"]) & 0x3FFFFF))


def add(service, user, text, bot=False):
    with LOCK:
        now = time.time()
        if service == "discord":
            m = {"id": _sid(), "type": 0, "content": text, "author": {"id": user, "username": "you", **({"bot": True} if bot else {})}}
        elif service == "slack":
            m = {"type": "message", "text": text, "ts": "%.6f" % (now + len(MSGS["slack"]) * 1e-6), **({"bot_id": "B0FAKE"} if bot else {"user": user})}
        else:
            m = {"id": "f%d" % (len(MSGS["ntfy"]) + 1), "time": int(now), "event": "message", "topic": "replies", "message": text, **({"title": "Vanilla Agents"} if bot else {})}
        MSGS[service].append(m)
        if bot:
            SENT.append({"service": service, "text": text, "at": now})
        return m


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if 0 < n < 100000 else b""

    def do_GET(self):
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p == "/sent":
            return self._json(200, SENT)
        if p.startswith("/discord/api/v10/channels/"):
            rest = p[len("/discord/api/v10/channels/"):].split("/")
            if len(rest) == 1:
                return self._json(200, {"id": rest[0], "name": "vanilla-agents", "type": 0})
            after = int(q.get("after", 0))
            with LOCK:
                ms = [m for m in MSGS["discord"] if int(m["id"]) > after]
            return self._json(200, list(reversed(ms[-int(q.get("limit", 50)):])), "application/json")
        if p == "/slack/api/conversations.history":
            oldest = float(q.get("oldest", 0))
            with LOCK:
                ms = [m for m in MSGS["slack"] if float(m["ts"]) > oldest]
            return self._json(200, {"ok": True, "messages": list(reversed(ms)), "has_more": False})
        if p.startswith("/ntfy/") and p.endswith("/json"):
            since = q.get("since", "0")
            with LOCK:
                ms = MSGS["ntfy"]
                ids = [m["id"] for m in ms]
                ms = ms[ids.index(since) + 1:] if since in ids else [m for m in ms if since.isdigit() and m["time"] >= int(since)]
            return self._json(200, b"".join(json.dumps(m).encode() + b"\n" for m in ms), "application/x-ndjson")
        self._json(404, {"message": "404: Not Found"})

    def do_POST(self):
        p = urlsplit(self.path).path
        raw = self._body()
        if p == "/say":
            d = json.loads(raw or b"{}")
            return self._json(200, add(d.get("service", "discord"), str(d.get("user", "111111111111111111")), str(d.get("text", ""))))
        if p.startswith("/discord/api/v10/channels/") and p.endswith("/messages"):
            return self._json(200, add("discord", "999", json.loads(raw)["content"], bot=True))
        if p == "/slack/api/chat.postMessage":
            add("slack", "", json.loads(raw)["text"], bot=True)
            return self._json(200, {"ok": True})
        if p.startswith("/ntfy/"):
            return self._json(200, add("ntfy", "", raw.decode("utf-8", "replace"), bot=True))
        self._json(404, {"message": "404: Not Found"})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8972)
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print("fake chat services on http://127.0.0.1:%d (dev only)" % a.port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
