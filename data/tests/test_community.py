"""Community chat (V-036): the people in a party talk to each other. Hub rules (post, since-cursor, length, rate limit, removed members,
host-only delete, kept across a restart) and the promise that the text never reaches Claude: not in the event feed, not in the MCP
tools, not in the hook texts (pending_text / stop_block), not in diagnostics, not in any file the window writes besides the host's hub."""
import threading
import http.client
import io
import json
import os
import tempfile
import unittest

from vanilla_agents import mcp
from vanilla_agents.app import diagnostics, server
from vanilla_agents.app.config import load_all
from vanilla_agents.events import hooks
from vanilla_agents.events.feed import Feed
from vanilla_agents.party import client as pc
from vanilla_agents.party import hub


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class HubChat(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.p = hub.Party(None, "Raid night", clock=self.c)
        self.h = self.p.join(self.p.d["invite"], "Host", self.p.d["host_token"])
        self.a = self.p.join(self.p.d["invite"], "Ann")

    def test_say_sync_and_cursor(self):
        st = self.p.say(self.a["key"], "  hello   there  ")
        mine = [m for m in st["chat"] if not m.get("sys")]
        self.assertEqual(len(mine), 1)
        m = mine[0]
        self.assertEqual((m["member"], m["name"], m["text"]), (self.a["member"], "Ann", "hello there"))
        self.assertTrue(m["id"] and m["ts"])
        self.assertEqual(st["chat_last"], m["id"])
        st = self.p.state(self.h["key"], since=st["chat_last"])
        self.assertEqual(st["chat"], [])                                   # nothing new: the sync stays small
        self.p.say(self.h["key"], "welcome")
        st = self.p.state(self.a["key"], since=m["id"])
        self.assertEqual([x["text"] for x in st["chat"]], ["welcome"])
        sys_lines = [x["sys"] for x in self.p.state(self.h["key"])["chat"] if x.get("sys")]
        self.assertEqual(sys_lines, ["join", "join"])                     # the host's own arrival and Ann's

    def test_newcomer_sees_chat_from_their_arrival(self):
        self.p.say(self.a["key"], "before Bo")
        b = self.p.join(self.p.d["invite"], "Bo")
        texts = [x.get("text") or x.get("sys") for x in self.p.state(b["key"])["chat"]]
        self.assertEqual(texts, ["join"])
        self.assertNotIn("before Bo", texts)

    def test_length_and_rate_limit(self):
        for bad in ("", "   ", "x" * (hub.CHAT_MAX_TEXT + 1)):
            with self.assertRaises(ValueError):
                self.p.say(self.a["key"], bad)
        self.p.say(self.a["key"], "y" * hub.CHAT_MAX_TEXT)                 # exactly the limit is fine
        for i in range(hub.CHAT_RATE[0] - 1):
            self.p.say(self.a["key"], "msg %d" % i)
        with self.assertRaises(ValueError) as e:
            self.p.say(self.a["key"], "one too many")
        self.assertIn("slow down", str(e.exception))
        self.p.say(self.h["key"], "the host is not limited by Ann")       # per member
        self.c.t += hub.CHAT_RATE[1] + 0.1
        self.p.say(self.a["key"], "later is fine")

    def test_removed_member_is_refused(self):
        self.p.kick(self.h["key"], self.a["member"])
        with self.assertRaises(PermissionError):
            self.p.say(self.a["key"], "let me back")
        sys_lines = [x["sys"] for x in self.p.state(self.h["key"])["chat"] if x.get("sys")]
        self.assertEqual(sys_lines[-1], "removed")
        with self.assertRaises(PermissionError):
            self.p.say("nobody", "hi")

    def test_host_delete_and_non_host_refused(self):
        st = self.p.say(self.a["key"], "oops")
        mid = st["chat"][-1]["id"]
        with self.assertRaises(PermissionError):
            self.p.unsay(self.a["key"], mid)                                # not the host
        st = self.p.unsay(self.h["key"], mid, since=mid)
        self.assertIn(mid, st["chat_removed"])
        self.assertNotIn(mid, [x["id"] for x in self.p.state(self.a["key"])["chat"]])
        with self.assertRaises(KeyError):
            self.p.unsay(self.h["key"], mid)
        with self.assertRaises(ValueError):
            self.p.unsay(self.h["key"], "abc")

    def test_keeps_the_last_messages(self):
        for i in range(hub.CHAT_KEEP + 20):
            self.c.t += 3                                                    # under the rate limit
            self.p.say(self.a["key"], "n%d" % i)
        self.assertEqual(len(self.p.d["chat"]), hub.CHAT_KEEP)
        self.assertEqual(self.p.d["chat"][-1]["text"], "n%d" % (hub.CHAT_KEEP + 19))

    def test_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hub.json")
            p = hub.Party(path, "Saved")
            h = p.join(p.d["invite"], "Host", p.d["host_token"])
            p.say(h["key"], "still here after a restart")
            p2 = hub.Party(path)
            texts = [x["text"] for x in p2.state(h["key"])["chat"]]
            self.assertIn("still here after a restart", texts)
            p2.say(h["key"], "and more")
            ids = [x["id"] for x in p2.d["chat"]]
            self.assertEqual(ids, sorted(set(ids)))                          # ids keep counting up


SECRET = "zq-community-only-7731 do not tell the robots"


class HumansOnly(unittest.TestCase):
    """Two PCs over real HTTP: the host's window server (say through POST /api/party) and a friend's client. Afterwards the text is in
    both windows' view and nowhere Claude can read it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ann_dir, self.bo_dir = os.path.join(d, "ann"), os.path.join(d, "bo")
        os.makedirs(self.ann_dir), os.makedirs(self.bo_dir)
        self.cfg = load_all()
        self.cfg["app"].update(settings_path=os.path.join(self.ann_dir, "settings.json"), projects_path=os.path.join(d, "p.json"),
                               approvals_path=os.path.join(d, "a.json"), sse_check_seconds=0.05)
        self.feed = os.path.join(self.ann_dir, "feed.jsonl")
        Feed(self.feed).append("agent_started", agent="claude-1", data={})
        self.ctx = server.Ctx(self.cfg, self.feed, None, settings_file=os.path.join(self.ann_dir, "settings.json"), watch=False)
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.bo = pc.PartyClient(self.bo_dir, os.path.join(self.bo_dir, "feed.jsonl"), self.cfg, period=999)

    def tearDown(self):
        self.bo.leave()
        self.ctx.party.leave()
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, body, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=8)
        c.request("POST", "/api/party", json.dumps(body), {"Content-Type": "application/json", **({server.KEY_HEADER: self.ctx.key} if key else {})})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def test_text_reaches_people_and_never_claude(self):
        ann = self.ctx.party
        link = ann.host("Raid night", "Ann", port=0, network=False)
        self.bo.join(link, "Bo")
        self.assertEqual(self.post({"action": "say", "text": SECRET}, key=False)[0], 403)      # window key needed like every POST
        code, snap = self.post({"action": "say", "text": SECRET})
        self.assertEqual(code, 200, snap)
        self.assertIn(SECRET, [m["text"] for m in snap["party"]["chat"]])                    # the window sees it
        self.nowhere_claude_reads()
        self.bo.act("say", {"text": "hi Ann, " + SECRET})
        self.nowhere_claude_reads()                                                          # checked straight after each message
        self.bo.sync()
        self.assertIn(SECRET, [m["text"] for m in self.bo.view()["chat"]])                   # so does the friend
        ann.sync()
        code, snap = self.post({"action": "unsay", "id": snap["party"]["chat"][-1]["id"]})
        self.assertEqual(code, 200, snap)                                                    # the host may delete
        code, err = self.post({"action": "say", "text": ""})
        self.assertEqual(code, 400)
        with self.assertRaises(pc.PartyError):
            self.bo.act("unsay", {"id": 1})                                                   # a friend may not
        self.nowhere_claude_reads()

    def test_live_updates_follow_the_party(self):
        """A friend's line reaches the window without any feed event: the event stream also follows the party's version."""
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        c.request("GET", "/api/events?cursor=999999")
        r = c.getresponse()
        got, stop = b"", threading.Event()

        def bump():               # keep changing the party until the stream notices (it may read its starting version after the first bump)
            while not stop.wait(0.3):
                self.ctx.party.version += 1
        threading.Thread(target=bump, daemon=True).start()
        self.addCleanup(stop.set)
        while b"event: update" not in got:
            got += r.fp.readline()
        self.assertIn(b"event: update", got)
        c.close()

    def nowhere_claude_reads(self):
        env = os.environ.pop("VANILLA_SETTINGS", None)
        self.addCleanup(lambda: env is not None and os.environ.__setitem__("VANILLA_SETTINGS", env))
        for path in (self.feed, os.path.join(self.bo_dir, "feed.jsonl")):
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    self.assertNotIn("zq-community", f.read(), path)
        for d in (self.ann_dir, self.bo_dir):                                                # cache / member files the MCP reads
            for name in os.listdir(d):
                if name == "party-hub.json":                                                 # the host's hub keeps the chat (by design)
                    continue
                with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
                    self.assertNotIn("zq-community", f.read(), name)
        tools = mcp.Tools(self.cfg, self.feed)
        out = io.StringIO()
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}]
        for i, (n, a) in enumerate((("status", {}), ("list_quests", {}), ("pending_requests", {"agent": "claude-1"}), ("pending_requests", {}),
                                    ("party_plan", {}))):
            msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": n, "arguments": a}})
        mcp.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out, tools)
        self.assertIn("Raid night", out.getvalue())                                          # party_plan did read the party...
        self.assertNotIn("zq-community", out.getvalue())                                     # ...but not its chat
        feed = Feed(self.feed)
        for agent in (None, "claude-1", "everyone"):
            self.assertNotIn("zq-community", hooks.pending_text(feed, agent))
        self.assertNotIn("zq-community", hooks.stop_block(feed, {"session_id": "claude-1"}, os.path.join(self.tmp.name, "seen.json")))
        self.assertNotIn("zq-community", diagnostics.report(self.ctx))


if __name__ == "__main__":
    unittest.main()
