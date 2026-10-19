"""V-040 phone access: off by default (127.0.0.1 only), a home-network listener only when switched on, every network call needs the
window key (constant-time compare), only approve / pause / verify are allowed from the phone; the settings file keeps sound choices;
the QR encoder (app/web/js/qr.js) produces codes a real decoder reads (needs node and OpenCV; skipped without them)."""
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from vanilla_agents.app import phone, server, settings
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed

ROOT = Path(__file__).resolve().parents[1]


class Binding(unittest.TestCase):
    def test_only_private_ipv4(self):
        for ok in ("192.168.1.20", "10.0.0.5", "172.16.3.4"):
            self.assertTrue(phone.lan_ok(ok), ok)
        for bad in ("127.0.0.1", "0.0.0.0", "8.8.8.8", "169.254.1.1", "::1", "fe80::1", "", "nonsense", None):
            self.assertFalse(phone.lan_ok(bad), bad)

    def test_bind_choice(self):
        self.assertEqual(phone.bind_hosts(False, "192.168.1.20"), ["127.0.0.1"])        # default: this PC only
        self.assertEqual(phone.bind_hosts(True, "192.168.1.20"), ["127.0.0.1", "192.168.1.20"])
        self.assertEqual(phone.bind_hosts(True, None), ["127.0.0.1"])
        self.assertEqual(phone.bind_hosts(True, "0.0.0.0"), ["127.0.0.1"])
        self.assertEqual(phone.lan_ip(lambda: "192.168.7.7"), "192.168.7.7")
        self.assertIsNone(phone.lan_ip(lambda: "127.0.0.1"))

    def test_phone_server_refuses_any_and_loopback(self):
        ctx = mock.Mock()
        for bad in ("0.0.0.0", "127.0.0.1", "8.8.8.8"):
            with self.assertRaises(OSError):
                server.make_phone_server(ctx, bad, 0)

    def test_setting_is_off_by_default_and_keeps_sound_choices(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.json")
            sd = load_all()["sounds"]
            self.assertFalse(phone.enabled(f))
            settings.update(f, {"master": {"volume": 0.3}}, sd)
            phone.set_enabled(f, True)
            self.assertTrue(phone.enabled(f))
            self.assertEqual(settings.load(f, sd)["master"]["volume"], 0.3)
            settings.update(f, {"master": {"mute": True}}, sd)                 # a sound change keeps the phone switch
            self.assertTrue(phone.enabled(f))
            settings.update(f, {"phone": {"enabled": False}}, sd)              # /api/settings cannot flip it
            self.assertTrue(phone.enabled(f))
            with self.assertRaises(ValueError):
                phone.set_enabled(f, "yes")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feed = os.path.join(self.tmp.name, "feed.jsonl")
        Feed(self.feed).append("task_created", task="q1", data={"title": "Fix it"})
        self.ctx = server.Ctx(load_all(), self.feed, None, assets_dir=self.tmp.name, settings_file=os.path.join(self.tmp.name, "s.json"), watch=False)
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.ctx.phone_stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, port, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request(method, path, None if body is None else json.dumps(body), headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data


class PhoneListener(Base):
    """A phone listener on 127.0.0.1 standing in for the home-network address (tests cannot own a real one)."""

    def start_phone(self):
        with mock.patch.object(phone, "lan_ok", lambda ip: True):
            self.ph = server.make_phone_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.ph.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.ph.shutdown(), self.ph.server_close()))
        return self.ph.server_address[1]

    def test_everything_needs_the_key(self):
        p = self.start_phone()
        self.assertEqual(self.req(p, "GET", "/phone.html")[0], 200)
        self.assertEqual(self.req(p, "GET", "/")[0], 200)
        self.assertEqual(self.req(p, "GET", "/api/state")[0], 403)                                  # no key: nothing to see
        self.assertEqual(self.req(p, "GET", "/api/state", headers={"X-Vanilla-Key": "x" * 43})[0], 403)
        self.assertEqual(self.req(p, "GET", "/api/state", headers={"X-Vanilla-Key": self.ctx.key})[0], 200)
        for other in ("/index.html", "/js/main.js", "/api/config", "/api/notify", "/api/phone", "/api/diff"):
            self.assertEqual(self.req(p, "GET", other, headers={"X-Vanilla-Key": self.ctx.key})[0], 404, other)

    def test_only_owner_buttons_and_only_with_the_key(self):
        p = self.start_phone()
        j = {"Content-Type": "application/json"}
        k = {**j, "X-Vanilla-Key": self.ctx.key}
        self.assertEqual(self.req(p, "POST", "/api/approve", {"task": "q1"}, j)[0], 403)
        self.assertEqual(self.req(p, "POST", "/api/approve", {"task": "q1"}, {**j, "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req(p, "POST", "/api/approve", {"task": "q1"}, {**k, "Host": "localhost:%d" % p})[0], 403)        # wrong name for this listener
        for call in ("/api/whisper", "/api/project", "/api/settings", "/api/resurrect", "/api/phone", "/api/notify", "/api/party", "/api/hearth"):
            self.assertEqual(self.req(p, "POST", call, {}, k)[0], 404, call)
        st, data = self.req(p, "POST", "/api/approve", {"task": "q1"}, k)
        self.assertEqual(st, 200)
        q = next(t for t in json.loads(data)["tasks"] if t["id"] == "q1")
        self.assertEqual((q["approved"], q["approved_by"]), (True, "you (phone)"))
        from vanilla_agents.verify import runner
        self.addCleanup(runner.CANCEL.clear)          # Pause stops checks process-wide; do not leak into other tests
        self.assertEqual(self.req(p, "POST", "/api/pause", {"paused": True}, k)[0], 200)
        self.assertTrue(self.ctx.verify.status()["paused"])

    def test_phone_strings(self):
        p = self.start_phone()
        st, data = self.req(p, "GET", "/api/phone/strings")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(data)["approve"], "Approve")


class Switch(Base):
    def post(self, body, key=True):
        h = {"Content-Type": "application/json", **({"X-Vanilla-Key": self.ctx.key} if key else {})}
        return self.req(self.srv.server_address[1], "POST", "/api/phone", body, h)

    def test_off_by_default_switch_needs_key_and_reports_problems(self):
        st, data = self.req(self.srv.server_address[1], "GET", "/api/phone")
        self.assertEqual(json.loads(data), {"enabled": False, "listening": False, "url": None, "error": ""})
        self.assertIsNone(self.ctx.phone_srv)
        self.assertEqual(self.post({"enabled": True}, key=False)[0], 403)
        self.ctx.phone_probe = lambda: "127.0.0.1"           # no home network address on this machine
        st, data = self.post({"enabled": True})
        d = json.loads(data)
        self.assertEqual((st, d["enabled"], d["listening"]), (200, True, False))
        self.assertIn("home network", d["error"])
        self.assertEqual(self.post({"enabled": "on"})[0], 400)
        st, data = self.post({"enabled": False})
        self.assertEqual(json.loads(data)["enabled"], False)

    def test_listener_opens_and_closes(self):
        self.ctx.phone_probe = lambda: "127.0.0.1"
        with mock.patch.object(phone, "lan_ok", lambda ip: True):
            d = json.loads(self.post({"enabled": True})[1])
        self.assertTrue(d["listening"])
        self.assertTrue(d["url"].startswith("http://127.0.0.1:") and d["url"].endswith("/phone.html"))
        self.assertNotIn(self.ctx.key, d["url"])             # the window adds the key itself
        self.assertFalse(json.loads(self.post({"enabled": False})[1])["listening"])
        self.assertIsNone(self.ctx.phone_srv)


class PhonePage(unittest.TestCase):
    def test_buttons_only_with_the_key(self):
        html = (ROOT / "app" / "web" / "phone.html").read_text(encoding="utf-8")
        self.assertIn("$('owner').hidden = !key", html)
        self.assertIn(".owner[hidden] { display: none; }", html)          # display:flex must not beat the hidden attribute
        self.assertIn("const can = key && !q.approved", html)
        self.assertIn("X-Vanilla-Key", html)
        self.assertIn("history.replaceState", html)          # the key does not stay in the address bar


@unittest.skipUnless(shutil.which("node"), "node not installed")
class QrCode(unittest.TestCase):
    def test_codes_decode(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not installed")
        texts = [["http://192.168.1.23:8943/phone.html#k=" + "AbC-_9" * 7 + "x", "M"], ["a", "L"], ["x" * 60, "M"], ["Grüße 東京", "M"], ["y" * 150, "L"], ["z" * 200, "M"]]
        js = ("import { encode } from %s; const out = []; for (const [t, e] of %s) { const q = encode(t, e); out.push({ v: q.version, m: q.modules.map((r) => r.map((b) => (b ? 1 : 0)).join('')) }); }"
              " console.log(JSON.stringify(out));") % (json.dumps((ROOT / "app" / "web" / "js" / "qr.js").as_uri()), json.dumps(texts))
        res = json.loads(subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout)
        dets = [cv2.QRCodeDetector()] + ([cv2.QRCodeDetectorAruco()] if hasattr(cv2, "QRCodeDetectorAruco") else [])
        for (text, _e), r in zip(texts, res):
            m = np.array([[int(c) for c in row] for row in r["m"]], dtype=np.uint8)
            img = np.kron(np.pad(1 - m, 4, constant_values=1), np.ones((6, 6), np.uint8)) * 255
            self.assertIn(text, [d.detectAndDecode(img)[0] for d in dets], "version %d" % r["v"])
        self.assertEqual([r["v"] for r in res][:3], [5, 1, 4])


if __name__ == "__main__":
    unittest.main()
