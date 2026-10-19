"""Recorded 'plan approved' step (D-310..D-319): durable file, feed event, counting gate, buff icon, HTTP call."""
import http.client
import json
import os
import tempfile
import threading
import unittest

from vanilla_agents.app import approvals, auras, server
from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import load_all
from vanilla_agents.app.state import AppState
from vanilla_agents.events.feed import Feed


class Store(unittest.TestCase):
    def test_add_load_and_duplicate(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "a.json")
            self.assertEqual(approvals.load(p, "proj"), {})
            self.assertTrue(approvals.add(p, "proj", "t1", "Fix it", 5.0, 10, 5))
            self.assertFalse(approvals.add(p, "proj", "t1", "Fix it", 6.0, 10, 5))
            self.assertEqual(approvals.load(p, "proj"), {"t1": {"ts": 5.0, "title": "Fix it", "who": "you", "why": "approved by you"}})
            self.assertEqual(approvals.load(p, "other"), {})

    def test_bounded_and_damaged_file_is_empty(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "a.json")
            for i in range(5):
                approvals.add(p, "proj", "t%d" % i, "x", float(i), 3, 2)
            self.assertEqual(sorted(approvals.load(p, "proj")), ["t2", "t3", "t4"])
            approvals.add(p, "b", "t", "x", 1.0, 3, 2)
            approvals.add(p, "c", "t", "x", 1.0, 3, 2)
            self.assertEqual(approvals.load(p, "proj"), {})        # oldest project dropped
            open(p, "w").write("{not json")
            self.assertEqual(approvals.load(p, "c"), {})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_all()
        self.cfg["app"]["approvals_path"] = os.path.join(self.tmp.name, "approvals.json")
        self.feed = os.path.join(self.tmp.name, "feed.jsonl")
        f = Feed(self.feed)
        f.append("task_created", task="t1", data={"title": "Fix movement"})
        f.append("agent_started", task=None, agent="builder", data={"role": "builder"})
        f.append("task_ready", task="t1", agent="builder", data={"title": "Fix movement"})
        f.append("task_verified", task="t1", agent="builder", data={"title": "Fix movement"})

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, st):
        return next(t for t in st.snapshot()["tasks"] if t["id"] == "t1")


class State(Base):
    def test_approve_marks_logs_and_survives_restart(self):
        st = AppState(self.cfg, self.feed)
        self.assertFalse(self.task(st)["approved"])
        st.approve("t1")
        self.assertTrue(self.task(st)["approved"])
        self.assertEqual(st.snapshot()["chat"][-1]["text"], "You approved the plan for Fix movement.")
        os.remove(self.feed)                                      # even with the feed gone the file remembers
        Feed(self.feed).append("task_created", task="t1", data={"title": "Fix movement"})
        self.assertTrue(self.task(AppState(self.cfg, self.feed))["approved"])

    def test_approve_twice_adds_one_event_and_unknown_quest_is_refused(self):
        st = AppState(self.cfg, self.feed)
        st.approve("t1")
        st.approve("t1")
        self.assertEqual(sum(1 for e in Feed(self.feed).read_since(0, 100) if e.type == "plan_approved"), 1)
        with self.assertRaises(ValueError):
            st.approve("nope")

    def test_done_counts_without_approval_by_default(self):
        self.assertEqual(AppState(self.cfg, self.feed).snapshot()["counts"], {"done": 1, "total": 1})

    def test_required_approval_gates_the_count_until_approved(self):
        self.cfg["app"]["plan_approval_required"] = True
        st = AppState(self.cfg, self.feed)
        self.assertEqual((st.snapshot()["counts"], self.task(st)["counts"]), ({"done": 0, "total": 1}, False))
        a = auras.compute({**st.snapshot(), "verify": {}})
        self.assertEqual((a["raid"][0]["passed"], [x["id"] for x in a["player"]]), (0, []))
        st.approve("t1")
        self.assertEqual(st.snapshot()["counts"], {"done": 1, "total": 1})
        a = auras.compute({**st.snapshot(), "verify": {}})
        self.assertEqual(([x["id"] for x in a["raid"][0]["auras"]], [x["id"] for x in a["player"]]), (["approved", "verified"], ["approved", "verified"]))


class Aura(unittest.TestCase):
    def test_approved_icon_only_when_true_and_counts_per_agent(self):
        snap = {"agents": [{"id": "a", "role": "", "state": "idle"}], "leases": [], "verify": {},
                "tasks": [{"id": "1", "status": "progress", "agent": "a", "approved": True}, {"id": "2", "status": "new", "agent": "a", "approved": True},
                          {"id": "3", "status": "new", "agent": "a", "approved": False}]}
        r = auras.compute(snap)
        self.assertEqual([(x["id"], x["count"]) for x in r["raid"][0]["auras"]], [("approved", 2)])
        self.assertEqual([(x["id"], x["count"]) for x in r["player"]], [("approved", 2)])
        snap["tasks"] = []
        self.assertEqual(auras.compute(snap)["player"], [])


class Http(Base):
    def setUp(self):
        super().setUp()
        self.ctx = server.Ctx(self.cfg, self.feed, None)
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def post(self, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/approve", json.dumps(body), {"Content-Type": "application/json", "X-Vanilla-Key": self.ctx.key})
        r = c.getresponse()
        d = json.loads(r.read())
        c.close()
        return r.status, d

    def test_approve_call(self):
        st, d = self.post({"task": "t1"})
        self.assertEqual(st, 200)
        self.assertTrue(next(t for t in d["tasks"] if t["id"] == "t1")["approved"])
        self.assertEqual(self.post({"task": "nope"})[0], 400)
        self.assertEqual(self.post({"task": 5})[0], 400)


class Settings(unittest.TestCase):
    def test_limits_must_be_positive(self):
        cfg = load_all()
        self.assertEqual(check_app(cfg), [])
        cfg["app"]["approvals_max_tasks"] = 0
        self.assertTrue(any("approvals_max_tasks" in p for p in check_app(cfg)))


class SentBackState(Base):
    def test_quest_keeps_evidence_until_the_next_hand_in(self):
        f = Feed(self.feed)
        f.append("sent_back", task="t1", data={"check": "Login page opens", "evidence": "exit code 101"})
        st = AppState(self.cfg, self.feed)
        self.assertEqual(self.task(st)["sent_back"], [{"check": "Login page opens", "evidence": "exit code 101"}])
        self.assertEqual(st.snapshot()["chat"][-1]["text"], "Sent back with evidence: Login page opens did not pass. exit code 101")
        self.assertEqual(st.snapshot()["chat"][-1]["channel"], "verifier")
        f.append("task_ready", task="t1", data={"title": "Fix movement"})
        st.refresh()
        self.assertEqual(self.task(st)["sent_back"], [])
