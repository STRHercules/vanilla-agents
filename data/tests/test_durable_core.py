"""Stage 3: scheduler, watchdog, disk preflight and restart recovery (no sleeping; injectable clocks)."""
import collections
import os

from vanilla_agents.contracts import LeaseBusy, StaleFence, ValidationError
from vanilla_agents.durable import preflight, recovery, scheduler
from vanilla_agents.durable.watchdog import Watchdog
from vanilla_agents.storage import StorageService
from tests.helpers import CTL, Base, worker

Usage = collections.namedtuple("Usage", "total used free")


class Durable(Base):
    def setUp(self):
        super().setUp()
        self.pid = self.svc.create_project(CTL, "p")
        self.w = self.svc.register_worker(CTL)
        self.svc.worker_heartbeat(worker(self.w), self.w)

    def mk(self, title="t"):
        return self.svc.create_task(CTL, {"project_id": self.pid, "title": title, "description": "d"})

    def running(self, ttl=3600):
        self.mk()
        c = scheduler.claim_next(self.svc, self.w, "slot", "k", ttl)
        return c, scheduler.start_run(self.svc, c, self.w)

    def state(self, tid):
        return self.task(tid)["execution_state"]

    def one(self, sql):
        return self.svc.conn.execute(sql).fetchone()[0]


class SchedulerTests(Durable):
    def test_picks_oldest_queued_deterministically_and_skips_others(self):
        self.assertIsNone(scheduler.next_ready(self.svc))
        a, b, c = self.mk("a"), self.mk("b"), self.mk("c")
        self.svc.request_cancel(CTL, a)
        self.assertEqual(scheduler.next_ready(self.svc), b)
        self.assertEqual(scheduler.next_ready(self.svc), b)       # pure read
        self.svc.transition_execution(CTL, b, "LEASING")
        self.assertEqual(scheduler.next_ready(self.svc), c)

    def test_claim_and_start_run_are_fenced_end_to_end(self):
        tid = self.mk()
        claim = scheduler.claim_next(self.svc, self.w, "slot", "k")
        self.assertEqual(claim["task_id"], tid)
        self.assertEqual(self.state(tid), "LEASING")
        scheduler.start_run(self.svc, claim, self.w)
        self.assertEqual(self.state(tid), "RUNNING")

    def test_busy_resource_changes_nothing(self):
        self.running()
        t2 = self.mk("t2")
        with self.assertRaises(LeaseBusy):
            scheduler.claim_next(self.svc, self.w, "slot", "k")
        self.assertEqual(self.state(t2), "QUEUED")

    def test_stale_claim_cannot_start_run(self):
        self.mk()
        claim = scheduler.claim_next(self.svc, self.w, "slot", "k")
        self.svc.request_lease_release(CTL, claim["lease_id"])
        with self.assertRaises(StaleFence):
            scheduler.start_run(self.svc, claim, self.w)


class PreflightTests(Base):
    def test_threshold_is_a_parameter(self):
        usage = lambda p: Usage(100, 90, 10)
        self.assertEqual(preflight.check_disk(self.tmp, 10, usage), 10)
        with self.assertRaises(preflight.DiskSpaceLow) as cm:
            preflight.check_disk(self.tmp, 11, usage)
        self.assertEqual(cm.exception.code, "disk_space_low")

    def test_missing_target_measured_on_existing_parent_and_real_call_works(self):
        seen = []
        preflight.check_disk(self.tmp + "/not/yet/there", 0, lambda p: seen.append(p) or Usage(1, 0, 1))
        self.assertEqual(seen[0], os.path.abspath(self.tmp))
        self.assertGreaterEqual(preflight.check_disk(self.tmp, 0), 0)

    def test_bad_threshold_rejected(self):
        for bad in (-1, 1.5, "10", True):
            with self.assertRaises(ValidationError):
                preflight.check_disk(self.tmp, bad)


class WatchdogTests(Durable):
    def test_nothing_found_while_healthy(self):
        self.running()
        self.assertEqual(Watchdog(self.svc, 60).scan(), {"findings": [], "changes": 0})

    def test_expired_lease_orphans_task_to_recoverable_state(self):
        c, att = self.running(ttl=10)
        self.clock.t += 11
        wd = Watchdog(self.svc, 10_000)
        r = wd.scan()
        self.assertEqual(r["findings"], [(c["task_id"], "lease_expired")])
        t = self.task(c["task_id"])
        self.assertEqual((t["execution_state"], t["blocked_reason"]), ("BLOCKED", "worker_unreachable"))
        self.assertEqual(self.svc.conn.execute("SELECT state FROM attempts WHERE attempt_id=?", (att,)).fetchone()[0], "LOST")
        self.assertEqual(self.one("SELECT state FROM leases"), "release_pending")
        self.assertEqual(self.one("SELECT state FROM workers"), "termination_requested")
        with self.assertRaises(StaleFence):                         # the old holder's token no longer works
            self.svc.transition_execution(CTL, c["task_id"], "QUEUED", fence=c["fence"])
        n = len(self.events())
        self.assertEqual(wd.scan()["changes"], 0)                   # idempotent: no new events
        self.assertEqual(len(self.events()), n)

    def test_silent_worker_detected_and_requeued_once_termination_proven(self):
        c, _ = self.running()
        self.clock.t += 100
        dead = set()
        wd = Watchdog(self.svc, 60, is_terminated=lambda wid: wid in dead)
        self.assertEqual(wd.scan()["findings"], [(c["task_id"], "worker_silent")])
        self.assertEqual(self.state(c["task_id"]), "BLOCKED")
        wd.scan()
        self.assertEqual(self.state(c["task_id"]), "BLOCKED")       # not proven dead: stays blocked
        dead.add(self.w)
        wd.scan()
        self.assertEqual(self.state(c["task_id"]), "QUEUED")
        self.assertEqual(self.one("SELECT state FROM leases"), "released")
        self.assertEqual(scheduler.next_ready(self.svc), c["task_id"])

    def test_stuck_leasing_detected(self):
        tid = self.mk()
        scheduler.claim_next(self.svc, self.w, "slot", "k")
        self.clock.t += 100
        self.assertEqual(Watchdog(self.svc, 60).scan()["findings"], [(tid, "leasing_stuck")])
        self.assertEqual(self.state(tid), "BLOCKED")

    def test_cancelling_task_left_alone(self):
        c, _ = self.running(ttl=1)
        self.svc.request_cancel(CTL, c["task_id"])
        self.clock.t += 5
        self.assertEqual(Watchdog(self.svc, 1000).scan()["changes"], 0)


class RecoveryTests(Durable):
    def reopen(self):
        self.svc.close()
        self.svc = StorageService(self.home, clock=self.clock, interval=self.interval).open()

    def test_restart_reconciles_in_flight_runs_and_is_idempotent(self):
        c1, _ = self.running()
        t2 = self.mk("queued")
        self.reopen()
        dead = set()
        r = recovery.recover(self.svc, lambda wid: wid in dead)
        self.assertEqual(r["orphaned"], [c1["task_id"]])
        self.assertEqual(self.state(c1["task_id"]), "BLOCKED")
        self.assertEqual(self.state(t2), "QUEUED")
        n = len(self.events())
        again = recovery.recover(self.svc, lambda wid: wid in dead)
        self.assertEqual((again["orphaned"], again["changes"]), ([], 0))
        self.assertEqual(len(self.events()), n)
        dead.add(self.w)
        recovery.recover(self.svc, lambda wid: wid in dead)
        self.assertEqual(self.state(c1["task_id"]), "QUEUED")
        self.assertEqual(scheduler.next_ready(self.svc), c1["task_id"])    # oldest first again
        self.assertEqual(recovery.recover(self.svc, lambda wid: True)["changes"], 0)

    def test_processing_order_depends_only_on_stored_state(self):
        for _ in range(3):
            self.mk()
        ids = [r[0] for r in self.svc.conn.execute("SELECT task_id FROM tasks ORDER BY created_at, task_id")]
        for i, tid in enumerate(ids):
            self.svc.create_lease(CTL, tid, self.w, "r", str(i))
        self.reopen()
        self.assertEqual(recovery.recover(self.svc)["orphaned"], ids)

    def test_dangling_lease_of_finished_task_is_released_when_worker_dead(self):
        c, att = self.running()
        self.svc.transition_attempt(CTL, att, "SUCCEEDED")
        self.svc.transition_execution(CTL, c["task_id"], "FINISHED", fence=c["fence"])
        self.reopen()
        recovery.recover(self.svc, lambda wid: True)
        self.assertEqual(self.one("SELECT state FROM leases"), "released")
