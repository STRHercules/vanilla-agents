"""Stage 3 review fixes (D-100..D-105): lease renewal, partial-orphan repair, fence validation, worker match, audit, any-state orphan."""
import json

from vanilla_agents.contracts import StaleFence
from vanilla_agents.durable import recovery, scheduler
from vanilla_agents.durable.watchdog import Watchdog
from tests.helpers import CTL, Base, worker


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Fixes(Base):
    def setUp(self):
        super().setUp()
        self.fc = FakeClock()
        self.svc.clock = self.fc
        _, self.tid = self.project_task()
        self.w = self.svc.register_worker(CTL)

    def run_task(self, ttl=60):
        claim = scheduler.claim_next(self.svc, self.w, "slot", "s", ttl=ttl)
        scheduler.start_run(self.svc, claim, self.w)
        return claim

    def test_heartbeating_worker_keeps_lease_for_70s(self):
        self.run_task(60)
        wd = Watchdog(self.svc, stuck_after=30, clock=self.fc)
        for _ in range(7):
            self.fc.t += 10
            self.svc.worker_heartbeat(worker(self.w), self.w, lease_ttl=60)
            self.assertEqual(wd.findings(), [])
        self.assertEqual(self.task(self.tid)["execution_state"], "RUNNING")
        self.assertEqual(self.events("lease_expired"), [])
        self.assertEqual(len(self.events("lease_renewed")), 0)   # heartbeat path renews inside its own event
        self.assertIn("renewed", json.loads(self.events("worker_heartbeat")[-1]["payload_json"]))
        # without renewal the same lease does expire
        self.fc.t += 61
        self.assertEqual(wd.findings()[0][1], "lease_expired")

    def test_renew_lease_is_fenced_and_audited(self):
        claim = self.run_task(60)
        self.fc.t += 30
        self.svc.renew_lease(CTL, claim["fence"], 60)
        ev = self.events("lease_renewed")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["task_id"], self.tid)
        self.fc.t += 50   # 80s after creation: alive only because of the renewal
        self.svc.transition_execution(CTL, self.tid, "FINISHED", fence=claim["fence"])
        bad = ("slot", "s", claim["fence"][2] + 1)
        with self.assertRaises(StaleFence):
            self.svc.renew_lease(CTL, bad, 60)
        # released lease cannot be renewed
        self.svc.set_worker_state(CTL, self.w, "termination_requested")
        self.svc.set_worker_state(CTL, self.w, "termination_confirmed")
        lid = self.svc.conn.execute("SELECT lease_id FROM leases").fetchone()[0]
        self.svc.request_lease_release(CTL, lid)
        self.svc.confirm_lease_release(CTL, lid)
        with self.assertRaises(StaleFence):
            self.svc.renew_lease(CTL, claim["fence"], 60)
        self.assertGreaterEqual(len(self.events("rejected")), 2)

    def test_watchdog_renews_fresh_workers(self):
        self.run_task(60)
        wd = Watchdog(self.svc, stuck_after=30, clock=self.fc, lease_ttl=60)
        for _ in range(7):
            self.fc.t += 10
            self.svc.worker_heartbeat(worker(self.w), self.w)
            self.assertEqual(wd.scan()["findings"], [])
        self.assertEqual(self.task(self.tid)["execution_state"], "RUNNING")

    def test_partial_orphan_is_repaired_by_later_scan(self):
        self.run_task()
        self.fc.t += 100
        wd = Watchdog(self.svc, stuck_after=30, clock=self.fc)
        real, calls = self.svc.block_task, []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return real(*a, **k)
        self.svc.block_task = flaky
        with self.assertRaises(RuntimeError):
            wd.scan()
        self.assertEqual(self.task(self.tid)["execution_state"], "RUNNING")
        self.assertTrue(wd.findings())   # the stranded task is still reported
        wd.scan()
        self.assertEqual(self.task(self.tid)["execution_state"], "BLOCKED")

    def test_fence_rejects_non_int_tokens_with_audit(self):
        claim = self.run_task()
        tok = claim["fence"][2]
        n = 0
        for bad in (True, None, str(tok), float(tok), 1.0):
            with self.assertRaises(StaleFence):
                self.svc.block_task(CTL, self.tid, "disk", fence=("slot", "s", bad))
            n += 1
            self.assertEqual(len(self.events("rejected")), n)
        with self.assertRaises(StaleFence):
            self.svc.block_task(CTL, self.tid, "disk", fence="nonsense")
        self.assertEqual(self.task(self.tid)["execution_state"], "RUNNING")

    def test_fence_checks_worker_is_lease_holder(self):
        other = self.svc.register_worker(CTL)
        claim = scheduler.claim_next(self.svc, self.w, "slot", "s")
        with self.assertRaises(StaleFence):
            self.svc.create_attempt(CTL, self.tid, other, fence=claim["fence"])
        att = self.svc.create_attempt(CTL, self.tid, self.w, fence=claim["fence"])
        self.svc.conn.execute("UPDATE attempts SET worker_id=? WHERE attempt_id=?", (other, att))
        with self.assertRaises(StaleFence):
            self.svc.transition_attempt(CTL, att, "ACTIVE", fence=claim["fence"])

    def test_audit_carries_task_id_and_termination_proof(self):
        self.run_task()
        recovery.orphan_task(self.svc, self.tid)
        for kind in ("worker_state_changed", "lease_release_requested"):
            self.assertTrue(all(e["task_id"] == self.tid for e in self.events(kind) if kind != "worker_state_changed" or json.loads(e["payload_json"])["to"] != "alive"), kind)
        recovery.settle(self.svc, lambda wid: True)
        confirmed = [e for e in self.events("worker_state_changed") if json.loads(e["payload_json"])["to"] == "termination_confirmed"]
        self.assertEqual(confirmed[0]["task_id"], self.tid)
        self.assertTrue(json.loads(confirmed[0]["payload_json"])["proof"]["is_terminated"])
        self.assertEqual(self.events("lease_released")[0]["task_id"], self.tid)

    def test_orphan_handles_queued_task_with_active_attempt(self):
        att = self.svc.create_attempt(CTL, self.tid, self.w)
        self.svc.transition_attempt(CTL, att, "ACTIVE")
        self.assertEqual(self.task(self.tid)["execution_state"], "QUEUED")
        self.assertEqual(Watchdog(self.svc, 30, clock=self.fc).findings(), [(self.tid, "stray_attempt")])
        recovery.orphan_task(self.svc, self.tid)
        self.assertEqual(self.task(self.tid)["execution_state"], "BLOCKED")
        self.assertEqual(self.svc.conn.execute("SELECT state FROM attempts").fetchone()[0], "LOST")
        self.assertEqual(recovery.orphan_task(self.svc, self.tid), 0)
