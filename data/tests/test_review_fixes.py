"""Regression tests for the independent-review findings (D-024, D-025, D-026)."""
import os
import unittest

from vanilla_agents import audit
from vanilla_agents.contracts import AuthorityError, IllegalTransition
from tests.helpers import CTL, USER, VER, Base, worker
from tests.test_integrity import tamper_connection


class EvidenceAtPromotionTests(Base):
    def prepared(self):
        pid, tid = self.project_task()
        self.svc.update_task_source(CTL, tid, "c1", "e1")
        crit = self.svc.add_criterion(CTL, tid, "c", True, "k")
        ev = self.svc.put_evidence(VER, b"original evidence")
        self.svc.insert_claim(VER, self.claim(tid, crit, "k", [ev]))
        self.svc.set_verification(CTL, tid, "RUNNING")
        return tid, crit, ev

    def test_promotion_is_refused_when_evidence_was_altered(self):
        tid, crit, ev = self.prepared()
        with open(self.svc.evidence.path_for(ev), "wb") as f:
            f.write(b"altered")
        with self.assertRaises(IllegalTransition) as cm:
            self.svc.set_verification(VER, tid, "VERIFIED")
        self.assertEqual(cm.exception.code, "evidence_invalid")
        self.assertEqual(self.task(tid)["verification_state"], "RUNNING")

    def test_promotion_is_refused_when_evidence_is_missing(self):
        tid, crit, ev = self.prepared()
        os.remove(self.svc.evidence.path_for(ev))
        with self.assertRaises(IllegalTransition) as cm:
            self.svc.set_verification(VER, tid, "VERIFIED")
        self.assertEqual(cm.exception.code, "evidence_invalid")

    def test_promotion_succeeds_with_intact_evidence(self):
        tid, crit, ev = self.prepared()
        self.assertTrue(self.svc.set_verification(VER, tid, "VERIFIED"))

    def test_revalidation_invalidates_claims_and_stales_a_verified_task(self):
        pid, tid, claims, qid = self.verified_task()
        ev = self.svc.conn.execute("SELECT evidence_id FROM evidence").fetchone()[0]
        os.remove(self.svc.evidence.path_for(ev))
        invalidated = self.svc.revalidate_evidence(VER, tid)
        self.assertEqual(invalidated, claims)
        t = self.task(tid)
        self.assertEqual((t["verification_state"], t["qualification_id"]), ("STALE", None))
        reason = self.svc.conn.execute("SELECT reason FROM claim_invalidations").fetchone()[0]
        self.assertEqual(reason, "evidence_missing")

    def test_revalidation_does_nothing_when_evidence_is_intact(self):
        pid, tid, claims, qid = self.verified_task()
        self.assertEqual(self.svc.revalidate_evidence(CTL, tid), [])
        self.assertEqual(self.task(tid)["verification_state"], "VERIFIED")

    def test_workers_cannot_revalidate(self):
        pid, tid, claims, qid = self.verified_task()
        with self.assertRaises(AuthorityError):
            self.svc.revalidate_evidence(worker("w"), tid)


class QualificationFreshnessTests(Base):
    def test_adding_a_required_criterion_makes_a_verified_task_stale(self):
        pid, tid, claims, qid = self.verified_task()
        self.svc.add_criterion(CTL, tid, "a new requirement", True, "new")
        t = self.task(tid)
        self.assertEqual((t["verification_state"], t["qualification_id"]), ("STALE", None))
        self.svc.set_verification(CTL, tid, "RUNNING")
        with self.assertRaises(IllegalTransition):             # the new criterion has no claim
            self.svc.set_verification(VER, tid, "VERIFIED")

    def test_a_release_cannot_proceed_after_a_criterion_was_added(self):
        pid, tid, claims, qid = self.verified_task()
        pol = self.svc.add_policy(CTL, pid, {"p": 1})
        self.svc.set_task_policy(CTL, tid, pol)
        self.svc.set_task_target(CTL, tid, "commit-x")
        self.svc.set_verification(CTL, tid, "RUNNING")
        q = self.svc.set_verification(VER, tid, "VERIFIED")
        a = self.svc.request_approval(CTL, tid, "integration", "integrate", {"task_id": tid}, 3, self.clock.t + 3600, "commit-x", pol, q)
        self.svc.decide_approval(USER, a, "approved")
        self.svc.add_criterion(CTL, tid, "late requirement", True, "late")
        with self.assertRaises(Exception):
            self.svc.approve_release(CTL, tid, a)

    def test_changing_the_target_makes_the_qualification_stale(self):
        pid, tid, claims, qid = self.verified_task()
        self.svc.set_task_target(CTL, tid, "t1")
        self.assertEqual(self.task(tid)["verification_state"], "STALE")
        self.svc.set_verification(CTL, tid, "RUNNING")
        self.svc.set_verification(VER, tid, "VERIFIED")
        self.svc.set_task_target(CTL, tid, "t1")                # same target again: nothing changes
        self.assertEqual(self.task(tid)["verification_state"], "VERIFIED")

    def test_a_new_failing_claim_replaces_the_old_one_and_stales_the_task(self):
        pid, tid, claims, qid = self.verified_task()
        crit = self.svc.conn.execute("SELECT criterion_id, check_id FROM criteria").fetchone()
        ev = self.svc.put_evidence(VER, b"a later, failing run")
        self.svc.insert_claim(VER, self.claim(tid, crit["criterion_id"], crit["check_id"], [ev], status="FAILED"))
        t = self.task(tid)
        self.assertEqual((t["verification_state"], t["qualification_id"]), ("STALE", None))
        self.svc.set_verification(CTL, tid, "RUNNING")
        with self.assertRaises(IllegalTransition):
            self.svc.set_verification(VER, tid, "VERIFIED")

    def test_a_first_claim_does_not_stale_anything(self):
        pid, tid = self.project_task()
        crit = self.svc.add_criterion(CTL, tid, "c", True, "k")
        ev = self.svc.put_evidence(VER, b"e")
        self.svc.insert_claim(VER, self.claim(tid, crit, "k", [ev]))
        self.assertEqual(self.task(tid)["verification_state"], "NOT_RUN")


class AuditAttributionTests(Base):
    def make(self):
        pid, tid = self.project_task()
        self.svc.transition_execution(CTL, tid, "LEASING")
        return tid

    def test_changing_the_task_attribution_of_an_event_breaks_the_chain(self):
        tid = self.make()
        c = tamper_connection(self.svc)
        seq = c.execute("SELECT seq FROM events WHERE task_id=? ORDER BY seq LIMIT 1", (tid,)).fetchone()[0]
        c.execute("UPDATE events SET task_id='task_other' WHERE seq=?", (seq,))
        r = audit.verify_chain(c)
        self.assertEqual((r["ok"], r["break_seq"]), (False, seq))

    def test_changing_an_event_id_breaks_the_chain(self):
        self.make()
        c = tamper_connection(self.svc)
        c.execute("UPDATE events SET event_id='evt_forged' WHERE seq=2")
        r = audit.verify_chain(c)
        self.assertEqual((r["ok"], r["break_seq"]), (False, 2))

    def test_removing_the_task_attribution_breaks_the_chain(self):
        tid = self.make()
        c = tamper_connection(self.svc)
        c.execute("UPDATE events SET task_id=NULL WHERE task_id=?", (tid,))
        self.assertFalse(audit.verify_chain(c)["ok"])


class WorkerApprovalRequestsStayPending(Base):
    def test_a_worker_request_is_only_a_pending_row(self):
        pid, tid = self.project_task()
        self.svc.register_worker(CTL, "fixture", "0", "w1")
        self.svc.create_attempt(CTL, tid, "w1")
        a = self.svc.request_approval(worker("w1"), tid, "action", "install_dependency", {"package": "x"}, 2, self.clock.t + 100)
        row = self.svc.conn.execute("SELECT * FROM approvals WHERE approval_id=?", (a,)).fetchone()
        self.assertEqual((row["decision"], row["decided_by"], row["consumed_at"], row["requested_by"]), (None, None, None, "w1"))
        for fn in (lambda: self.svc.decide_approval(worker("w1"), a, "approved"),
                   lambda: self.svc.use_action_approval(worker("w1"), tid, a, "install_dependency", {"package": "x"})):
            with self.assertRaises(AuthorityError):
                fn()
        self.assertIsNone(self.svc.conn.execute("SELECT decision FROM approvals").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
