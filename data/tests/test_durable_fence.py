"""D-048: state-changing storage operations reject a stale, expired or foreign fencing token."""
from vanilla_agents.contracts import StaleFence
from tests.helpers import CTL, Base


class FenceWiringTests(Base):
    def setUp(self):
        super().setUp()
        _, self.tid = self.project_task()
        self.w1 = self.svc.register_worker(CTL)
        self.w2 = self.svc.register_worker(CTL)

    def _rotate(self, lease, worker):
        self.svc.set_worker_state(CTL, worker, "termination_requested")
        self.svc.set_worker_state(CTL, worker, "termination_confirmed")
        self.svc.request_lease_release(CTL, lease["lease_id"])
        self.svc.confirm_lease_release(CTL, lease["lease_id"])

    def test_current_token_allows_and_stale_token_rejects_every_fenced_operation(self):
        a = self.svc.create_lease(CTL, self.tid, self.w1, "slot", "s")
        fa = ("slot", "s", a["fencing_token"])
        self.svc.transition_execution(CTL, self.tid, "LEASING", fence=fa)
        att = self.svc.create_attempt(CTL, self.tid, self.w1, fence=fa)
        self._rotate(a, self.w1)
        b = self.svc.create_lease(CTL, self.tid, self.w2, "slot", "s")
        before = tuple(self.task(self.tid))
        for call in (lambda: self.svc.transition_execution(CTL, self.tid, "RUNNING", fence=fa),
                     lambda: self.svc.block_task(CTL, self.tid, "disk", fence=fa),
                     lambda: self.svc.create_attempt(CTL, self.tid, self.w1, fence=fa),
                     lambda: self.svc.transition_attempt(CTL, att, "ACTIVE", fence=fa)):
            with self.assertRaises(StaleFence):
                call()
        self.assertEqual(tuple(self.task(self.tid)), before)
        self.assertEqual(len(self.events("rejected")), 4)
        self.svc.transition_execution(CTL, self.tid, "RUNNING", fence=("slot", "s", b["fencing_token"]))

    def test_unfenced_calls_still_work(self):
        self.svc.transition_execution(CTL, self.tid, "LEASING")

    def test_expired_lease_and_foreign_task_rejected(self):
        a = self.svc.create_lease(CTL, self.tid, self.w1, "slot", "s", ttl=0)
        with self.assertRaises(StaleFence):
            self.svc.transition_execution(CTL, self.tid, "LEASING", fence=("slot", "s", a["fencing_token"]))
        _, other = self.project_task("t2")
        b = self.svc.create_lease(CTL, other, self.w2, "slot2", "s")
        with self.assertRaises(StaleFence):
            self.svc.transition_execution(CTL, self.tid, "LEASING", fence=("slot2", "s", b["fencing_token"]))
