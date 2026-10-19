"""V-032 background session discovery: parsing `claude agents --json` (fake runner), merging into the snapshot, failing quietly."""
import json
import subprocess
import tempfile
import threading
import os
import unittest
from types import SimpleNamespace

from vanilla_agents.app import background as B
from vanilla_agents.app.config import load_all
from vanilla_agents.app.server import Ctx
from vanilla_agents.events.feed import Feed

# shape seen from Claude Code 2.1.303 plus the documented background fields
OUT = json.dumps([
    {"pid": 96, "cwd": "/home/me/game", "kind": "interactive", "startedAt": 1792404128948, "sessionId": "667adf9b-a050-5ead-a25f-6c930953382b", "name": "claude-64", "status": "busy"},
    {"pid": 97, "cwd": "/home/me/game", "kind": "interactive", "startedAt": 1, "sessionId": "11112222-3333", "status": "waiting", "waitingFor": "permission prompt"},
    {"cwd": "/home/me/web", "kind": "background", "startedAt": 2, "id": "bg7", "state": "blocked"},
    {"cwd": "/x", "kind": "background", "startedAt": 3, "id": "bg8", "state": "working"},
    {"cwd": "/x", "kind": "background", "startedAt": 4, "id": "bg9", "state": "stopped"},
    {"cwd": "/x", "kind": "background", "startedAt": 5, "id": "bgA", "state": "failed", "sessionId": "aaaabbbbcccc"},
    {"cwd": "/x", "kind": "interactive", "startedAt": 6, "status": "idle"},          # no id at all: skipped
    "junk",
])


class Parse(unittest.TestCase):
    def test_fields_and_states(self):
        rows = {r["id"]: r for r in B.parse(OUT)}
        self.assertEqual(sorted(rows), ["bg-bg7", "bg-bg8", "claude-11112222", "claude-667adf9b", "claude-aaaabbbb"])
        self.assertEqual(rows["claude-667adf9b"]["state"], "working")
        self.assertNotIn("needs", rows["claude-667adf9b"])
        self.assertEqual((rows["claude-11112222"]["state"], rows["claude-11112222"]["needs"], rows["claude-11112222"]["needs_note"]), ("idle", "permission", "permission prompt"))
        self.assertEqual(rows["bg-bg7"]["needs"], "input")
        self.assertEqual(rows["bg-bg8"]["state"], "working")
        self.assertEqual(rows["claude-aaaabbbb"]["stunned"], "failed")

    def test_bad_output(self):
        for bad in ("", "Usage: claude agents [options]", "{}", "null", None):
            self.assertEqual(B.parse(bad), [])

    def test_merge_adds_only_unknown_sessions(self):
        agents = [{"id": "claude-667adf9b", "role": "", "state": "idle"}]
        out = B.merge(agents, B.parse(OUT), now=50)
        self.assertEqual(out[0], agents[0])                      # the hooks' own picture wins
        added = {a["id"]: a for a in out[1:]}
        self.assertEqual(len(added), 4)
        a = added["claude-11112222"]
        self.assertEqual((a["role"], a["state"], a["needs"], a["seen"], a["actions"], a["tools"]), ("background", "idle", "permission", 50, 0, {}))


class Runner(unittest.TestCase):
    def test_polls_with_fake_runner(self):
        calls = []

        def run(cmd, **k):
            calls.append((cmd, k["timeout"]))
            return SimpleNamespace(returncode=0, stdout=OUT)
        d = B.Discovery(timeout=3, run=run, which=lambda n: "/usr/bin/claude")
        self.assertEqual(len(d.poll_once()), 5)
        self.assertEqual(calls, [(["/usr/bin/claude", "agents", "--json"], 3)])
        v = d.version
        d.poll_once()
        self.assertEqual(d.version, v)          # same answer: no change to push

    def test_missing_command_old_version_and_timeouts(self):
        self.assertEqual(B.Discovery(which=lambda n: None, run=lambda *a, **k: 1 / 0).poll_once(), [])
        old = B.Discovery(which=lambda n: "claude", run=lambda c, **k: SimpleNamespace(returncode=1, stdout="error: unknown option '--json'"))
        self.assertEqual(old.poll_once(), [])
        ok = B.Discovery(which=lambda n: "claude", run=lambda c, **k: SimpleNamespace(returncode=0, stdout=OUT))
        ok.poll_once()

        def slow(c, **k):
            raise subprocess.TimeoutExpired(c, k["timeout"])
        ok.run = slow
        self.assertEqual(len(ok.poll_once()), 5)     # a slow ask keeps the last good list

    def test_thread_stops(self):
        stop = threading.Event()
        d = B.Discovery(interval=0.01, run=lambda c, **k: SimpleNamespace(returncode=0, stdout=OUT), which=lambda n: "claude")
        t = d.start(stop, first_delay=0)
        for _ in range(200):
            if d.sessions():
                break
            stop.wait(0.01)
        stop.set()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(d.sessions()), 5)


class Snapshot(unittest.TestCase):
    def test_ctx_snap_merges_background_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = os.path.join(tmp, "feed.jsonl")
            Feed(feed).append("agent_working", agent="claude-667adf9b", data={"activity": "x"})
            ctx = Ctx(load_all(), feed, settings_file=os.path.join(tmp, "s.json"), watch=False)
            self.assertEqual([a["id"] for a in ctx.snap()["agents"]], ["claude-667adf9b"])
            ctx.bg = B.Discovery(run=lambda c, **k: SimpleNamespace(returncode=0, stdout=OUT), which=lambda n: "claude")
            ctx.bg.poll_once()
            s = ctx.snap()
            ids = [a["id"] for a in s["agents"]]
            self.assertEqual(ids[0], "claude-667adf9b")
            self.assertEqual(len(ids), 5)
            self.assertEqual(next(a for a in s["agents"] if a["id"] == "bg-bg7")["role"], "background")


if __name__ == "__main__":
    unittest.main()
