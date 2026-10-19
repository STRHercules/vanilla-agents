"""GET /api/diff (Inspect window) and GET /api/history (Replay bar) against a real temp git repo and a temp feed."""
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

from vanilla_agents.app import server
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class InspectAndReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "a.py").write_text("".join("line %d\n" % i for i in range(1, 11)))
        (self.repo / "old.txt").write_text("x\ny\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        (self.repo / "src" / "a.py").write_text("".join(("LINE %d\n" if i == 5 else "line %d\n") % i for i in range(1, 11)) + "line 11\n")
        (self.repo / "old.txt").unlink()
        (self.repo / "new.md").write_text("hello\nworld\n")
        self.feed = str(root / "feed.jsonl")
        f = Feed(self.feed)
        f.append("task_created", "t1", None, {"title": "First quest"})
        f.append("task_created", "t2", None, {"title": "Second quest"})
        f.append("task_created", "t3", None, {"title": "Third quest"})
        self.srv = None

    def start(self, project):
        self.ctx = server.Ctx(load_all(), self.feed, project, assets_dir=Path(self.tmp.name), watch=False)
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        if self.srv:
            self.ctx.stop.set()
            self.srv.shutdown()
            self.srv.server_close()
        self.tmp.cleanup()

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data)

    def test_diff_lists_changed_files_with_status_and_counts(self):
        self.start(str(self.repo))
        st, d = self.get("/api/diff")
        self.assertEqual(st, 200)
        by = {f["file"]: f for f in d["files"]}
        self.assertEqual(set(by), {"src/a.py", "old.txt", "new.md"})
        self.assertEqual((by["src/a.py"]["status"], by["src/a.py"]["added"], by["src/a.py"]["removed"]), ("M", "2", "1"))
        self.assertEqual((by["old.txt"]["status"], by["old.txt"]["removed"]), ("D", "2"))
        self.assertEqual(by["new.md"]["status"], "??")
        self.assertNotIn("added", by["new.md"])        # untracked: git has no line counts

    def test_diff_of_one_file_has_hunks_and_untracked_file_is_shown_whole(self):
        self.start(str(self.repo))
        st, d = self.get("/api/diff?file=" + quote("src/a.py"))
        self.assertEqual((st, d["file"], d["truncated"]), (200, "src/a.py", False))
        self.assertIn("@@ -", d["diff"])
        self.assertIn("\n-line 5\n+LINE 5\n", d["diff"])
        self.assertIn("\n+line 11\n", d["diff"])
        d = self.get("/api/diff?file=new.md")[1]
        self.assertEqual(d["diff"], "+hello\n+world\n")

    def test_diff_refuses_paths_outside_the_repo_and_non_git_projects(self):
        self.start(str(self.repo))
        self.assertIn("error", self.get("/api/diff?file=" + quote("../feed.jsonl"))[1])
        self.srv.shutdown()
        self.srv.server_close()
        self.start(None)
        st, d = self.get("/api/diff")
        self.assertEqual(st, 200)
        self.assertIn("git", d["error"])

    def test_history_folds_up_to_the_event_and_reports_the_range(self):
        self.start(str(self.repo))
        st, d = self.get("/api/history?until=2")
        self.assertEqual(st, 200)
        self.assertEqual(d["range"], {"first": 1, "last": 3, "at": 2})
        self.assertEqual([t["title"] for t in d["tasks"]], ["First quest", "Second quest"])
        self.assertEqual(len(d["chat"]), 2)
        self.assertTrue(d["chat"][-1]["ts"] > 0)
        d = self.get("/api/history")[1]          # no until: now
        self.assertEqual((d["range"]["at"], len(d["tasks"])), (3, 3))
        d = self.get("/api/history?until=99")[1]
        self.assertEqual(d["range"]["at"], 3)
        self.assertEqual(self.get("/api/history?until=abc")[0], 400)


if __name__ == "__main__":
    unittest.main()
