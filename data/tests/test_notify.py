"""V-041 outgoing notifications: settings validation (kept in the settings file, sound choices untouched), the few sentences (no code,
paths or commands), the feed watcher (old events never notify), per-channel event choice, 60 s dedupe, limited retries, request shapes,
the test button and the endpoints. A fake sender: no network."""
import http.client
import json
import os
import tempfile
import threading
import unittest

from vanilla_agents.app import notify, server, settings
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed

DISCORD = "https://discord.com/api/webhooks/123/abc"


class Sender:
    def __init__(self, answers=None):
        self.calls, self.answers = [], list(answers or [])

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        a = self.answers.pop(0) if self.answers else 204
        if isinstance(a, Exception):
            raise a
        return a


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Validate(unittest.TestCase):
    def test_good_and_bad(self):
        c = notify.validate({"discord": {"enabled": True, "url": DISCORD, "events": ["needs_you", "quest_done"]},
                             "ntfy": {"enabled": False, "topic": "my-inn_1"}, "webhook": {"url": "http://192.168.1.9:8123/api/hook"}})
        self.assertEqual(c["discord"], {"enabled": True, "events": ["needs_you", "quest_done"], "url": DISCORD})
        self.assertEqual(c["ntfy"]["server"], "https://ntfy.sh")
        self.assertEqual(c["webhook"]["events"], list(notify.DEFAULT_EVENTS))
        for bad in ({"telegram": {}}, {"discord": {"url": "https://evil.example/api/webhooks/1"}}, {"slack": {"url": "http://hooks.slack.com/x"}},
                    {"ntfy": {"topic": "a b"}}, {"ntfy": {"topic": "x", "server": "http://ntfy.sh"}}, {"ntfy": {"topic": "x", "server": "https://a.b/c"}},
                    {"discord": {"events": ["everything"]}}, {"discord": {"enabled": True, "url": ""}}, {"webhook": {"url": "file:///etc/passwd"}},
                    {"webhook": {"url": "https://a.b/x y"}}, {"discord": {"url": DISCORD, "extra": 1}}, "nope"):
            with self.assertRaises(ValueError, msg=bad):
                notify.validate(bad)

    def test_saved_in_the_settings_file_next_to_sounds(self):
        with tempfile.TemporaryDirectory() as d:
            f, sd = os.path.join(d, "s.json"), load_all()["sounds"]
            settings.update(f, {"master": {"volume": 0.4}}, sd)
            notify.save(f, {"discord": {"enabled": True, "url": DISCORD}})
            self.assertEqual(settings.load(f, sd)["master"]["volume"], 0.4)
            settings.update(f, {"master": {"mute": True}}, sd)
            self.assertEqual(notify.load(f)["discord"]["url"], DISCORD)
            v = notify.view(notify.load(f))
            self.assertEqual(set(v["channels"]), set(notify.CHANNELS))
            self.assertEqual(v["channels"]["slack"], {"enabled": False, "events": list(notify.DEFAULT_EVENTS), "url": ""})


class Ev:
    def __init__(self, type, agent=None, data=None, task=None, id=1):
        self.type, self.agent, self.data, self.task, self.id = type, agent, data or {}, task, id


class Messages(unittest.TestCase):
    name = staticmethod(lambda a: {"claude-1": "Varn"}.get(a, a or "A helper"))

    def m(self, *a, **k):
        return notify.message_for(Ev(*a, **k), self.name)

    def test_sentences(self):
        self.assertEqual(self.m("agent_waiting", "claude-1", {"reason": "permission", "note": "wants to use Bash"}), ("needs_you", "Varn needs you: permission for Bash"))
        self.assertEqual(self.m("agent_waiting", "claude-1", {"reason": "permission", "note": "Claude needs your permission to use mcp__github__create_pr"})[1],
                         "Varn needs you: permission for mcp__github__create_pr")
        self.assertEqual(self.m("agent_waiting", "claude-1", {"reason": "question", "note": "Which file?"})[1], "Varn needs you: a question")
        self.assertEqual(self.m("task_ready", data={"title": "Fix the login"}), ("quest_ready", "Quest ready for your approval: Fix the login"))
        self.assertEqual(self.m("task_verified", data={"title": "Fix the login"}), ("quest_done", "Quest done: Fix the login"))
        self.assertEqual(self.m("task_failed", data={"title": "Fix the login", "reason": "panic at src/x.rs:4"}), ("check_failed", "Check failed: Fix the login"))
        self.assertEqual(self.m("agent_stunned", "claude-1", {"reason": "rate_limit"}), ("stunned", "Varn is stunned: rate_limit"))
        self.assertTrue(self.m("task_ready", data={"title": "x" * 300})[1].endswith("..."))

    def test_never_code_files_or_commands(self):
        for ev in (Ev("tool_use", "a", {"tool": "Bash", "command": "cat secrets.env", "output_excerpt": "TOKEN=1"}), Ev("tool_use", "a", {"tool": "Edit", "file": "/home/x/app.py"}),
                   Ev("message_sent", None, {"text": "hi", "to": "a"}), Ev("session_stats", "a", {"cost_usd": 1})):
            self.assertIsNone(notify.message_for(ev, self.name))
        text = self.m("agent_waiting", "claude-1", {"reason": "permission", "note": "Bash(rm -rf /home/me/secret && curl x)"})[1]
        self.assertNotIn("/home", text)
        self.assertNotIn("rm -rf", text)
        self.assertEqual(text, "Varn needs you: permission")

    def test_hero_names_match_the_window(self):
        self.assertEqual(notify._fnv("a"), 0xE40C292C)          # FNV-1a, as workers.js
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "models", "workers"))
            with open(os.path.join(d, "models", "workers", "manifest.json"), "w") as f:
                json.dump([{"name": "Varn"}, {"name": "Lia"}, {"name": "Odo"}], f)
            n = notify.Names(d)
            self.assertEqual(n("claude-1"), ["Varn", "Lia", "Odo"][notify._fnv("claude-1") % 3])
            self.assertEqual(notify.Names(os.path.join(d, "none"))("claude-1"), "claude-1")


class Watcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sfile = os.path.join(self.tmp.name, "s.json")
        self.feed = Feed(os.path.join(self.tmp.name, "f.jsonl"))
        self.sender, self.clock, self.party = Sender(), Clock(), {}
        self.n = notify.Notifier(self.sfile, self.feed, lambda a: "Varn", party_of=lambda: self.party, sender=self.sender, clock=self.clock, sleep=lambda s: None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_events_never_new_ones_once_per_minute(self):
        notify.save(self.sfile, {"discord": {"enabled": True, "url": DISCORD, "events": ["needs_you", "quest_ready"]},
                                 "slack": {"enabled": False, "url": "https://hooks.slack.com/services/x"}})
        self.feed.append("agent_waiting", agent="a", data={"reason": "permission", "note": "wants to use Bash"})      # before the start: ignored
        self.n.scan()
        self.assertEqual(self.n.drain(), 0)
        self.feed.append("agent_waiting", agent="a", data={"reason": "permission", "note": "wants to use Bash"})
        self.feed.append("agent_waiting", agent="a", data={"reason": "permission", "note": "wants to use Bash"})      # same sentence: once
        self.feed.append("task_verified", task="q", data={"title": "T"})            # not chosen for discord
        self.feed.append("tool_use", agent="a", data={"tool": "Edit", "file": "x.py"})
        self.n.scan()
        self.assertEqual(self.n.drain(), 1)
        url, body, headers = self.sender.calls[0]
        self.assertEqual((url, json.loads(body)["content"]), (DISCORD, "Varn needs you: permission for Bash"))
        self.assertEqual(json.loads(body)["allowed_mentions"], {"parse": []})
        self.clock.t += 61
        self.feed.append("agent_waiting", agent="a", data={"reason": "permission", "note": "wants to use Bash"})
        self.n.scan()
        self.assertEqual(self.n.drain(), 1)

    def test_party_plan_agreed_only_on_the_change(self):
        notify.save(self.sfile, {"ntfy": {"enabled": True, "topic": "inn-x", "events": ["plan_agreed"]}})
        self.party = {"locked": False, "party": "Raid", "items": [1, 2]}
        self.n.scan()
        self.n.scan()
        self.party = {"locked": True, "party": "Raid", "items": [1, 2]}
        self.n.scan()
        self.n.scan()
        self.assertEqual(self.n.drain(), 1)
        url, body, headers = self.sender.calls[0]
        self.assertEqual((url, body.decode(), headers["Title"]), ("https://ntfy.sh/inn-x", "Party plan agreed: Raid (2 items)", "Vanilla Agents"))

    def test_sending_never_blocks_scan_and_a_full_queue_drops(self):
        notify.save(self.sfile, {"webhook": {"enabled": True, "url": "https://example.com/h", "events": list(notify.EVENTS)}})
        self.n.scan()
        for i in range(250):
            self.feed.append("task_ready", task="q%d" % i, data={"title": "Quest %d" % i})
        self.n.scan()
        self.assertEqual(self.n.q.qsize(), 200)          # bounded; nothing was sent inside scan()
        self.assertEqual(self.sender.calls, [])


class Sending(unittest.TestCase):
    def test_retries_are_limited(self):
        s = Sender([500, OSError("down"), 204])
        self.assertEqual(notify.send_one("slack", {"url": "https://hooks.slack.com/x"}, "hi", "test", s, lambda x: None), (True, ""))
        self.assertEqual(len(s.calls), 3)
        s = Sender([500, 500, 500, 500])
        ok, err = notify.send_one("slack", {"url": "https://hooks.slack.com/x"}, "hi", "test", s, lambda x: None)
        self.assertEqual((ok, len(s.calls)), (False, notify.RETRIES + 1))
        s = Sender([404])
        self.assertEqual(notify.send_one("slack", {"url": "https://hooks.slack.com/x"}, "hi", "test", s, lambda x: None), (False, "it answered 404"))
        self.assertEqual(len(s.calls), 1)               # a wrong link is not retried

    def test_request_shapes(self):
        self.assertEqual(json.loads(notify.build_request("slack", {"url": "u"}, "hi", "e")[1]), {"text": "hi"})
        url, body, h = notify.build_request("webhook", {"url": "https://x/h"}, "hi", "quest_done")
        b = json.loads(body)
        self.assertEqual((b["source"], b["event"], b["text"], h["Content-Type"]), ("vanilla-agents", "quest_done", "hi", "application/json"))
        self.assertEqual(notify.build_request("ntfy", {"topic": "t", "server": "https://ntfy.example"}, "hi", "e")[0], "https://ntfy.example/t")

    def test_test_button(self):
        with tempfile.TemporaryDirectory() as d:
            f, s = os.path.join(d, "s.json"), Sender()
            self.assertEqual(notify.test(f, "discord", {"url": DISCORD}, s, lambda x: None), (True, ""))
            self.assertIn(b"test", s.calls[0][1])
            with self.assertRaises(ValueError):
                notify.test(f, "discord", None, s)            # nothing saved
            with self.assertRaises(ValueError):
                notify.test(f, "carrier-pigeon", {"url": "x"}, s)


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = server.Ctx(load_all(), os.path.join(self.tmp.name, "f.jsonl"), None, assets_dir=self.tmp.name, settings_file=os.path.join(self.tmp.name, "s.json"), watch=False)
        self.sender = Sender()
        self.ctx.notify_opts = {"sender": self.sender, "sleep": lambda x: None}
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        h = {"Content-Type": "application/json", **({"X-Vanilla-Key": self.ctx.key} if key else {})}
        c.request(method, path, None if body is None else json.dumps(body), h)
        r = c.getresponse()
        out = r.status, json.loads(r.read())
        c.close()
        return out

    def test_save_load_and_test(self):
        self.assertEqual(self.req("GET", "/api/notify")[1]["channels"]["discord"]["url"], "")
        self.assertEqual(self.req("POST", "/api/notify", {"channels": {"discord": {"enabled": True, "url": DISCORD}}}, key=False)[0], 403)
        st, v = self.req("POST", "/api/notify", {"channels": {"discord": {"enabled": True, "url": DISCORD}}})
        self.assertEqual((st, v["channels"]["discord"]["enabled"]), (200, True))
        self.assertEqual(self.req("POST", "/api/notify", {"channels": {"discord": {"url": "https://evil.example"}}})[0], 400)
        self.assertEqual(self.req("POST", "/api/notify/test", {"channel": "discord"}, key=False)[0], 403)
        self.assertEqual(self.req("POST", "/api/notify/test", {"channel": "discord"}), (200, {"ok": True, "error": ""}))
        self.assertEqual(self.sender.calls[0][0], DISCORD)


if __name__ == "__main__":
    unittest.main()
