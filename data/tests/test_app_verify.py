import http.client
import json
import os
import tempfile
import threading
import unittest
from collections import namedtuple

from vanilla_agents.app import server
from vanilla_agents.app.config import load_all
from vanilla_agents.app.verify_job import Refused, VerifyJob
from vanilla_agents.events.feed import Feed
from vanilla_agents.verify.manifest import ManifestMismatch

Usage = namedtuple("Usage", "total used free")
GB = 2 ** 30


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.cfg = load_all()
        a = self.cfg["app"]
        a.update(projects_path=os.path.join(t, "projects.json"), verify_work_root=os.path.join(t, "verify"), verify_min_free_bytes=5 * GB, runs_path=os.path.join(t, "runs.jsonl"))
        with open(a["projects_path"], "w") as f:
            json.dump({t: {"repo": t, "trusted_dir": t, "expected_manifest_hash": "h", "criteria": ["cargo_test"], "default_base_ref": "HEAD"}}, f)
        self.feed_path, self.sf = os.path.join(t, "feed.jsonl"), os.path.join(t, "settings.json")
        self.calls, self.gate, self.work = [], threading.Event(), []

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, verifier, free=50 * GB, wait=False):
        def v(req):
            d = os.path.join(req["work_root"], req["run_id"], "tmp")
            os.makedirs(d)                                  # the real verifier leaves a checkout here
            open(os.path.join(d, "ro"), "w").close()
            os.chmod(os.path.join(d, "ro"), 0o444)
            self.work.append(os.path.join(req["work_root"], req["run_id"]))
            self.calls.append(req)
            if wait:
                self.gate.wait(5)
            return verifier(req)
        return VerifyJob(self.cfg, self.feed_path, self.sf, verifier=v, usage=lambda p: Usage(100 * GB, 0, free), git_head=lambda r, ref="HEAD": "a" * 40)

    def types(self):
        return [e.type for e in Feed(self.feed_path).read_since(0, 100)]

    def run_job(self, job):
        job.start(self.tmp.name)
        job.thread.join(10)
        return self.types()


def bundle(outcome, status=None):
    return lambda req: ({"outcome": outcome, "checks": [{"check_id": "cargo_test", "status": status or outcome}],
                         "not_verified": [] if outcome == "VERIFIED" else [{"reason": "tests broke"}]}, {})


class Verify(Base):
    def test_success_events_and_cleanup(self):
        ev = self.run_job(self.job(bundle("VERIFIED")))
        self.assertEqual(ev, ["task_ready", "route_chosen", "check_started", "check_finished", "task_verified"])
        self.assertFalse(os.path.exists(self.work[0]))
        self.assertEqual(self.calls[0]["base_commit"], "a" * 40)

    def test_failure(self):
        self.assertEqual(self.run_job(self.job(bundle("FAILED")))[-1], "task_failed")

    def test_blocked_by_manifest_mismatch_says_what_to_do(self):
        def boom(req):
            raise ManifestMismatch("bad")
        j = self.job(boom)
        self.assertEqual(self.run_job(j)[-1], "task_blocked")
        last = list(Feed(self.feed_path).read_since(0, 100))[-1]
        self.assertIn("onboard.py --regen", last.data["reason"])
        self.assertFalse(os.path.exists(self.work[0]))
        self.assertFalse(j.status()["running"])

    def test_crash_never_leaves_it_running(self):
        j = self.job(lambda req: 1 / 0)
        self.assertEqual(self.run_job(j)[-1], "task_blocked")
        self.assertFalse(j.status()["running"])

    def test_busy(self):
        j = self.job(bundle("VERIFIED"), wait=True)
        j.start(self.tmp.name)
        with self.assertRaisesRegex(Refused, "already running"):
            j.start(self.tmp.name)
        self.assertTrue(j.status()["running"])
        self.gate.set()
        j.thread.join(10)
        j.start(self.tmp.name)      # free again
        j.thread.join(10)

    def test_paused_persists_and_refuses(self):
        j = self.job(bundle("VERIFIED"))
        j.set_paused(True)
        self.assertTrue(self.job(bundle("VERIFIED")).status()["paused"])   # a new object reads the settings file
        with self.assertRaisesRegex(Refused, "Paused"):
            j.start(self.tmp.name)
        j.set_paused(False)
        self.assertEqual(self.run_job(j)[-1], "task_verified")

    def test_single_check_run_and_unknown_check(self):
        j = self.job(bundle("VERIFIED"))
        with self.assertRaisesRegex(Refused, "not one of"):
            j.start(self.tmp.name, "nope")
        j.start(self.tmp.name, "cargo_test")
        j.thread.join(10)
        self.assertEqual([c["check_id"] for c in self.calls[-1]["criteria"]], ["cargo_test"])
        self.assertFalse(self.calls[-1]["holdouts"])

    def test_disk_low(self):
        with self.assertRaisesRegex(Refused, "free space"):
            self.job(bundle("VERIFIED"), free=GB).start(self.tmp.name)
        self.assertEqual(self.calls, [])

    def test_unknown_project(self):
        json.dump({"a": {}, "b": {}}, open(self.cfg["app"]["projects_path"], "w"))
        with self.assertRaisesRegex(Refused, "not set up"):
            self.job(bundle("VERIFIED")).start("zzz")


class Http(Base):
    def test_endpoints(self):
        self.cfg["app"]["sse_check_seconds"] = 0.05
        ctx = server.Ctx(self.cfg, self.feed_path, self.tmp.name, settings_file=self.sf, verifier=bundle("VERIFIED"))
        ctx.verify.git_head = lambda r, ref="HEAD": "a" * 40
        ctx.verify.usage = lambda p: Usage(100 * GB, 0, 50 * GB)
        srv = server.make_server(ctx, "127.0.0.1", 0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def post(path, body, origin=None):
            c = http.client.HTTPConnection("127.0.0.1", port)
            h = {"Content-Type": "application/json", "X-Vanilla-Key": ctx.key, **({"Origin": origin} if origin else {})}
            c.request("POST", path, json.dumps(body), h)
            r = c.getresponse()
            return r.status, json.loads(r.read())
        try:
            self.assertEqual(post("/api/verify", {}, "http://evil.example")[0], 403)
            self.assertEqual(post("/api/pause", {"paused": 1})[0], 400)
            st, snap = post("/api/pause", {"paused": True})
            self.assertTrue(st == 200 and snap["verify"]["paused"])
            st, body = post("/api/verify", {})
            self.assertEqual(st, 409)
            self.assertIn("Paused", body["error"])
            post("/api/pause", {"paused": False})
            st, snap = post("/api/verify", {})
            self.assertEqual(st, 200)
            ctx.verify.thread.join(10)
            self.assertEqual(self.types()[-1], "task_verified")
        finally:
            srv.shutdown()
            srv.server_close()
            ctx.stop.set()


if __name__ == "__main__":
    unittest.main()


class SentBack(Base):
    """D-320..D-329: a failed check is sent back with its evidence; a hidden check never shows detail."""
    SECRET = "SECRET_HOLDOUT_TOKEN_xyz"

    def events(self, job):
        self.run_job(job)
        return Feed(self.feed_path).read_since(0, 100)

    def failing(self, checks, **extra):
        return lambda req: ({"outcome": "FAILED", "checks": checks, "not_verified": [{"reason": "x"}], **extra}, {})

    def test_failed_check_is_sent_back_with_redacted_evidence_before_task_failed(self):
        chk = [{"check_id": "cargo_test", "status": "FAILED", "observed": "exit code 101 password=hunter2 in " + "C:" + chr(92) + "Users" + chr(92) + "someuser"}]
        ev = self.events(self.job(self.failing(chk)))
        types = [e.type for e in ev]
        self.assertEqual(types[-2:], ["sent_back", "task_failed"])
        sb = ev[-2].data
        self.assertEqual(sb["check"], "Run the game's tests")
        self.assertIn("exit code 101", sb["evidence"])
        for needle in ("hunter2", "someuser"):
            self.assertNotIn(needle, sb["evidence"])

    def test_hidden_check_gets_a_fixed_sentence_and_nothing_else(self):
        chk = [{"check_id": "hidden-1", "status": "FAILED", "observed": self.SECRET}, {"check_id": "cargo_test", "status": "VERIFIED", "observed": "ok"}]
        ev = self.events(self.job(self.failing(chk)))
        sent = [e for e in ev if e.type == "sent_back"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].data, {"check": "A hidden check", "evidence": self.cfg["strings"]["ui"]["sentback.hidden"]})
        self.assertNotIn(self.SECRET, json.dumps([e.to_dict() for e in ev]))

    def test_passing_and_blocked_checks_are_not_sent_back(self):
        self.assertNotIn("sent_back", self.run_job(self.job(bundle("VERIFIED"))))
        self.assertNotIn("sent_back", self.run_job(self.job(bundle("BLOCKED"))))

    def test_refused_change_uses_the_finding_as_evidence(self):
        ev = self.events(self.job(self.failing([], diff_findings=[{"blocking": True, "detail": "the patch edits a protected file"}])))
        sb = [e for e in ev if e.type == "sent_back"][0].data
        self.assertEqual((sb["check"], sb["evidence"]), ("The change itself", "the patch edits a protected file"))
