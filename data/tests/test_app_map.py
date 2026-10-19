"""World map (D-260..): zone derivation from a synthetic git repo, path -> zone mapping, state transitions, bounded persistence, /api/zones."""
import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents.app import server, zones, zones_api
from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import load_all
from vanilla_agents.events.redact_util import clean
from vanilla_agents.events.schema import Event

CFG = load_all()
M = CFG["map"]
SPLIT = M["split_dirs"]


def git(repo, *a):
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(a), check=True, capture_output=True)


def make_repo(root):
    files = {"README.md": "a\nb\n", "Cargo.toml": "x\n", "docs/a.md": "1\n2\n3\n", "src/main.rs": "fn main(){}\n", "src/bin/t1.rs": "x\ny\n",
             "src/bin/t2.rs": "z\n", "src/audio/mod.rs": "q\n", "scripts/go.py": "print(1)\n"}
    for rel, txt in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="\n") as f:
            f.write(txt)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "x")


class Derive(unittest.TestCase):
    def test_zone_of(self):
        z = lambda p, d=False: zones.zone_of(p, SPLIT, d)
        self.assertEqual(z("docs/a.md"), "docs")
        self.assertEqual(z("src/bin/t1.rs"), "src/bin")
        self.assertEqual(z("src/main.rs"), "src/main.rs")
        self.assertEqual(z("Cargo.toml"), ".")
        self.assertEqual(z("scripts", True), "scripts")
        self.assertIsNone(z("src"))
        self.assertIsNone(z(""))
        self.assertEqual(z("a\\b\\c.txt"), "a")

    def test_rel_in_repo(self):
        r = os.path.abspath("somerepo")
        self.assertEqual(zones.rel_in_repo(os.path.join(r, "src", "x.rs"), r), "src/x.rs")
        self.assertEqual(zones.rel_in_repo(os.path.join(r, "src", "x.rs").upper(), r.upper()).lower(), "src/x.rs")
        self.assertIsNone(zones.rel_in_repo(os.path.abspath("somerepo2/x"), r))
        self.assertIsNone(zones.rel_in_repo(r, r))
        self.assertIsNone(zones.rel_in_repo(None, r))

    def test_synthetic_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            make_repo(d)
            zs = {z["id"]: z for z in zones.derive(d, M)}
            self.assertEqual(set(zs), {".", "docs", "src/main.rs", "src/bin", "src/audio", "scripts"})
            self.assertEqual((zs["src/bin"]["files"], zs["src/bin"]["lines"]), (2, 3))
            self.assertEqual((zs["."]["files"], zs["."]["lines"]), (2, 3))
            self.assertEqual(len(zones.head_of(d, M)), 40)

    def test_cap_merges_the_smallest_into_other(self):
        zl = [{"id": "z%02d" % i, "files": 1, "bytes": i, "lines": i} for i in range(10)]
        m = dict(M, zone_max=4)
        out = zones.cap(zl, m)
        self.assertEqual([z["id"] for z in out], ["z07", "z08", "z09", "other"])
        self.assertEqual(out[-1]["files"], 7)
        self.assertEqual(len(out[-1]["merged"]), 7)
        self.assertEqual(len(zones.cap(zl[:3], m)), 3)

    def test_not_a_git_folder_is_a_plain_problem(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OSError):
                zones.derive(d, M)


def build(zl, touches, runs, here=None):
    return {z["id"]: z for z in zones.build(zl, touches, runs, here or {}, M, {}, 1000.0)}


ZL = [{"id": "a", "files": 1, "bytes": 1, "lines": 1}, {"id": "b", "files": 2, "bytes": 2, "lines": 2}, {"id": "c", "files": 1, "bytes": 1, "lines": None}]
RUN = lambda ts, v="VERIFIED", commit="c" * 40: {"run_id": "r", "ts": ts, "verdict": v, "commit": commit}


class States(unittest.TestCase):
    def test_untouched_is_fog(self):
        self.assertEqual(build(ZL, {}, [RUN(500)])["a"]["state"], "UNDISCOVERED")

    def test_touch_makes_explored_with_edit_marker(self):
        z = build(ZL, {"a": {"touch": 100, "edit": 100}}, [])["a"]
        self.assertEqual((z["state"], z["edited_since_check"]), ("EXPLORED", True))
        z = build(ZL, {"a": {"touch": 100, "edit": 0}}, [])["a"]            # read only: no edit marker
        self.assertEqual((z["state"], z["edited_since_check"]), ("EXPLORED", False))

    def test_cleared_needs_verified_run_after_last_edit_with_commit(self):
        t = {"a": {"touch": 100, "edit": 100}}
        self.assertEqual(build(ZL, t, [RUN(99)])["a"]["state"], "EXPLORED")           # run before the edit
        self.assertEqual(build(ZL, t, [RUN(150, "FAILED")])["a"]["state"], "EXPLORED")
        self.assertEqual(build(ZL, t, [RUN(150, "BLOCKED")])["a"]["state"], "EXPLORED")
        self.assertEqual(build(ZL, t, [RUN(150, commit="")])["a"]["state"], "EXPLORED")  # no commit recorded
        z = build(ZL, t, [RUN(150)])["a"]
        self.assertEqual((z["state"], z["edited_since_check"], z["commit"]), ("CLEARED", False, "c" * 12))

    def test_edit_after_clear_goes_back_to_explored(self):
        z = build(ZL, {"a": {"touch": 300, "edit": 300}}, [RUN(150)])["a"]
        self.assertEqual((z["state"], z["edited_since_check"]), ("EXPLORED", True))

    def test_untouched_zone_is_never_cleared_by_a_run(self):
        self.assertEqual(build(ZL, {"a": {"touch": 1, "edit": 1}}, [RUN(150)])["b"]["state"], "UNDISCOVERED")

    def test_other_folds_merged_zones_and_here_marker(self):
        zl = [{"id": "other", "files": 3, "bytes": 3, "lines": None, "merged": ["m1", "m2"]}]
        z = build(zl, {"m2": {"touch": 5, "edit": 0}}, [], {"builder": "m1", "scribe": "zz"})["other"]
        self.assertEqual((z["state"], z["here"], z["last_touch"]), ("EXPLORED", ["builder"], 5))

    def test_grid_positions_are_deterministic(self):
        zl = [{"id": "z%d" % i, "files": 1, "bytes": 1, "lines": 1} for i in range(8)]
        out = zones.build(zl, {}, [], {}, M, {}, 0)
        self.assertEqual((out[0]["col"], out[0]["row"], out[7]["col"], out[7]["row"]), (0, 0, 1, 1))


def ev(i, tool, f, agent="builder", ts=None):
    return Event(i, ts or float(i), "tool_use", None, agent, {"tool": tool, "file": clean(f)})  # the feed redacts paths before they reach the app


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmp.name, "sub", "zone-activity.json")
        self.now = 10 ** 6
        self.clock = lambda: self.now

    def tearDown(self):
        self.tmp.cleanup()

    def act(self, **m):
        return zones.Activity(self.p, dict(M, **m), self.clock)

    def test_survives_restart_and_edit_is_tracked_separately(self):
        a = self.act()
        a.touch("P", "src/bin", self.now - 50, False)
        a.touch("P", "src/bin", self.now - 40, True)
        a.touch("P", "src/bin", self.now - 60, True)        # older than the newest edit: ignored
        a.flush()
        self.assertEqual(self.act().get("P")["src/bin"], {"touch": self.now - 40, "edit": self.now - 40})

    def test_old_entries_expire(self):
        a = self.act(activity_keep_days=1)
        a.touch("P", "old", self.now - 2 * 86400, True)
        a.touch("P", "new", self.now - 10, True)
        a.flush()
        self.assertEqual(set(self.act(activity_keep_days=1).get("P")), {"new"})

    def test_zone_and_project_counts_are_capped_keeping_the_newest(self):
        a = self.act(activity_max_zones=3, activity_max_projects=2)
        for i in range(10):
            a.touch("P", "z%d" % i, self.now - 100 + i, False)
        for j, p in enumerate(["Q", "R", "S"]):
            a.touch(p, "x", self.now - 50 + j, False)
        a.flush()
        b = self.act(activity_max_zones=3, activity_max_projects=2)
        self.assertEqual(b.get("P"), {})          # P is the oldest project: dropped
        self.assertEqual(set(b.d), {"R", "S"})

    def test_zone_cap_keeps_the_newest_zones(self):
        a = self.act(activity_max_zones=3)
        for i in range(10):
            a.touch("P", "z%d" % i, self.now - 100 + i, False)
        a.flush()
        self.assertEqual(set(self.act(activity_max_zones=3).get("P")), {"z7", "z8", "z9"})

    def test_damaged_file_is_ignored(self):
        os.makedirs(os.path.dirname(self.p))
        for bad in ("{torn", "[1,2]", '{"projects": {"P": {"z": 5}}}', ""):
            with open(self.p, "w") as f:
                f.write(bad)
            self.assertEqual(self.act().d, {})

    def test_file_stays_small(self):
        a = self.act()
        for i in range(2000):
            a.touch("P%d" % (i % 50), "zone-%d" % i, self.now - i, True)
        a.flush()
        self.assertLess(os.path.getsize(self.p), 8 * M["activity_max_zones"] * M["activity_max_projects"] * 4)

    def test_tracker_maps_tool_events(self):
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "scripts"))
            a = self.act()
            t = zones.Tracker(repo, M, a)
            t.on_tool(ev(1, "Read", os.path.join(repo, "src", "bin", "x.rs"), ts=10.0))
            t.on_tool(ev(2, "Edit", os.path.join(repo, "docs", "a.md"), ts=11.0))
            t.on_tool(ev(3, "Grep", os.path.join(repo, "scripts"), ts=12.0))       # a folder as the search path
            t.on_tool(ev(4, "Bash", os.path.join(repo, "docs", "b.md"), ts=13.0))  # not a file tool: ignored
            t.on_tool(ev(5, "Edit", os.path.join(os.path.dirname(repo), "elsewhere.txt"), ts=14.0))   # outside the repo
            t.on_tool(ev(6, "Edit", os.path.join(repo, "src"), ts=15.0))           # a bare split dir
            g = a.get(repo)
            self.assertEqual(set(g), {"src/bin", "docs", "scripts"})
            self.assertEqual((g["docs"]["edit"], g["src/bin"]["edit"], g["scripts"]["touch"]), (11.0, 0, 12.0))
            self.assertEqual(t.last["builder"], ("scripts", 12.0))


class Config(unittest.TestCase):
    def test_bad_map_settings_are_caught(self):
        c = json.loads(json.dumps(CFG))
        c["map"]["zone_max"] = 99
        c["map"]["states"]["CLEARED"]["color"] = "pink"
        del c["map"]["states"]["EXPLORED"]
        p = " ".join(check_app(c))
        self.assertIn("zone_max", p)
        self.assertIn("pink", p)
        self.assertIn("EXPLORED", p)


class Api(unittest.TestCase):
    def test_zones_endpoint_and_config_check(self):
        from vanilla_agents.app.state import AppState
        from tests.test_app_verify import Base
        self.assertEqual([p for p in check_app(CFG) if "map" in p.lower() or "zones" in p], [])
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(repo)
            make_repo(repo)
            act = os.path.join(d, "act.json")
            cfg = json.loads(json.dumps(CFG))
            cfg["map"]["activity_path"] = act
            feed = os.path.join(d, "feed.jsonl")
            from vanilla_agents.events.feed import Feed
            f = Feed(feed)
            f.append("tool_use", None, "builder", {"tool": "Edit", "file": os.path.join(repo, "docs", "a.md")})
            f.append("agent_working", None, "builder", {"activity": "x"})
            ctx = server.Ctx(cfg, feed, repo, settings_file=os.path.join(d, "s.json"))
            ctx.verify.runs_path = os.path.join(d, "runs.jsonl")
            srv = server.make_server(ctx)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
                c.request("GET", "/api/zones")
                r = c.getresponse()
                z = json.loads(r.read())
                self.assertEqual(r.status, 200)
                byid = {x["id"]: x for x in z["zones"]}
                self.assertEqual(byid["docs"]["state"], "EXPLORED")
                self.assertEqual(byid["docs"]["here"], ["builder"])
                self.assertEqual(byid["scripts"]["state"], "UNDISCOVERED")
                self.assertTrue(byid["docs"]["eli5"])
                self.assertEqual(byid["."]["name"], CFG["strings"]["ui"]["map.root_name"])
                self.assertTrue(os.path.isfile(act))
                self.assertNotIn("a.md", Path(act).read_text())          # paths/contents are not stored, only zone ids and times
            finally:
                srv.shutdown()
                srv.server_close()
                ctx.stop.set()

    def test_no_project_and_not_git(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = server.Ctx(CFG, os.path.join(d, "f.jsonl"), None, settings_file=os.path.join(d, "s.json"))
            self.assertEqual(zones_api.payload(CFG, ctx.zone_cache, ctx.state, os.path.join(d, "r.jsonl"))["problem"], "no_project")
            ctx2 = server.Ctx(CFG, os.path.join(d, "f.jsonl"), d, settings_file=os.path.join(d, "s.json"))
            self.assertEqual(zones_api.payload(CFG, ctx2.zone_cache, ctx2.state, os.path.join(d, "r.jsonl"))["problem"], "no_project")   # no .git: no tracker


if __name__ == "__main__":
    unittest.main()
