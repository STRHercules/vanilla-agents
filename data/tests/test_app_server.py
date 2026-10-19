import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents.app import server
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed


class SafeJoin(unittest.TestCase):
    def test_traversal_and_oddities_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.png").write_bytes(b"x")
            (Path(d) / "sub").mkdir()
            (Path(d) / "sub" / "b.png").write_bytes(b"x")
            ok = server.safe_join(d, "/sub/b.png", (".png",))
            self.assertIsNotNone(ok)
            for bad in ("../a.png", "sub/../a.png", "%2e%2e/a.png", "sub%2f..%2fa.png", "..%5ca.png", "sub\\b.png", "C:/x.png", "a.png\x00.png",
                        "a.txt", "", "/", "sub//b.png", "nope.png", "sub", "%252e%252e/a.png"):
                self.assertIsNone(server.safe_join(d, bad, (".png",)), bad)


class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = load_all()
        cfg["app"]["sse_check_seconds"] = 0.05
        self.assets = Path(self.tmp.name) / "assets"
        (self.assets / "fonts").mkdir(parents=True)
        (self.assets / "fonts" / "f.ttf").write_bytes(b"font")
        self.feed = os.path.join(self.tmp.name, "feed.jsonl")
        self.ctx = server.Ctx(cfg, self.feed, None, assets_dir=self.assets)
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        with self.assertRaises(ValueError):
            server.make_server(self.ctx, "0.0.0.0", 0)

    def test_pages_assets_and_api(self):
        self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertEqual(self.req("GET", "/js/main.js")[0], 200)
        self.assertEqual(self.req("GET", "/assets/fonts/f.ttf"), (200, b"font"))
        for bad in ("/assets/../f.ttf", "/assets/%2e%2e/f.ttf", "/assets/fonts/f.txt", "/js/../../vanilla_agents/app/server.py", "/%2e%2e/app.json", "/config/app.json"):
            self.assertEqual(self.req("GET", bad)[0], 404, bad)
        st, data = self.req("GET", "/api/state")
        self.assertEqual((st, json.loads(data)["counts"]), (200, {"done": 0, "total": 0}))
        cfg = json.loads(self.req("GET", "/api/config")[1])
        self.assertNotIn("app", cfg)
        self.assertNotIn("eli5", cfg["strings"])

    def test_foreign_host_and_origin_refused(self):
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        h = {"Content-Type": "application/json", "Origin": "http://evil.example"}
        self.assertEqual(self.req("POST", "/api/project", json.dumps({"path": ""}), h)[0], 403)
        self.assertEqual(self.req("POST", "/api/project", "{}", {"Content-Type": "text/plain"})[0], 404)

    def test_project_switch_validates_folder(self):
        h = {"Content-Type": "application/json", "X-Vanilla-Key": self.ctx.key}
        self.assertEqual(self.req("POST", "/api/project", json.dumps({"path": os.path.join(self.tmp.name, "nope")}), h)[0], 400)
        st, data = self.req("POST", "/api/project", json.dumps({"path": self.tmp.name}), h)
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(data)["project"]["path"], self.tmp.name)

    def test_sse_pushes_new_events_without_polling(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/events?cursor=0")
        r = c.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        Feed(self.feed).append("task_created", "t1", None, {"title": "Hello"})
        got = {}
        while "data" not in got:
            line = r.fp.readline().decode().strip()
            if ":" in line and not line.startswith(":"):
                k, v = line.split(":", 1)
                got[k] = v.strip()
        c.close()
        self.assertEqual(got["event"], "update")
        d = json.loads(got["data"])
        self.assertEqual(d["events"][0]["type"], "task_created")
        self.assertEqual(d["state"]["tasks"][0]["title"], "Hello")


if __name__ == "__main__":
    unittest.main()
