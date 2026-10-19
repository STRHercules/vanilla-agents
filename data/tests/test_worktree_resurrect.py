"""V-038 worktree awareness (checkout from plain file reads, agent_place events, same-file warning only inside one checkout) and
V-039 Resurrect (session id + folder kept, departed helpers, terminal plans per platform with a fake runner, the owner-only endpoint)."""
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents import mcp
from vanilla_agents.app import resurrect, server
from vanilla_agents.app.config import load_all
from vanilla_agents.app.state import Folder
from vanilla_agents.events import hooks
from vanilla_agents.events.feed import Feed
from vanilla_agents.events.worktree import branch_of, locate

SID = "0b5f0c3e-1b7a-4c1e-9d2c-7e1f5a9c0d11"


def fake_repo(base):
    """A main checkout <base>/repo (branch main) with a linked worktree <base>/wt-a (branch feat-a), made from files only."""
    repo, wt = Path(base) / "repo", Path(base) / "wt-a"
    (repo / ".git" / "worktrees" / "wt-a").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git" / "worktrees" / "wt-a" / "HEAD").write_text("ref: refs/heads/feat-a\n")
    (repo / "src").mkdir()
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: %s\n" % (repo / ".git" / "worktrees" / "wt-a"))
    return repo, wt


class Locate(unittest.TestCase):
    def test_main_linked_relative_detached_and_none(self):
        with tempfile.TemporaryDirectory() as d:
            repo, wt = fake_repo(d)
            self.assertEqual(locate(repo / "src"), {"root": str(repo), "worktree": "", "branch": "main", "repo": str(repo)})
            self.assertEqual(locate(wt / "src"), {"root": str(wt), "worktree": "wt-a", "branch": "feat-a", "repo": str(repo)})
            (wt / ".git").write_text("gitdir: ../repo/.git/worktrees/wt-a")          # relative gitdir
            self.assertEqual(locate(wt)["worktree"], "wt-a")
            (repo / ".git" / "HEAD").write_text("3f2a9c1d0e5b7a8c9d0e1f2a3b4c5d6e7f8a9b0c\n")
            self.assertEqual(locate(repo)["branch"], "detached at 3f2a9c1")
            plain = Path(d) / "plain"
            plain.mkdir()
            self.assertEqual(locate(plain), {})
            self.assertEqual(locate(Path(d) / "missing"), {})
            self.assertEqual(locate(""), {})
            (plain / ".git").write_text("not a gitdir line")
            self.assertEqual(locate(plain), {})
        self.assertEqual(branch_of("ref: refs/remotes/origin/x"), "refs/remotes/origin/x")
        self.assertEqual(branch_of("garbage"), "")

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_real_git_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "r"
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            run = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True)
            repo.mkdir()
            run("init", "-q", "-b", "trunk")
            (repo / "a.txt").write_text("a")
            run("add", "a.txt")
            run("commit", "-qm", "a")
            run("worktree", "add", "-q", "-b", "side", str(Path(d) / "second"))
            self.assertEqual(locate(repo)["branch"], "trunk")
            w = locate(Path(d) / "second")
            self.assertEqual((w["worktree"], w["branch"], Path(w["repo"]).resolve()), ("second", "side", repo.resolve()))


class Hooks(unittest.TestCase):
    def test_session_start_and_prompt_carry_the_place(self):
        with tempfile.TemporaryDirectory() as d:
            _repo, wt = fake_repo(d)
            evs = hooks.to_events({"hook_event_name": "SessionStart", "session_id": SID, "cwd": str(wt / "src")})
            self.assertEqual([e[0] for e in evs], ["agent_started", "agent_place"])
            self.assertEqual(evs[1][1], "claude-0b5f0c3e")
            self.assertEqual(evs[1][2], {"session": SID, "cwd": str(wt / "src"), "root": str(wt), "worktree": "wt-a", "branch": "feat-a"})
            self.assertEqual(hooks.to_events({"hook_event_name": "UserPromptSubmit", "session_id": SID, "cwd": str(wt)})[1][0], "agent_place")
            moved = hooks.to_events({"hook_event_name": "CwdChanged", "session_id": SID, "new_cwd": d})
            self.assertEqual(moved[1][2], {"session": SID, "cwd": d})          # not a checkout: no root/branch
            sub = hooks.to_events({"hook_event_name": "SessionStart", "session_id": SID, "agent_type": "explorer", "cwd": d})
            self.assertNotIn("session", sub[1][2])          # a sub-agent must not resume its parent's conversation
            bad = hooks.to_events({"hook_event_name": "SessionStart", "session_id": "x; rm -rf /", "cwd": d})
            self.assertNotIn("session", bad[1][2])
            feed = Feed(os.path.join(d, "f.jsonl"))
            hooks.handle(json.dumps({"hook_event_name": "SessionStart", "session_id": SID, "cwd": str(wt)}), feed)
            self.assertEqual([e.type for e in feed.read_since(0)], ["agent_started", "agent_place"])


def folder():
    cfg = load_all()
    return Folder(cfg["strings"]["eli5"], cfg["metaphors"], 50, 40)


class Ev:
    n = 0

    def __init__(self, type, agent=None, data=None, ts=None, task=None):
        import time
        Ev.n += 1
        self.id, self.type, self.agent, self.data, self.task = Ev.n, type, agent, data or {}, task
        self.ts = ts if ts is not None else time.time()


class Clashes(unittest.TestCase):
    def place(self, f, agent, root, wt="", branch="main"):
        f.apply(Ev("agent_place", agent, {"session": SID, "cwd": root, "root": root, **({"worktree": wt} if wt else {}), "branch": branch}))

    def edit(self, f, agent, path):
        f.apply(Ev("tool_use", agent, {"tool": "Edit", "file": path}))

    def test_same_checkout_warns_other_worktree_does_not(self):
        f = folder()
        self.place(f, "a", "/w/repo")
        self.place(f, "b", "/w/repo")
        self.place(f, "c", "/w/wt-a", "wt-a", "feat-a")
        self.edit(f, "a", "/w/repo/src/x.py")
        self.edit(f, "c", "/w/wt-a/src/x.py")          # same relative path, other worktree: fine
        self.assertEqual(f.snapshot()["clashes"], [])
        self.edit(f, "b", "src/x.py")                    # same checkout (relative path): warn
        self.assertEqual(f.snapshot()["clashes"], [{"file": "src/x.py", "agents": ["a", "b"], "root": "/w/repo"}])

    def test_relative_paths_in_two_worktrees_are_two_files(self):
        f = folder()
        self.place(f, "a", "/w/repo")
        self.place(f, "c", "/w/wt-a", "wt-a")
        self.edit(f, "a", "./src/y.py")
        self.edit(f, "c", "src/y.py")
        self.assertEqual(f.snapshot()["clashes"], [])

    def test_helper_without_place_matches_a_known_checkout(self):
        f = folder()
        self.place(f, "a", "C:/Users/[user]/repo")
        self.edit(f, "a", "C:\\Users\\[user]\\Repo\\src\\z.py")      # Windows: case and slashes do not matter
        self.edit(f, "old", "c:/users/[user]/repo/src/z.py")          # a transcript helper with no place yet
        self.assertEqual(f.snapshot()["clashes"][0]["agents"], ["a", "old"])

    def test_without_any_place_old_behaviour(self):
        f = folder()
        self.edit(f, "a", "/p/q.py")
        self.edit(f, "b", "/p/q.py")
        self.assertEqual(len(f.snapshot()["clashes"]), 1)


class Departed(unittest.TestCase):
    def test_left_helper_keeps_its_place_and_comes_back(self):
        f = folder()
        f.apply(Ev("agent_started", "claude-0b5f0c3e", {}))
        f.apply(Ev("agent_place", "claude-0b5f0c3e", {"session": SID, "cwd": "/w/repo", "root": "/w/repo", "branch": "main"}))
        f.apply(Ev("agent_left", "claude-0b5f0c3e", {"reason": "logout"}))
        s = f.snapshot()
        self.assertEqual(s["agents"], [])
        self.assertEqual([(d["id"], d["session"], d["cwd"], d["branch"]) for d in s["departed"]], [("claude-0b5f0c3e", SID, "/w/repo", "main")])
        f.apply(Ev("agent_working", "claude-0b5f0c3e", {"activity": "reading your message"}))
        s = f.snapshot()
        self.assertEqual(s["departed"], [])
        self.assertEqual((s["agents"][0]["session"], s["agents"][0]["branch"]), (SID, "main"))       # back with where it ran

    def test_at_most_eight_remembered(self):
        f = folder()
        for i in range(12):
            f.apply(Ev("agent_started", "h%d" % i, {}))
            f.apply(Ev("agent_left", "h%d" % i, {}))
        self.assertEqual([d["id"] for d in f.snapshot()["departed"]], ["h%d" % i for i in range(4, 12)])


class Plans(unittest.TestCase):
    def which(self, *have):
        return lambda name: "/usr/bin/" + name if name in have else None

    def test_session_ids(self):
        self.assertTrue(resurrect.valid_session(SID))
        self.assertTrue(resurrect.valid_session("abcdef12"))
        for bad in ("", "abc", "x; rm -rf /", "--help", "0b5f0c3e 1b7a", "0b5f0c3e&calc", "../../etc", "g" * 8, "a" * 65, None, 5):
            self.assertFalse(resurrect.valid_session(bad), bad)

    def test_claude_inside_the_helpers_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            planted = os.path.join(d, "claude.bat")
            for plat in ("win32", "linux"):
                with self.assertRaises(resurrect.ResurrectError) as e:
                    resurrect.plan(SID, d, plat, which=lambda n: planted if n == "claude" else "/usr/bin/" + n)
                self.assertIn("claude --resume", e.exception.command)

    def test_windows_mac_linux(self):
        win = resurrect.plan(SID, "C:\\p", "win32", self.which("claude"))
        self.assertEqual(win, [["cmd", "/c", "start", "", "cmd", "/k", os.path.abspath("/usr/bin/claude"), "--resume", SID]])      # V-049: the full path, never a claude.bat in the folder
        mac = resurrect.plan(SID, '/Users/me/my "odd" dir', "darwin", self.which("claude"))[0]
        self.assertEqual(mac[0], "osascript")
        self.assertIn("claude --resume " + SID, mac[2])
        self.assertIn("'/Users/me/my \\\"odd\\\" dir'", mac[2])       # shell-quoted, then AppleScript-escaped
        lin = resurrect.plan(SID, "/p", "linux", self.which("claude", "gnome-terminal", "xterm"))
        self.assertEqual(lin, [["/usr/bin/gnome-terminal", "--", "claude", "--resume", SID], ["/usr/bin/xterm", "-e", "claude", "--resume", SID]])

    def test_no_claude_or_no_terminal_gives_the_command(self):
        with self.assertRaises(resurrect.ResurrectError) as e:
            resurrect.plan(SID, "/p q", "linux", self.which("xterm"))
        self.assertEqual(e.exception.command, "cd '/p q' && claude --resume " + SID)
        with self.assertRaises(resurrect.ResurrectError) as e:
            resurrect.plan(SID, "/p", "linux", self.which("claude"))
        self.assertIn("No terminal", str(e.exception))
        self.assertEqual(resurrect.copy_command(SID, 'C:\\a "b"', "win32"), 'cd /d "C:\\a b" && claude --resume ' + SID)

    def test_runner_gets_argument_lists_and_the_folder(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            def runner(argv, cwd):
                calls.append((argv, cwd))
                if "gnome-terminal" in argv[0]:
                    raise OSError("no display")
            r = resurrect.resurrect(SID, d, "linux", self.which("claude", "gnome-terminal", "xterm"), runner)
            self.assertTrue(r["ok"])
            self.assertEqual([c[0][0] for c in calls], ["/usr/bin/gnome-terminal", "/usr/bin/xterm"])      # first failed: next terminal
            self.assertTrue(all(isinstance(a, list) and c == d for a, c in calls))
            with self.assertRaises(resurrect.ResurrectError):
                resurrect.resurrect("bad id!", d, "linux", self.which("claude", "xterm"), runner)
            with self.assertRaises(resurrect.ResurrectError) as e:
                resurrect.resurrect(SID, os.path.join(d, "gone"), "linux", self.which("claude", "xterm"), runner)
            self.assertIn("gone", str(e.exception))

    def test_user_name_put_back(self):
        self.assertEqual(resurrect.real_folder("/home/[user]/p", home="/home/alice"), "/home/alice/p")
        self.assertEqual(resurrect.real_folder("C:\\Users\\[user]\\p", home="C:\\Users\\bob"), "C:\\Users\\bob\\p")

    def test_owner_only_never_an_agent_tool(self):
        names = {t["name"] for t in mcp.tool_list()}
        self.assertFalse({n for n in names if any(w in n for w in ("resurrect", "phone", "notify", "approve", "verify", "pause"))})


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = load_all()
        self.feed = os.path.join(self.tmp.name, "feed.jsonl")
        self.ctx = server.Ctx(cfg, self.feed, None, assets_dir=self.tmp.name, settings_file=os.path.join(self.tmp.name, "s.json"), watch=False)
        self.calls = []
        self.ctx.resurrect_opts = {"platform": "linux", "which": lambda n: "/bin/" + n if n in ("claude", "xterm") else None,
                                   "runner": lambda argv, cwd: self.calls.append((argv, cwd))}
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, body, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json", **({"X-Vanilla-Key": self.ctx.key} if key else {})}
        c.request("POST", "/api/resurrect", json.dumps(body), h)
        r = c.getresponse()
        out = r.status, json.loads(r.read())
        c.close()
        return out

    def test_needs_the_key_and_opens_the_session_folder(self):
        f = Feed(self.feed)
        f.append("agent_started", agent="claude-0b5f0c3e", data={})
        f.append("agent_place", agent="claude-0b5f0c3e", data={"session": SID, "cwd": self.tmp.name})
        f.append("agent_left", agent="claude-0b5f0c3e", data={})
        self.assertEqual(self.post({"agent": "claude-0b5f0c3e"}, key=False)[0], 403)
        self.assertEqual(self.calls, [])
        st, r = self.post({"agent": "claude-0b5f0c3e"})
        self.assertEqual((st, r.get("ok")), (200, True), r)
        self.assertEqual(self.calls, [(["/bin/xterm", "-e", "claude", "--resume", SID], self.tmp.name)])
        self.assertEqual(self.post({"agent": "nobody"})[0], 400)
        f.append("agent_started", agent="transcript-only", data={})
        st, r = self.post({"agent": "transcript-only"})
        self.assertEqual((st, r["ok"]), (200, False))            # no session id: a plain error, nothing started
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
