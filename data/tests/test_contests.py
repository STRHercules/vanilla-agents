"""V-045: competing fixes (open / propose / check one / pick the winner, owner-only, read-only branch diff) and World map hero dots."""
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from collections import namedtuple
from pathlib import Path

from vanilla_agents import mcp
from vanilla_agents.app import contests, server, zones
from vanilla_agents.app.config import load_all
from vanilla_agents.app.verify_job import VerifyJob
from vanilla_agents.events.feed import Feed

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
Usage = namedtuple("Usage", "total used free")


def make_repo(base):
    """<base>/repo on main with two fix branches: fix-a (a plain branch) and fix-b (checked out in the worktree <base>/wt-b)."""
    repo = Path(base) / "repo"
    repo.mkdir()
    run = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, env=ENV, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "game.py").write_text("speed = 1\n")
    (repo / "README.md").write_text("hello\n")
    run("add", ".")
    run("commit", "-qm", "start")
    run("checkout", "-qb", "fix-a")
    (repo / "src" / "game.py").write_text("speed = 2\njump = True\n")
    run("commit", "-qam", "fix a")
    run("checkout", "-q", "main")
    wt = Path(base) / "wt-b"
    run("worktree", "add", "-q", "-b", "fix-b", str(wt))
    (wt / "README.md").write_text("hello there\n")
    (wt / "docs.txt").write_text("new\n")
    run("add", ".", cwd=wt)
    run("commit", "-qm", "fix b", cwd=wt)
    return repo, wt


@unittest.skipUnless(shutil.which("git"), "git not installed")
class Contests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.repo, self.wt = make_repo(t)
        self.cfg = load_all()
        a = self.cfg["app"]
        a.update(approvals_path=os.path.join(t, "work", "approvals.json"), projects_path=os.path.join(t, "projects.json"),
                 verify_work_root=os.path.join(t, "verify"), runs_path=os.path.join(t, "runs.jsonl"))
        with open(a["projects_path"], "w") as f:
            json.dump({str(self.repo): {"repo": str(self.repo), "trusted_dir": t, "expected_manifest_hash": "h", "criteria": ["unit_tests"], "default_base_ref": "HEAD"}}, f)
        self.cpath = contests.path_for(a)
        self.feed_path = os.path.join(t, "feed.jsonl")
        self.feed = Feed(self.feed_path)
        self.feed.append("task_created", task="q1", data={"title": "Faster hero"})
        self.feed.append("task_created", task="q2", data={"title": "Not competing"})
        self.seen = []

        def verifier(req):
            self.seen.append(req)
            return {"outcome": "VERIFIED", "checks": [{"check_id": "unit_tests", "status": "VERIFIED"}], "not_verified": []}, {}
        self.ctx = server.Ctx(self.cfg, self.feed_path, str(self.repo), assets_dir=t, settings_file=os.path.join(t, "s.json"), watch=False)
        self.ctx.verify = VerifyJob(self.cfg, self.feed_path, os.path.join(t, "s.json"), verifier=verifier, usage=lambda p: Usage(1 << 50, 0, 1 << 45))
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tools = mcp.Tools(self.cfg, self.feed_path)

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()

    def http(self, method, path, body=None, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **({"X-Vanilla-Key": self.ctx.key} if key else {})}
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        out = r.status, json.loads(r.read())
        c.close()
        return out

    def contest_post(self, **body):
        return self.http("POST", "/api/contest", body)

    def propose(self, **kw):
        kw.setdefault("summary", "makes the hero faster")
        return contests.propose(self.feed, self.cpath, kw.pop("quest", "q1"), kw.pop("agent", "claude-a"), kw.pop("summary"), kw.pop("branch", None), kw.pop("worktree", None))

    def task(self, snap, qid="q1"):
        return next(t for t in snap["tasks"] if t["id"] == qid)

    # ---- opening (owner only) ----
    def test_open_needs_the_key_and_valid_tries(self):
        self.assertEqual(self.http("POST", "/api/contest", {"action": "open", "quest": "q1", "tries": 2}, key=False)[0], 403)
        self.assertEqual(self.contest_post(action="open", quest="q1", tries=7)[0], 409)
        self.assertEqual(self.contest_post(action="open", quest="nope", tries=2)[0], 409)
        self.assertEqual(self.contest_post(action="pick", quest="q1")[0], 400)          # agent missing
        st, snap = self.contest_post(action="open", quest="q1", tries=2)
        self.assertEqual(st, 200)
        self.assertEqual(self.task(snap)["contest"], {"tries": 2, "base": "main", "open": True, "winner": None, "closed": False,
                                                      "ts": self.task(snap)["contest"]["ts"], "proposals": []})
        self.assertEqual(self.contest_post(action="open", quest="q1", tries=2)[0], 409)        # already open
        self.assertIn("contest_opened", [e.type for e in self.feed.read_since(0, 100)])

    def test_a_feed_line_alone_opens_or_wins_nothing(self):
        self.feed.append("contest_opened", task="q1", data={"title": "Faster hero", "tries": "4"})
        self.feed.append("fix_picked", task="q1", agent="claude-x", data={"title": "Faster hero", "winner": "claude-x"})
        self.ctx.state.refresh()
        self.assertNotIn("contest", self.task(self.ctx.snap()))
        with self.assertRaises(contests.ContestError):
            self.propose()

    # ---- proposals (MCP) ----
    def test_proposal_validation(self):
        with self.assertRaisesRegex(contests.ContestError, "no quest called"):
            self.propose(quest="ghost", branch="fix-a")
        with self.assertRaisesRegex(contests.ContestError, "not open for competing"):
            self.propose(quest="q2", branch="fix-a")
        self.contest_post(action="open", quest="q1", tries=2)
        for bad in ("--upload-pack=evil", "-x", "fix-a;rm -rf /", "../main", "a..b", "fix a", "HEAD", "x.lock", "refs/heads/../../x", "fix-a@{1}", "nope"):
            with self.assertRaises(contests.ContestError, msg=bad):
                self.propose(branch=bad)
        for bad in (str(Path(self.tmp.name) / "wt-b" / ".." / ".." ), "/etc", str(self.wt) + "/../repo/../../", "../wt-b"):
            with self.assertRaises(contests.ContestError, msg=bad):
                self.propose(worktree=bad, agent="claude-z")
        with self.assertRaisesRegex(contests.ContestError, "base branch"):
            self.propose(branch="main")
        with self.assertRaisesRegex(contests.ContestError, "summary"):
            self.propose(branch="fix-a", summary="  ")
        with self.assertRaisesRegex(contests.ContestError, "not fix-a"):
            self.propose(worktree=str(self.wt), branch="fix-a")
        r = self.propose(branch="fix-a", agent="claude-a")
        self.assertEqual((r["branch"], r["files"], r["added"], r["removed"]), ("fix-a", 1, 2, 1))
        r = self.propose(worktree=str(self.wt), agent="claude-b", summary="docs too")
        self.assertEqual((r["branch"], r["files"], Path(r["worktree"]).resolve()), ("fix-b", 2, self.wt.resolve()))
        with self.assertRaisesRegex(contests.ContestError, "tries"):
            self.propose(branch="fix-a", agent="claude-c")                  # too many: both tries are taken
        self.propose(branch="fix-a", agent="claude-a", summary="better")     # the same helper may send its fix again
        with self.assertRaisesRegex(contests.ContestError, "already claude-a's proposal"):
            self.propose(branch="fix-a", agent="claude-b", summary="mine now")      # V-049: no taking over another helper's branch
        with self.assertRaisesRegex(contests.ContestError, "already claude-b's proposal"):
            self.propose(branch="fix-b", agent="claude-a", summary="swap")          # V-049: nor swapping to someone else's
        self.assertEqual(contests.proposals(self.feed, "q1", 0)["claude-a"]["branch"], "fix-a")
        self.ctx.state.refresh()
        props = self.task(self.ctx.snap())["contest"]["proposals"]
        self.assertEqual([(p["agent"], p["branch"], p["summary"]) for p in props], [("claude-b", "fix-b", "docs too"), ("claude-a", "fix-a", "better")])
        self.assertEqual(props[0]["files"], ["README.md", "docs.txt"])

    def test_mcp_tool_and_list_quests(self):
        self.contest_post(action="open", quest="q1", tries=3)
        res = mcp.handle(self.tools, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "propose_fix", "arguments": {"quest": "q1", "summary": "x", "branch": "-rf"}}})
        self.assertTrue(res["result"]["isError"])
        res = mcp.handle(self.tools, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "propose_fix", "arguments": {"quest": "q1", "summary": "faster", "branch": "fix-a", "agent": "claude-a"}}})
        self.assertNotIn("isError", res["result"])
        q = next(x for x in self.tools.list_quests() if x["id"] == "q1")
        self.assertEqual(q["competition"], {"open": True, "tries": 3, "base": "main", "proposals": 1, "by": ["claude-a"]})

    def test_no_tool_can_pick_or_open(self):
        names = {t["name"] for t in mcp.tool_list()}
        self.assertIn("propose_fix", names)
        self.assertFalse({n for n in names if any(w in n for w in ("pick", "winner", "contest", "compete", "approve", "verify", "pause", "done"))} - {"done_for_now"})
        self.assertFalse(any(hasattr(self.tools, n) for n in ("pick_winner", "pick", "open_contest", "owner_pick")))
        self.assertNotIn("/api/contest", server.PHONE_CALLS)

    # ---- check one and pick the winner (owner only) ----
    def test_check_one_runs_on_that_branch_quietly(self):
        self.contest_post(action="open", quest="q1", tries=2)
        self.propose(branch="fix-a", agent="claude-a")
        before = [e.type for e in self.feed.read_since(0, 100)]
        st, _ = self.contest_post(action="check", quest="q1", agent="claude-a")
        self.assertEqual(st, 200)
        self.ctx.verify.thread.join(10)
        tip = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "fix-a"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.seen[0]["base_commit"], tip)
        new = [e for e in self.feed.read_since(0, 100)][len(before):]
        self.assertEqual([e.type for e in new], ["fix_checked"])           # no task_ready / task_verified for the project's own quest
        self.assertEqual((new[0].agent, new[0].data["verdict"], new[0].data["branch"]), ("claude-a", "VERIFIED", "fix-a"))
        self.assertFalse(os.path.exists(self.cfg["app"]["runs_path"]))     # zones and auto-detect read only the project's own runs
        self.ctx.state.refresh()
        self.assertEqual(self.task(self.ctx.snap())["contest"]["proposals"][0]["verdict"], "VERIFIED")
        self.assertEqual(self.contest_post(action="check", quest="q1", agent="claude-nobody")[0], 409)

    def test_pick_winner_flow(self):
        self.contest_post(action="open", quest="q1", tries=3)
        self.propose(branch="fix-a", agent="claude-a")
        self.propose(worktree=str(self.wt), agent="claude-b")
        self.ctx.state.refresh()
        snap = self.ctx.snap()
        self.assertEqual({a["id"]: a.get("contest") for a in snap["agents"]}, {"claude-a": "q1", "claude-b": "q1"})
        main_before = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "main"], capture_output=True, text=True).stdout
        self.assertEqual(self.http("POST", "/api/contest", {"action": "pick", "quest": "q1", "agent": "claude-b"}, key=False)[0], 403)
        self.assertEqual(self.contest_post(action="pick", quest="q1", agent="claude-zz")[0], 409)
        st, snap = self.contest_post(action="pick", quest="q1", agent="claude-b")
        self.assertEqual(st, 200)
        con = self.task(snap)["contest"]
        self.assertEqual((con["open"], con["winner"]), (False, "claude-b"))
        self.assertFalse(any(a.get("contest") for a in snap["agents"]))
        evs = self.feed.read_since(0, 100)
        picked = [e for e in evs if e.type == "fix_picked"]
        self.assertEqual((picked[0].data["winner"], picked[0].data["base"], picked[0].data["branch"]), ("claude-b", "main", "fix-b"))
        msgs = {e.data["to"]: e.data["text"] for e in evs if e.type == "message_sent"}
        self.assertIn("merge it into main", msgs["claude-b"])
        self.assertIn("picked another one", msgs["claude-a"])
        self.assertEqual(self.contest_post(action="pick", quest="q1", agent="claude-a")[0], 409)       # closed
        with self.assertRaisesRegex(contests.ContestError, "over"):
            self.propose(branch="fix-a", agent="claude-a")
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "rev-parse", "main"], capture_output=True, text=True).stdout, main_before)
        self.assertEqual((self.repo / "README.md").read_text(), "hello\n")      # nothing merged

    def test_quest_helper_competes_before_it_proposes(self):
        """V-047: the inn seats every competitor at one table; the helper on the quest counts from the moment the competition opens."""
        self.feed.append("agent_started", agent="claude-q")
        self.feed.append("quest_claimed", task="q1", agent="claude-q", data={"title": "Faster hero"})
        self.feed.append("agent_started", agent="claude-x")
        self.contest_post(action="open", quest="q1", tries=2)
        self.propose(branch="fix-a", agent="claude-a")
        self.ctx.state.refresh()
        got = {a["id"]: a.get("contest") for a in self.ctx.snap()["agents"]}
        self.assertEqual(got, {"claude-q": "q1", "claude-a": "q1", "claude-x": None})
        st, snap = self.contest_post(action="pick", quest="q1", agent="claude-a")
        self.assertEqual(st, 200)
        self.assertFalse(any(a.get("contest") for a in snap["agents"]))         # closed: everybody back to normal
        self.assertEqual(self.task(snap)["contest"]["winner"], "claude-a")       # the inn reads the winner here (cheer / applaud)

    def test_stop(self):
        self.contest_post(action="open", quest="q1", tries=2)
        st, snap = self.contest_post(action="stop", quest="q1")
        self.assertEqual((st, self.task(snap)["contest"]["open"], self.task(snap)["contest"]["closed"]), (200, False, True))
        self.assertEqual(self.contest_post(action="stop", quest="q1")[0], 409)
        self.assertEqual(self.contest_post(action="open", quest="q1", tries=2)[0], 200)          # may be opened again

    def test_zones_payload_has_hero_dots(self):
        self.cfg["map"]["activity_path"] = os.path.join(self.tmp.name, "work", "zones.json")
        self.feed.append("agent_started", agent="claude-w", data={})
        self.feed.append("agent_place", agent="claude-w", data={"cwd": str(self.wt), "root": str(self.wt), "branch": "fix-b"})
        self.feed.append("tool_use", agent="claude-w", data={"tool": "Edit", "file": str(self.wt / "src" / "game.py")})
        self.feed.append("agent_started", agent="claude-m", data={})
        self.feed.append("tool_use", agent="claude-m", data={"tool": "Edit", "file": "README.md"})
        self.ctx.open_project(str(self.repo))
        st, d = self.http("GET", "/api/zones")
        self.assertEqual(st, 200)
        self.assertEqual(sorted((h["agent"], h["zone"]) for h in d["heroes"]), [("claude-m", "."), ("claude-w", "src/game.py")])

    # ---- the diff for a branch: read-only, this project only ----
    def test_branch_diff_read_only(self):
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        st, d = self.http("GET", "/api/diff?branch=fix-b&base=main")
        self.assertEqual((st, sorted(f["file"] for f in d["files"])), (200, ["README.md", "docs.txt"]))
        st, d = self.http("GET", "/api/diff?branch=fix-b&base=main&file=README.md")
        self.assertIn("+hello there", d["diff"])
        st, d = self.http("GET", "/api/diff?branch=fix-a")          # base defaults to the project's own branch
        self.assertEqual(d["base"], "main")
        for q in ("branch=--output=/tmp/x", "branch=fix-a&base=-p", "branch=nope", "branch=fix-a&file=../../etc/passwd", "branch=fix-a&file=/etc/passwd",
                  "branch=fix-a&file=-rf", "branch=a..b", "branch=fix-a%00"):
            st, d = self.http("GET", "/api/diff?" + q)
            self.assertIn("error", d, q)
        self.assertFalse(os.path.exists("/tmp/x"))
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout, head)
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True, text=True).stdout, "")
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        self.assertIn("error", contests.branch_diff(str(other), "fix-a"))        # not a git folder: nothing


class RefsAndDots(unittest.TestCase):
    def test_valid_ref(self):
        for ok in ("main", "fix-a", "feat/x_1.2", "HEAD"):
            self.assertTrue(contests.valid_ref(ok), ok)
        for bad in ("", "-x", "--upload-pack=x", "a..b", "a//b", "x.lock", "a/", "a.", "a@{1}", "a b", "a;b", "a/.b", "x" * 101, None, 3, "ü"):
            self.assertFalse(contests.valid_ref(bad), bad)

    def test_file_to_zone_like_the_map(self):
        split = ["src"]
        self.assertEqual(zones.zone_for_file("src/movement.rs", "/r", split), "src/movement.rs")
        self.assertEqual(zones.zone_for_file("src/net/a.rs", "/r", split), "src/net")
        self.assertEqual(zones.zone_for_file("web/login.html", "/r", split), "web")
        self.assertEqual(zones.zone_for_file("./README.md", "/r", split), ".")
        self.assertEqual(zones.zone_for_file("/r/tests/combat.rs", "/r", split), "tests")
        self.assertIsNone(zones.zone_for_file("/elsewhere/x.rs", "/r", split))
        self.assertIsNone(zones.zone_for_file("", "/r", split))
        built = [{"id": "."}, {"id": "src/movement.rs"}, {"id": "web"}, {"id": "other", "merged": ["tests", "docs"]}]
        files = [{"file": "docs/README.md", "agent": "b", "ts": 50}, {"file": "web/a.css", "agent": "a", "ts": 40}, {"file": "src/movement.rs", "agent": "a", "ts": 10},
                 {"file": "/elsewhere/z", "agent": "c", "ts": 60}]
        spots = zones.hero_spots(files, {"a": ("src/movement.rs", 45), "d": ("nowhere", 99)}, "/r", split, built)
        self.assertEqual([(h["agent"], h["zone"]) for h in spots], [("b", "other"), ("a", "src/movement.rs")])       # the newest touch wins (a read at 45 beats an edit at 40)
        self.assertEqual(zones.hero_spots([], {}, "/r", split, built), [])
        wt = zones.hero_spots([{"file": "/wt/web/b.html", "agent": "w", "ts": 5}], {}, "/r", split, built, {"w": "/wt"})
        self.assertEqual([(h["agent"], h["zone"]) for h in wt], [("w", "web")])         # a helper in its own worktree of the project


if __name__ == "__main__":
    unittest.main()
