"""Stage 3 gap closers: retry cap (D-060) and skip-busy scheduling (D-061)."""
from vanilla_agents.contracts import LeaseBusy
from vanilla_agents.durable import recovery, scheduler
from tests.test_durable_core import Durable


class RetryCap(Durable):
    def crash_once(self, max_attempts):
        """Run the task, crash the controller view, recover with worker proven dead."""
        c = scheduler.claim_next(self.svc, self.w, "slot", "k")
        scheduler.start_run(self.svc, c, self.w)
        recovery.recover(self.svc, lambda wid: True, max_attempts)
        return c["task_id"]

    def test_requeued_until_cap_then_blocked_for_good(self):
        tid = self.mk()
        for _ in range(2):
            self.assertEqual(self.crash_once(3), tid)
            self.assertEqual(self.state(tid), "QUEUED")
        self.crash_once(3)
        t = self.task(tid)
        self.assertEqual((t["execution_state"], t["blocked_reason"]), ("BLOCKED", "retries_exhausted"))
        self.assertIsNone(scheduler.next_ready(self.svc))
        self.assertEqual(recovery.recover(self.svc, lambda wid: True, 3)["changes"], 0)   # stays put
        self.assertEqual(self.one("SELECT COUNT(*) FROM leases WHERE state!='released'"), 0)  # but cleaned up

    def test_cap_is_configurable_and_none_disables(self):
        tid = self.mk()
        self.crash_once(1)
        self.assertEqual(self.task(tid)["blocked_reason"], "retries_exhausted")
        t2 = self.mk("b")
        for _ in range(5):
            c = scheduler.claim_next(self.svc, self.w, "slot", "k2")
            scheduler.start_run(self.svc, c, self.w)
            recovery.recover(self.svc, lambda wid: True, None)
        self.assertEqual(self.state(t2), "QUEUED")


class SkipBusy(Durable):
    def test_skips_busy_oldest_to_next_ready_without_reordering(self):
        a, b = self.mk("a"), self.mk("b")
        res = {a: ("slot", "x"), b: ("slot", "y")}.get
        self.svc.create_lease(recovery.CTL, a, self.w, "slot", "x", 3600)   # a's resource is busy
        c = scheduler.claim_next(self.svc, self.w, None, None, resource_of=res)
        self.assertEqual(c["task_id"], b)
        self.assertEqual(self.state(a), "QUEUED")
        self.assertEqual(scheduler.ready_tasks(self.svc), [a])               # a still first in line

    def test_all_busy_raises_and_changes_nothing(self):
        a, b = self.mk("a"), self.mk("b")
        self.svc.create_lease(recovery.CTL, a, self.w, "slot", "k", 3600)
        with self.assertRaises(LeaseBusy):
            scheduler.claim_next(self.svc, self.w, "slot", "k")
        self.assertEqual(scheduler.ready_tasks(self.svc), [a, b])
