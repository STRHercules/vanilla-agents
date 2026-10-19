"""V-047: the world map's fog (explored zones) and the hero dots use one path rule (zones.repo_rel): relative paths, the helper's own
git worktree, Windows spelling; paths outside the project are ignored. A zone with a dot is never left in fog."""
import json
import os
import tempfile
import unittest

from tests.test_app_map import CFG, M, SPLIT, ev, git, make_repo
from vanilla_agents.app import server, zones, zones_api
from vanilla_agents.events.feed import Feed


class RepoRel(unittest.TestCase):
    def test_relative_paths(self):
        self.assertEqual(zones.repo_rel("src/net/a.rs", "/r"), "src/net/a.rs")
        self.assertEqual(zones.repo_rel("./docs/a.md", "/r"), "docs/a.md")
        self.assertEqual(zones.repo_rel("docs//x/../a.md", "/r"), "docs/a.md")
        self.assertEqual(zones.repo_rel("docs\\a.md", "/r"), "docs/a.md")
        for out in ("../other/x.rs", "..", ".", "", "   ", None, 5):
            self.assertIsNone(zones.repo_rel(out, "/r"), out)

    def test_absolute_inside_and_outside(self):
        self.assertEqual(zones.repo_rel("/r/src/a.rs", "/r"), "src/a.rs")
        self.assertEqual(zones.repo_rel("/r/../r/src/a.rs", "/r/"), "src/a.rs")
        self.assertIsNone(zones.repo_rel("/r", "/r"))
        self.assertIsNone(zones.repo_rel("/rr/src/a.rs", "/r"))
        self.assertIsNone(zones.repo_rel("/elsewhere/a.rs", "/r"))
        self.assertIsNone(zones.repo_rel("/r/../etc/passwd", "/r"))

    def test_windows_paths(self):
        root = "C:\\Users\\[user]\\proj"
        self.assertEqual(zones.repo_rel("C:\\Users\\[user]\\proj\\src\\net\\a.rs", root), "src/net/a.rs")
        self.assertEqual(zones.repo_rel("c:/users/[user]/PROJ/docs/a.md", root), "docs/a.md")      # drive paths ignore case
        self.assertEqual(zones.repo_rel("C:/Users/[user]/proj/./web\\x.html", "C:/Users/[user]/proj/"), "web/x.html")
        self.assertIsNone(zones.repo_rel("D:\\proj\\src\\a.rs", root))
        self.assertIsNone(zones.repo_rel("C:\\Users\\[user]\\proj2\\a.rs", root))
        self.assertEqual(zones.zone_for_file("C:\\Users\\[user]\\proj\\src\\net\\a.rs", root, SPLIT), "src/net")

    def test_worktree_root(self):
        self.assertEqual(zones.repo_rel("/wt/web/b.html", "/r", "/wt"), "web/b.html")
        self.assertIsNone(zones.repo_rel("/wt/web/b.html", "/r"))
        self.assertEqual(zones.repo_rel("/r/web/b.html", "/r", "/wt"), "web/b.html")


class TrackerPaths(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.act = zones.Activity(os.path.join(self.d.name, "act.json"), M)
        self.repo = os.path.join(self.d.name, "repo")
        os.makedirs(os.path.join(self.repo, "scripts"))

    def tearDown(self):
        self.d.cleanup()

    def test_relative_and_outside(self):
        t = zones.Tracker(self.repo, M, self.act)
        t.on_tool(ev(1, "Read", "src/net/a.rs", ts=10.0))
        t.on_tool(ev(2, "Edit", "./docs/a.md", ts=11.0))
        t.on_tool(ev(3, "Edit", "../elsewhere/x.md", ts=12.0))
        t.on_tool(ev(4, "Read", "/somewhere/else/y.md", ts=13.0))
        self.assertEqual(set(self.act.get(self.repo)), {"src/net", "docs"})
        self.assertEqual(t.last["builder"], ("docs", 11.0))

    def test_worktree_only_when_same_repo(self):
        asked = []
        t = zones.Tracker(self.repo, M, self.act, lambda r: asked.append(r) or r == "/wt")
        t.on_tool(ev(1, "Edit", "/wt/web/b.html", ts=10.0), "/wt")
        t.on_tool(ev(2, "Edit", "/wt/web/c.html", ts=11.0), "/wt")
        t.on_tool(ev(3, "Edit", "/other/tests/c.rs", ts=12.0), "/other")      # a checkout of another project: ignored
        t.on_tool(ev(4, "Edit", "/wt2/tests/c.rs", ts=13.0))                   # no checkout reported: ignored
        self.assertEqual(set(self.act.get(self.repo)), {"web"})
        self.assertEqual(asked, ["/wt", "/other"])                              # asked once per root

    def test_windows_project(self):
        t = zones.Tracker("C:\\Users\\me\\proj", M, self.act)
        t.root = "C:\\Users\\[user]\\proj"       # what clean() makes of it on Windows
        t.on_tool(zones_ev(1, "Edit", "C:\\Users\\[user]\\proj\\src\\audio\\mod.rs"))
        t.on_tool(zones_ev(2, "Read", "c:/users/[user]/proj/docs/a.md"))
        t.on_tool(zones_ev(3, "Read", "C:\\Users\\[user]\\elsewhere\\a.md"))
        self.assertEqual(set(self.act.get("C:\\Users\\me\\proj")), {"src/audio", "docs"})


def zones_ev(i, tool, f):
    from vanilla_agents.events.schema import Event
    return Event(i, float(i), "tool_use", None, "builder", {"tool": tool, "file": f})


class DotsNeverInFog(unittest.TestCase):
    def test_worktree_and_relative_paths_explore_the_dot_zone(self):
        with tempfile.TemporaryDirectory() as d:
            repo, wt = os.path.join(d, "repo"), os.path.join(d, "wt")
            os.makedirs(repo)
            make_repo(repo)
            git(repo, "worktree", "add", "-q", "-b", "side", wt)
            cfg = json.loads(json.dumps(CFG))
            cfg["map"]["activity_path"] = os.path.join(d, "act.json")
            feed = os.path.join(d, "feed.jsonl")
            f = Feed(feed)
            f.append("agent_started", None, "w", {})
            f.append("agent_place", None, "w", {"cwd": wt, "root": wt, "worktree": "wt", "branch": "side"})
            f.append("tool_use", None, "w", {"tool": "Edit", "file": os.path.join(wt, "scripts", "go.py")})
            f.append("agent_started", None, "r", {})
            f.append("tool_use", None, "r", {"tool": "Edit", "file": "docs/a.md"})
            f.append("agent_started", None, "x", {})
            f.append("tool_use", None, "x", {"tool": "Edit", "file": os.path.join(d, "outside", "README.md")})
            ctx = server.Ctx(cfg, feed, repo, settings_file=os.path.join(d, "s.json"))
            try:
                ctx.state.refresh()
                self.assertEqual(set(ctx.state.tracker.act.get(repo)), {"scripts", "docs"})      # the tracker itself, not only the safety net
                z = zones_api.payload(cfg, ctx.zone_cache, ctx.state, os.path.join(d, "runs.jsonl"))
            finally:
                ctx.stop.set()
            byid = {x["id"]: x for x in z["zones"]}
            dots = {h["agent"]: h["zone"] for h in z["heroes"]}
            self.assertEqual(dots, {"w": "scripts", "r": "docs"})
            self.assertEqual((byid["scripts"]["state"], byid["docs"]["state"]), ("EXPLORED", "EXPLORED"))
            self.assertEqual(byid["."]["state"], "UNDISCOVERED")        # the outside README did not explore the root zone
            for h in z["heroes"]:
                self.assertNotEqual(byid[h["zone"]]["state"], "UNDISCOVERED")

    def test_dot_from_another_tool_still_clears_fog(self):
        built = [{"id": "web", "state": "UNDISCOVERED", "last_touch": None}]
        spots = zones.hero_spots([{"file": "web/x.html", "agent": "a", "ts": 7}], {}, "/r", SPLIT, built)
        self.assertEqual(spots[0]["zone"], "web")


if __name__ == "__main__":
    unittest.main()
