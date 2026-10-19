"""V-048: fixes from the independent review of the V-026..V-045 merges. Each test failed before its fix.
GitHub token never sent to a look-alike remote host; party hub: negative Content-Length, non-ASCII keys, huge numbers, member ids
never reused; server: wrong JSON types answer 400, a NUL in /api/diff, the phone listener never waits on a big body; a garbled HTTP
answer does not kill the notification sender or escape as a crash; validators reject a trailing newline. Temp dirs, local sockets only."""
import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

from tests.test_integrations_sources import gitdir
from vanilla_agents.app import contests, notify, phone, places_api, resurrect, server
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed
from vanilla_agents.integrations import httpjson, isettings
from vanilla_agents.integrations.hub import Hub
from vanilla_agents.party import hub as party_hub

TOKEN = "ghp_SECRETsecretSECRET1234"


class Opener:
    """A fake urlopen that records every request (URL and headers) and answers an empty list."""

    def __init__(self):
        self.reqs = []

    def __call__(self, req, timeout):
        self.reqs.append((req.full_url, dict(req.header_items())))

        class R:
            def read(self, n=-1):
                return b'{"login": "me"}' if req.full_url.endswith("/user") else b"[]"
        return R()


class TokenHost(unittest.TestCase):
    def test_token_only_goes_to_github_or_the_named_host(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.json")
            isettings.save(f, {"github": {"enabled": True, "mode": "token", "token": TOKEN}})
            for remote in ("https://github.attacker.example/o/r.git", "git@evilgithub.io:o/r.git"):
                op = Opener()
                proj = gitdir([("origin", remote)])
                h = Hub(f, lambda: proj, opener=op)
                st = h.refresh_github(ci=False)
                self.assertFalse(st["ok"])
                self.assertIn("only sent to github.com", st["error"])
                self.assertEqual(op.reqs, [], remote)                                      # nothing at all went to the look-alike host
                self.assertFalse(h.test("github")["ok"])
                self.assertEqual(op.reqs, [])
            op = Opener()                                                                    # github.com itself still works
            h = Hub(f, lambda: gitdir([("origin", "https://github.com/o/r.git")]), opener=op)
            self.assertTrue(h.refresh_github(ci=False)["ok"])
            self.assertTrue(all(u.startswith("https://api.github.com/") for u, _ in op.reqs))
            isettings.save(f, {"github": {"enabled": True, "mode": "token", "host": "github.mycorp.example"}})     # the owner named his Enterprise host
            op = Opener()
            h = Hub(f, lambda: gitdir([("origin", "https://github.mycorp.example/o/r.git")]), opener=op)
            self.assertTrue(h.refresh_github(ci=False)["ok"])
            self.assertTrue(op.reqs and all(u.startswith("https://github.mycorp.example/api/v3/") for u, _ in op.reqs))


def raw_request(port, data, wait=5):
    """Send raw bytes, return what came back (b'' when the server closed without an answer, None when nothing came in time)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    try:
        s.sendall(data)
        try:
            return s.recv(4096)
        except socket.timeout:
            return None
    finally:
        s.close()


class PartyHub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = party_hub.Party(os.path.join(self.tmp.name, "h.json"))
        self.srv = party_hub.serve(self.p, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.key = self.p.join(self.p.d["invite"], "Host", self.p.d["host_token"])["key"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=b"", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request(method, path, body, headers or {})
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def test_negative_length_is_refused_at_once(self):
        got = raw_request(self.port, b"POST /party/say HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\nX-Party-Key: " + self.key.encode() + b"\r\n\r\n{}")
        self.assertIsNotNone(got, "the hub waited for the connection to close")
        self.assertIn(b" 413 ", got.split(b"\r\n")[0] + b" ")
        self.assertIsNotNone(party_hub.make_handler(self.p).timeout)                       # a client that stops sending frees its thread

    def test_odd_keys_and_numbers_answer(self):
        bad = "é".encode("utf-8").decode("latin-1")
        self.assertEqual(self.req("GET", "/party/state", headers={"X-Party-Key": bad})[0], 403)
        self.assertEqual(self.req("POST", "/party/join", json.dumps({"invite": "é", "name": "x"}))[0], 403)
        k = {"X-Party-Key": self.key}
        self.assertEqual(self.req("POST", "/party/agree", b'{"version": 1e999}', k)[0], 400)
        self.assertEqual(self.req("POST", "/party/renew", b'{"hours": 1e999}', k)[0], 400)
        self.assertEqual(self.req("GET", "/party/state", headers=k)[0], 200)

    def test_member_ids_are_never_reused(self):
        inv = self.p.d["invite"]
        b = self.p.join(inv, "B")
        self.p.leave(b["key"])
        c = self.p.join(inv, "C")
        self.assertNotEqual(c["member"], b["member"])
        d = self.p.join(inv, "D")
        self.p.kick(self.key, d["member"])
        e = self.p.join(self.p.d["invite"], "E")
        self.assertNotIn(e["member"], (b["member"], d["member"]))
        again = party_hub.Party(self.p.path)                                                 # the counter is saved with the party
        f = again.join(again.d["invite"], "F")
        self.assertNotIn(f["member"], (b["member"], c["member"], d["member"], e["member"]))


class ServerRobust(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feed = os.path.join(self.tmp.name, "feed.jsonl")
        Feed(self.feed).append("task_created", task="q1", data={"title": "Fix it"})
        cfg = load_all()
        cfg["app"]["approvals_path"] = os.path.join(self.tmp.name, "approvals.json")
        self.ctx = server.Ctx(cfg, self.feed, self.tmp.name, assets_dir=self.tmp.name, settings_file=os.path.join(self.tmp.name, "s.json"), watch=False)
        os.makedirs(os.path.join(self.tmp.name, ".git"), exist_ok=True)
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.ctx.stop.set()
        self.ctx.phone_stop()
        self.ctx.party.leave()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, path, raw, port=None):
        c = http.client.HTTPConnection("127.0.0.1", port or self.port, timeout=10)
        try:
            c.request("POST", path, raw, {"Content-Type": "application/json", "X-Vanilla-Key": self.ctx.key})
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def test_wrong_types_answer_400(self):
        for raw in (b'{"action": "host", "port": 1e999}', b'{"action": "host", "hours": [1]}', b'{"action": "host", "port": [8940]}'):
            self.assertEqual(self.post("/api/party", raw)[0], 400, raw)
        self.assertFalse(self.ctx.party.joined)

    def test_nul_in_diff_file(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/diff?file=%00")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn("error", json.loads(r.read()))
        c.close()

    def test_phone_listener_never_waits_for_a_big_body(self):
        with mock.patch.object(phone, "lan_ok", lambda ip: True):
            ph = server.make_phone_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=ph.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (ph.shutdown(), ph.server_close()))
        p = ph.server_address[1]
        got = raw_request(p, b"POST /api/inn_photo HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nContent-Length: 300000\r\n\r\n" % p, wait=3)
        self.assertIsNotNone(got, "the phone listener waited for a 300 KB body from an unchecked client")
        self.assertIsNotNone(server.PhoneHandler.timeout)


class Garbled(unittest.TestCase):
    """A server that answers something that is not HTTP (http.client.BadStatusLine is not an OSError)."""

    def setUp(self):
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(8)
        self.port = self.ls.getsockname()[1]

        def serve():
            while True:
                try:
                    c, _ = self.ls.accept()
                except OSError:
                    return
                try:
                    c.recv(65536)
                    c.sendall(b"garbage\r\n\r\n")
                finally:
                    c.close()
        threading.Thread(target=serve, daemon=True).start()

    def tearDown(self):
        self.ls.close()

    def test_notify_sender_survives(self):
        url = "http://127.0.0.1:%d/hook" % self.port
        ok, err = notify.send_one("webhook", {"url": url}, "hello", "test", sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertTrue(err)

    def test_httpjson_says_it_plainly(self):
        with self.assertRaises(httpjson.HttpError):
            httpjson.call("https://127.0.0.1:%d/x" % self.port, opener=lambda req, t: urllib.request.urlopen("http://127.0.0.1:%d/x" % self.port, timeout=t))

    def test_party_client(self):
        from vanilla_agents.party.client import PartyClient, PartyError
        with tempfile.TemporaryDirectory() as d:
            pc = PartyClient(d, os.path.join(d, "f.jsonl"), load_all())
            with self.assertRaises(PartyError):
                pc.join("http://127.0.0.1:%d/party?t=abc" % self.port, "Me")


class SettingsKeepGroups(unittest.TestCase):
    def test_pause_keeps_secrets_when_a_sound_entry_is_unknown(self):
        from vanilla_agents.app import settings
        sd = load_all()["sounds"]
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.json")
            phone.set_enabled(f, True)
            isettings.save(f, {"github": {"enabled": True, "mode": "token", "token": TOKEN}})
            raw = json.load(open(f, encoding="utf-8"))
            raw["events"] = {"a_sound_event_from_an_older_version": {"volume": 0.5}}      # e.g. an event sounds.json no longer has
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            settings.update(f, {"paused": True}, sd)                                       # what Pause / Hearth / the phone do
            self.assertTrue(phone.enabled(f))
            self.assertEqual(isettings.load(f)["github"]["token"], TOKEN)
            self.assertTrue(settings.load(f, sd)["paused"])


class Validators(unittest.TestCase):
    def test_trailing_newline_is_refused(self):
        self.assertTrue(resurrect.valid_session("0123abcd-1234-5678-9abc-def012345678"))
        self.assertFalse(resurrect.valid_session("0123abcd-1234-5678-9abc-def012345678\n"))
        self.assertTrue(contests.valid_ref("feat/x"))
        self.assertFalse(contests.valid_ref("feat/x\n"))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                places_api.save_photo(d, "inn\n", "/9j/4AAQ")


if __name__ == "__main__":
    unittest.main()


class SettingsLockV049(unittest.TestCase):
    def test_parallel_saves_keep_every_group(self):
        """V-049: sound changes and the phone / notify / integrations saves at the same moment never lose one another."""
        import tempfile as _t
        import threading as _th
        from vanilla_agents.app import settings as st
        from vanilla_agents.app.config import load_all
        sd = load_all()["sounds"]
        with _t.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            go = _th.Event()

            def extra(name):
                go.wait()
                for i in range(40):
                    st.save_extra(p, name, {"n": i})

            def sound():
                go.wait()
                for i in range(40):
                    st.update(p, {"paused": bool(i % 2)}, sd)
            ts = [_th.Thread(target=extra, args=(n,)) for n in ("phone", "notify", "integrations")] + [_th.Thread(target=sound)]
            for t in ts:
                t.start()
            go.set()
            for t in ts:
                t.join()
            for n in ("phone", "notify", "integrations"):
                self.assertEqual(st.load_extra(p, n), {"n": 39}, n)
