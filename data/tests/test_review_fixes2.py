"""Regression tests for the second independent review (H-1..H-4, M-1..M-6, L-1..L-5; decisions D-027..D-030)."""
import errno
import os
import unittest
from unittest import mock

from vanilla_agents import audit, lock
from vanilla_agents.contracts import AuthorityError, ControllerBusy, EvidenceError, IllegalTransition, ValidationError
from vanilla_agents.storage import StorageService
from tests.helpers import CTL, USER, VER, Base, worker


class ReleaseNeedsFreshVerification(Base):
    def released_setup(self):
        pid, tid, claims, qid = self.verified_task()
        pol = self.svc.add_policy(CTL, pid, {"p": 1})
        self.svc.set_task_policy(CTL, tid, pol)
        self.svc.set_task_target(CTL, tid, "commit-1")
        self.svc.set_verification(CTL, tid, "RUNNING")
        q = self.svc.set_verification(VER, tid, "VERIFIED")
        a = self.svc.request_approval(CTL, tid, "integration", "integrate", {"task_id": tid}, 3, self.clock.t + 3600, "commit-1", pol, q)
        self.svc.decide_approval(USER, a, "approved")
        self.svc.approve_release(CTL, tid, a)
        return pid, tid, a

    def test_h1_direct_stale_drops_the_qualification_and_revokes_the_release(self):
        pid, tid, a = self.released_setup()
        self.svc.set_verification(CTL, tid, "STALE")
        t = self.task(tid)
        self.assertEqual((t["verification_state"], t["qualification_id"], t["release_state"]), ("STALE", None, "REVOKED"))
        with self.assertRaises(Exception):
            self.svc.apply_release(CTL, tid, a)

    def test_h1_apply_rechecks_verification_even_if_state_was_forced(self):
        pid, tid, a = self.released_setup()
        self.svc.conn.execute("UPDATE tasks SET verification_state='STALE' WHERE task_id=?", (tid,))   # simulate a buggy caller
        with self.assertRaises(IllegalTransition) as cm:
            self.svc.apply_release(CTL, tid, a)
        self.assertEqual(cm.exception.code, "not_verified")

    def test_m1_altered_evidence_blocks_apply(self):
        pid, tid, a = self.released_setup()
        ev = self.svc.conn.execute("SELECT evidence_id FROM evidence").fetchone()[0]
        with open(self.svc.evidence.path_for(ev), "wb") as f:
            f.write(b"changed after qualification")
        with self.assertRaises(IllegalTransition) as cm:
            self.svc.apply_release(CTL, tid, a)
        self.assertEqual(cm.exception.code, "evidence_invalid")

    def test_m5_a_requalified_task_can_be_approved_again(self):
        pid, tid, a = self.released_setup()
        self.svc.set_verification(CTL, tid, "STALE")          # revokes the release
        self.svc.set_verification(CTL, tid, "RUNNING")
        q2 = self.svc.set_verification(VER, tid, "VERIFIED")
        t = self.task(tid)
        a2 = self.svc.request_approval(CTL, tid, "integration", "integrate", {"task_id": tid}, 3, self.clock.t + 3600, "commit-1", t["policy_hash"], q2)
        self.svc.decide_approval(USER, a2, "approved")
        self.svc.approve_release(CTL, tid, a2)
        self.assertEqual(self.task(tid)["release_state"], "APPROVED")


class CancelNeedsEveryWorkerTerminated(Base):
    def setUp(self):
        super().setUp()
        self.pid, self.tid = self.project_task()
        self.wid = self.svc.register_worker(CTL, "fixture", "0")
        self.svc.set_worker_state(CTL, self.wid, "alive")
        self.aid = self.svc.create_attempt(CTL, self.tid, self.wid)
        self.svc.transition_attempt(CTL, self.aid, "ACTIVE")

    def test_h2_a_lost_attempt_does_not_prove_the_worker_stopped(self):
        self.svc.transition_attempt(CTL, self.aid, "LOST")
        self.svc.request_cancel(CTL, self.tid)
        self.svc.advance_cancel(CTL, self.tid, "TERMINATING")
        with self.assertRaises(IllegalTransition):
            self.svc.advance_cancel(CTL, self.tid, "CONFIRMED")
        self.svc.set_worker_state(CTL, self.wid, "termination_requested")
        self.svc.set_worker_state(CTL, self.wid, "termination_confirmed")
        self.svc.advance_cancel(CTL, self.tid, "CONFIRMED")

    def test_h2_an_older_attempt_with_a_live_worker_still_blocks(self):
        old_worker = self.svc.register_worker(CTL, "fixture", "0")
        self.svc.set_worker_state(CTL, old_worker, "alive")
        self.svc.transition_attempt(CTL, self.aid, "FAILED")
        a2 = self.svc.create_attempt(CTL, self.tid, old_worker)
        self.svc.transition_attempt(CTL, a2, "ACTIVE")
        self.svc.set_worker_state(CTL, old_worker, "termination_requested")
        self.svc.set_worker_state(CTL, old_worker, "termination_confirmed")
        self.svc.request_cancel(CTL, self.tid)
        self.svc.advance_cancel(CTL, self.tid, "TERMINATING")
        with self.assertRaises(IllegalTransition):             # the first worker is still alive
            self.svc.advance_cancel(CTL, self.tid, "CONFIRMED")

    def test_m4_cancellation_blocks_progress_attempts_and_leases(self):
        self.svc.request_cancel(CTL, self.tid)
        with self.assertRaises(IllegalTransition) as cm:
            self.svc.transition_execution(CTL, self.tid, "LEASING")
        self.assertEqual(cm.exception.code, "cancel_in_progress")
        with self.assertRaises(IllegalTransition):
            self.svc.create_attempt(CTL, self.tid, self.wid)
        with self.assertRaises(IllegalTransition):
            self.svc.create_lease(CTL, self.tid, self.wid, "port", "1")


class ClaimsMustMatchTheTask(Base):
    def setUp(self):
        super().setUp()
        self.pid, self.tid = self.project_task()
        self.svc.update_task_source(CTL, self.tid, "c1", "e1")
        self.crit = self.svc.add_criterion(CTL, self.tid, "c", True, "k")
        self.ev = self.svc.put_evidence(VER, b"evidence")

    def test_h3_claim_for_a_different_commit_or_environment_is_rejected(self):
        for commit, env in (("old-commit", "e1"), ("c1", "other-env")):
            with self.assertRaises(ValidationError) as cm:
                self.svc.insert_claim(VER, self.claim(self.tid, self.crit, "k", [self.ev], commit, env))
            self.assertEqual(cm.exception.code, "claim_source_mismatch")

    def test_h3_claim_check_must_match_the_criterion(self):
        with self.assertRaises(ValidationError) as cm:
            self.svc.insert_claim(VER, self.claim(self.tid, self.crit, "some-other-check", [self.ev], "c1", "e1"))
        self.assertEqual(cm.exception.code, "check_mismatch")

    def test_h3_attempt_must_belong_to_the_task(self):
        _, other = self.project_task("other")
        att = self.svc.create_attempt(CTL, other)
        data = self.claim(self.tid, self.crit, "k", [self.ev], "c1", "e1")
        data["attempt_id"] = att
        with self.assertRaises(ValidationError) as cm:
            self.svc.insert_claim(VER, data)
        self.assertEqual(cm.exception.code, "attempt_mismatch")

    def test_h3_qualification_needs_a_recorded_source(self):
        pid, tid = self.project_task("nosource")
        crit = self.svc.add_criterion(CTL, tid, "c", True, "k")
        ev = self.svc.put_evidence(VER, b"e2")
        self.svc.insert_claim(VER, self.claim(tid, crit, "k", [ev]))
        self.svc.set_verification(CTL, tid, "RUNNING")
        with self.assertRaises(IllegalTransition) as cm:
            self.svc.set_verification(VER, tid, "VERIFIED")
        self.assertEqual(cm.exception.code, "task_source_not_set")

    def test_h4_no_required_criteria_cannot_be_verified(self):
        pid, tid = self.project_task("empty")
        self.svc.update_task_source(CTL, tid, "c1", "e1")
        self.svc.set_verification(CTL, tid, "RUNNING")
        with self.assertRaises(IllegalTransition):
            self.svc.set_verification(VER, tid, "VERIFIED")
        self.svc.add_criterion(CTL, tid, "optional only", False, "x")
        with self.assertRaises(IllegalTransition):
            self.svc.set_verification(VER, tid, "VERIFIED")

    def test_h4_a_verified_claim_needs_evidence(self):
        with self.assertRaises(ValidationError) as cm:
            self.svc.insert_claim(VER, self.claim(self.tid, self.crit, "k", [], "c1", "e1"))
        self.assertEqual(cm.exception.code, "verified_claim_needs_evidence")
        self.svc.insert_claim(VER, self.claim(self.tid, self.crit, "k", [], "c1", "e1", status="FAILED"))   # a failing claim may have none


class ApprovalRequestsAreConstrained(Base):
    def setUp(self):
        super().setUp()
        self.pid, self.tid = self.project_task()
        self.svc.register_worker(CTL, "fixture", "0", "w1")

    def row(self, aid):
        return self.svc.conn.execute("SELECT * FROM approvals WHERE approval_id=?", (aid,)).fetchone()

    def test_m2_a_worker_needs_an_attempt_on_the_task(self):
        with self.assertRaises(AuthorityError):
            self.svc.request_approval(worker("w1"), self.tid, "action", "install_dependency", {"p": 1}, 2, self.clock.t + 10)

    def test_m2_tier_is_derived_from_the_operation_not_the_requester(self):
        self.svc.create_attempt(CTL, self.tid, "w1")
        a = self.svc.request_approval(worker("w1"), self.tid, "action", "push", {"remote": "origin"}, 0, self.clock.t + 10)
        self.assertEqual(self.row(a)["tier"], 3)
        with self.assertRaises(AuthorityError):                # so the controller cannot decide it
            self.svc.decide_approval(CTL, a, "approved")
        low = self.svc.request_approval(worker("w1"), self.tid, "action", "install_dependency", {"p": 1}, 0, self.clock.t + 10)
        self.assertEqual(self.row(low)["tier"], 2)
        high = self.svc.request_approval(worker("w1"), self.tid, "action", "install_dependency", {"p": 2}, 3, self.clock.t + 10)
        self.assertEqual(self.row(high)["tier"], 3)            # a requester may only ask for a higher tier

    def test_m2_expiry_is_capped(self):
        self.svc.create_attempt(CTL, self.tid, "w1")
        a = self.svc.request_approval(worker("w1"), self.tid, "action", "install_dependency", {"p": 1}, 2, self.clock.t + 10 ** 9)
        self.assertLessEqual(self.row(a)["expires_at"] / 1e6, self.clock.t + 24 * 3600 + 1)


class EvidenceRegistrationIsValidated(Base):
    def test_m6_bad_ids_and_paths_are_rejected(self):
        h = self.svc.put_evidence(VER, b"real")
        for args in (("../../x", 4, "real", "../../x"), ("A" * 64, 4, "x", "AA/" + "A" * 64), (h, 4, "x", "wrong/path"), (h, 999, "x", "%s/%s" % (h[:2], h))):
            with self.assertRaises(ValidationError):
                self.svc.register_evidence(VER, *args[:1], *args[1:2], *args[2:3], args[3])
        with self.assertRaises(EvidenceError):
            self.svc.evidence.path_for("..\\..\\escape")

    def test_m6_registration_needs_an_existing_matching_file(self):
        fake = "a" * 64
        with self.assertRaises(Exception):
            self.svc.register_evidence(VER, fake, 3, "x", "%s/%s" % (fake[:2], fake))

    def test_l4_unauthorized_put_evidence_leaves_a_rejection_event(self):
        before = len(self.events("rejected"))
        with self.assertRaises(AuthorityError):
            self.svc.put_evidence(worker("w"), b"x")
        self.assertEqual(len(self.events("rejected")), before + 1)


class MiscellaneousReviewFixes(Base):
    def test_l1_hash_separator_ambiguity_is_gone(self):
        a = audit.event_hash("e", 1, 1, "t", "x|T", "U", "k", "{}", "p")
        b = audit.event_hash("e", 1, 1, "t", "x", "T|U", "k", "{}", "p")
        self.assertNotEqual(a, b)
        self.assertNotEqual(audit.event_hash("e", 1, 1, "t", "x", None, "k", "{}", "p"), audit.event_hash("e", 1, 1, "t", "x", "", "k", "{}", "p") + "z")

    def test_l5_policies_must_belong_to_the_project(self):
        pid, tid = self.project_task()
        with self.assertRaises(ValidationError):
            self.svc.add_policy(CTL, "proj_missing", {"a": 1})
        with self.assertRaises(ValidationError) as cm:
            self.svc.set_task_policy(CTL, tid, "f" * 64)
        self.assertEqual(cm.exception.code, "unknown_policy")
        other_pid = self.svc.create_project(CTL, "other")
        foreign = self.svc.add_policy(CTL, other_pid, {"b": 2})
        with self.assertRaises(ValidationError):
            self.svc.set_task_policy(CTL, tid, foreign)

    def test_m3_unknown_posture_is_refused(self):
        with self.assertRaises(ValidationError):
            StorageService(os.path.join(self.tmp, "p2"), posture="level_9_unbreakable")

    def test_l3_only_contention_errors_mean_another_controller(self):
        path = os.path.join(self.tmp, "lk", "c.lock")
        if lock.msvcrt is None:
            self.skipTest("msvcrt path only")
        with mock.patch.object(lock.msvcrt, "locking", side_effect=OSError(errno.EINVAL, "bad")):
            with self.assertRaises(OSError) as cm:
                lock.ControllerLock(path, "x").acquire()
            self.assertNotIsInstance(cm.exception, ControllerBusy)
        with mock.patch.object(lock.msvcrt, "locking", side_effect=OSError(errno.EACCES, "locked")):
            with self.assertRaises(ControllerBusy):
                lock.ControllerLock(path, "x").acquire()

    def test_l2_a_failed_start_keeps_the_crash_record_for_the_next_start(self):
        home = os.path.join(self.tmp, "crash")
        svc = StorageService(home).open()
        svc.lock.release(clear_record=False)       # simulate a crash: lock freed, owner record left behind
        svc.conn.close()
        bad = [(1, "initial", "THIS IS NOT SQL;")]
        with self.assertRaises(Exception):
            StorageService(home, migrations=bad).open()   # migration checksum or SQL failure before takeover is recorded
        again = StorageService(home).open()
        try:
            self.assertEqual(again.conn.execute("SELECT COUNT(*) FROM events WHERE kind='controller_takeover'").fetchone()[0], 1)
        finally:
            again.close()


if __name__ == "__main__":
    unittest.main()
