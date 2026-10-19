"""Fixes from the independent review of slices A and B (D-380..D-399): approvals keyed by real path, Idea approvals survive the caps, links and junctions,
case-insensitive names, the hook that tells a session about open requests. Temp dirs only."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from vanilla_agents.app import approvals
from vanilla_agents.app.config import load_all
from vanilla_agents.app.verify_job import VerifyJob
from vanilla_agents.events import hooks
from vanilla_agents.events.feed import Feed
from vanilla_agents.onboard import setup
from tests.test_onboard import git_repo


def link(src, dst):
    """A directory link (a junction on Windows, a symlink elsewhere), or None when the machine will not make one."""
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True)
        return str(dst) if r.returncode == 0 else None
    try:
        os.symlink(str(src), str(dst))
        return str(dst)
    except OSError:
        return None


class Caps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmp.name, "approvals.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_caps_never_drop_an_idea_approval(self):
        approvals.add(self.p, "projA", "idea:abc", "Idea", 1, 2, 2)
        for i in range(6):
            approvals.add(self.p, "projA", "q%d" % i, "quest", 10 + i, 2, 2)
        got = approvals.load(self.p, "projA")
        self.assertEqual(sorted(got), ["idea:abc", "q4", "q5"])                       # the oldest quests went, the oldest record of all (the Idea) stayed
        approvals.add(self.p, "projB", "q", "quest", 100, 2, 1)
        approvals.add(self.p, "projC", "q", "quest", 101, 2, 1)                       # only one project may stay: A is the oldest ...
        self.assertEqual(sorted(approvals.load(self.p, "projA")), ["idea:abc"])       # ... but its Idea approval is kept, its quests are not
        self.assertEqual(approvals.load(self.p, "projB"), {})
        self.assertIn("q", approvals.load(self.p, "projC"))

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive; this case cannot happen elsewhere")
    def test_file_written_with_a_path_as_typed_still_loads(self):
        Path(self.p).write_text(json.dumps({"projects": {self.tmp.name.upper(): {"q1": {"ts": 1, "title": "t", "who": "you", "why": "x"}}}}), encoding="utf-8")
        self.assertIn("q1", approvals.load(self.p, self.tmp.name))
        self.assertEqual(approvals.add(self.p, self.tmp.name, "q2", "t", 2, 5, 5), True)
        self.assertEqual(sorted(approvals.load(self.p, self.tmp.name)), ["q1", "q2"])

    def test_junction_and_case_share_one_key(self):
        real = Path(self.tmp.name) / "Real"
        real.mkdir()
        lnk = link(real, Path(self.tmp.name) / "alias")
        if lnk is None:
            self.skipTest("this machine will not make a directory link")
        approvals.add(self.p, str(real), "q1", "t", 1, 5, 5)
        self.assertIn("q1", approvals.load(self.p, lnk))
        if os.name == "nt":
            self.assertIn("q1", approvals.load(self.p, str(real).upper()))
        self.assertEqual(approvals.add(self.p, lnk, "q1", "t", 2, 5, 5), False)       # already approved, whichever way the folder is spelled
        self.assertEqual(len(json.loads(Path(self.p).read_text(encoding="utf-8"))["projects"]), 1)


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.reg = os.path.join(t, "p.json")
        self.repo = git_repo(t, "Demo")
        with contextlib.redirect_stdout(io.StringIO()):
            self.summary = setup.onboard(self.repo, self.reg, os.path.join(t, "trusted"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_names_match_without_regard_to_case_on_windows(self):
        reg = setup.load_registry(self.reg)
        self.assertEqual(setup.find(reg, "demo")[0] is not None, True)
        if os.name == "nt":
            self.assertIsNotNone(setup.find(reg, "DEMO")[1])
            self.assertIsNotNone(setup.find(reg, self.repo.upper())[1])
        self.assertIsNone(setup.find(reg, "other")[1])

    def test_a_junction_to_the_project_is_the_same_project(self):
        lnk = link(self.repo, os.path.join(self.tmp.name, "alias"))
        if lnk is None:
            self.skipTest("this machine will not make a directory link")
        reg = setup.load_registry(self.reg)
        self.assertEqual(setup.find(reg, lnk)[0], setup.find(reg, self.repo)[0])
        self.assertTrue(setup.onboard(lnk, self.reg, os.path.join(self.tmp.name, "trusted"))["already"])       # no second entry through the link
        self.assertEqual(len(setup.load_registry(self.reg)), 1)
        cfg = load_all()
        cfg["app"]["projects_path"] = self.reg
        job = VerifyJob(cfg, os.path.join(self.tmp.name, "feed.jsonl"), os.path.join(self.tmp.name, "s.json"))
        self.assertIsNotNone(job.entry(lnk))
        self.assertEqual(job.entry(lnk)["repo"], os.path.realpath(self.repo))


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "feed.jsonl")
        self.feed = Feed(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, name):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(hooks.main(io.BytesIO(json.dumps({"hook_event_name": name, "session_id": "s1"}).encode()), feed_path=self.path), 0)
        return out.getvalue()

    def test_open_requests_are_told_to_a_session_until_answered(self):
        self.assertEqual(self.run_hook("SessionStart"), "")
        self.feed.append("explain_requested", task="demo:q:1", data={"title": "Add a lobby"})
        self.feed.append("change_requested", task="demo:q:2", data={"title": "Add a shop"})
        for name in ("SessionStart", "UserPromptSubmit"):
            text = self.run_hook(name)
            self.assertIn("EXPLAIN quest demo:q:1 (Add a lobby)", text)
            self.assertIn("CHANGE quest demo:q:2 (Add a shop)", text)
            self.assertIn("answer_explain.py", text)
        self.assertEqual(self.run_hook("PreToolUse"), "")                       # other hooks stay silent
        self.feed.append("explain_answered", task="demo:q:1", data={"title": "Add a lobby", "text": "It is a waiting room."})
        text = self.run_hook("UserPromptSubmit")
        self.assertNotIn("EXPLAIN", text)
        self.assertIn("CHANGE quest demo:q:2", text)
        self.feed.append("change_answered", task="demo:q:2", data={"title": "Add a shop"})
        self.assertEqual(self.run_hook("UserPromptSubmit"), "")

    def test_a_later_request_after_an_answer_is_open_again(self):
        self.feed.append("explain_requested", task="q", data={"title": "T"})
        self.feed.append("explain_answered", task="q", data={"title": "T", "text": "a"})
        self.feed.append("explain_requested", task="q", data={"title": "T"})
        self.assertEqual(hooks.pending(self.feed), [("explain", "q", "T")])


if __name__ == "__main__":
    unittest.main()
