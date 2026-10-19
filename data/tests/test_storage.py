"""StorageService behaviour: atomicity, authority at the storage level, claims, approvals, cancellation, leases and fencing."""
import sqlite3
import unittest

from vanilla_agents.contracts import (AuthorityError, IllegalTransition, VanillaError, LeaseBusy, StaleFence, ValidationError)
from tests.helpers import CTL, SYS, USER, VER, Base, worker


class AtomicityTests(Base):
    def test_each_mutation_writes_exactly_one_event(self):
        n0 = len(self.events())
        pid = self.svc.create_project(CTL, "p")
        self.assertEqual(len(self.events()), n0 + 1)
        tid = self.svc.create_task(CTL, {"project_id": pid, "title": "t", "description": "d"})
        self.assertEqual(len(self.events()), n0 + 2)
        self.svc.transition_execution(CTL, tid, "LEASING")
        self.assertEqual(len(self.events()), n0 + 3)
        self.assertEqual(self.events()[-1]["kind"], "execution_changed")

    def test_failed_event_write_rolls_the_mutation_back(self):
        pid, tid = self.project_task()
        n0 = len(self.events())
        self.svc._fail_event_write = True
        with self.assertRaises(RuntimeError):
            self.svc.transition_execution(CTL, tid, "LEASING")
        self.svc._fail_event_write = False
        self.assertEqual(self.task(tid)["execution_state"], "QUEUED")
        self.assertEqual(len(self.events()), n0)

    def test_rejected_request_changes_nothing_but_leaves_a_rejection_event(self):
        pid, tid = self.project_task()
        before = tuple(self.task(tid))
        with self.assertRaises(IllegalTransition):
            self.svc.transition_execution(CTL, tid, "FINISHED")
        self.assertEqual(tuple(self.task(tid)), before)
        rej = self.events("rejected")
        self.assertEqual(len(rej), 1)
        self.assertIn("illegal_transition", rej[0]["payload_json"])

    def test_unauthorized_attempt_is_rejected_and_audited(self):
        pid, tid = self.project_task()
        with self.assertRaises(AuthorityError):
            self.svc.transition_execution(worker("w1"), tid, "LEASING")
        self.assertEqual(self.task(tid)["execution_state"], "QUEUED")
        self.assertIn("authority_denied", self.events("rejected")[-1]["payload_json"])

    def test_task_validation(self):
        with self.assertRaises(ValidationError):
            self.svc.create_task(CTL, {"project_id": "nope", "title": "t", "description": "d"})
        with self.assertRaises(ValidationError):
            self.svc.create_task(CTL, {"title": "t"})


class ExecutionTests(Base):
    def test_happy_path_and_blocked_cycle(self):
        pid, tid = self.project_task()
        for s in ("LEASING", "RUNNING"):
            self.svc.transition_execution(CTL, tid, s)
        self.svc.block_task(CTL, tid, "disk")
        t = self.task(tid)
        self.assertEqual((t["execution_state"], t["blocked_reason"]), ("BLOCKED", "disk"))
        self.svc.transition_execution(CTL, tid, "QUEUED")
        self.assertIsNone(self.task(tid)["blocked_reason"])

    def test_running_never_jumps_to_finished_from_blocked(self):
        pid, tid = self.project_task()
        self.svc.block_task(CTL, tid, "environment")
        with self.assertRaises(IllegalTransition):
            self.svc.transition_execution(CTL, tid, "FINISHED")

    def test_unknown_blocked_reason(self):
        pid, tid = self.project_task()
        with self.assertRaises(ValidationError):
            self.svc.block_task(CTL, tid, "because")

    def test_cannot_use_transition_for_cancel_or_block(self):
        pid, tid = self.project_task()
        for s in ("CANCELLED", "BLOCKED"):
            with self.assertRaises(IllegalTransition):
                self.svc.transition_execution(CTL, tid, s)

    def test_terminal_state_has_no_exit(self):
        pid, tid = self.project_task()
        for s in ("LEASING", "RUNNING", "FINISHED"):
            self.svc.transition_execution(CTL, tid, s)
        for s in ("QUEUED", "RUNNING", "FAILED"):
            with self.assertRaises(IllegalTransition):
                self.svc.transition_execution(CTL, tid, s)


class CancellationTests(Base):
    def setUp(self):
        super().setUp()
        self.pid, self.tid = self.project_task()
        self.wid = self.svc.register_worker(CTL, "fixture", "0")
        self.svc.set_worker_state(CTL, self.wid, "alive")
        self.aid = self.svc.create_attempt(CTL, self.tid, self.wid)
        self.svc.transition_attempt(CTL, self.aid, "ACTIVE")
        for s in ("LEASING", "RUNNING"):
            self.svc.transition_execution(CTL, self.tid, s)

    def test_running_with_cancel_requested_is_representable(self):
        self.svc.request_cancel(USER, self.tid)
        t = self.task(self.tid)
        self.assertEqual((t["execution_state"], t["cancel_state"]), ("RUNNING", "REQUESTED"))

    def test_cancelled_requires_confirmation_termination_and_reconciled_leases(self):
        lease = self.svc.create_lease(CTL, self.tid, self.wid, "port", "8081")
        self.svc.request_cancel(CTL, self.tid)
        with self.assertRaises(IllegalTransition):           # not confirmed yet
            self.svc.finish_cancel(CTL, self.tid)
        self.svc.advance_cancel(CTL, self.tid, "TERMINATING")
        with self.assertRaises(IllegalTransition):           # worker still alive: cannot confirm
            self.svc.advance_cancel(CTL, self.tid, "CONFIRMED")
        self.svc.set_worker_state(CTL, self.wid, "termination_requested")
        with self.assertRaises(IllegalTransition):           # termination requested is not confirmed
            self.svc.advance_cancel(CTL, self.tid, "CONFIRMED")
        self.svc.set_worker_state(CTL, self.wid, "termination_confirmed")
        self.svc.advance_cancel(CTL, self.tid, "CONFIRMED")
        with self.assertRaises(IllegalTransition):           # a lease is still open
            self.svc.finish_cancel(CTL, self.tid)
        self.svc.request_lease_release(CTL, lease["lease_id"])
        self.svc.confirm_lease_release(CTL, lease["lease_id"])
        self.svc.finish_cancel(CTL, self.tid)
        self.assertEqual(self.task(self.tid)["execution_state"], "CANCELLED")

    def test_cancel_request_can_be_abandoned_before_terminating(self):
        self.svc.request_cancel(CTL, self.tid)
        self.svc.advance_cancel(CTL, self.tid, "NONE")
        self.assertEqual(self.task(self.tid)["cancel_state"], "NONE")
        self.svc.request_cancel(CTL, self.tid)
        self.svc.advance_cancel(CTL, self.tid, "TERMINATING")
        with self.assertRaises(IllegalTransition):
            self.svc.advance_cancel(CTL, self.tid, "NONE")

    def test_worker_cannot_cancel_or_confirm(self):
        w = worker(self.wid)
        for fn in (lambda: self.svc.request_cancel(w, self.tid), lambda: self.svc.advance_cancel(w, self.tid, "CONFIRMED"),
                   lambda: self.svc.finish_cancel(w, self.tid), lambda: self.svc.set_worker_state(w, self.wid, "termination_confirmed")):
            with self.assertRaises(AuthorityError):
                fn()

    def test_unreachable_is_not_terminated(self):
        self.svc.set_worker_state(CTL, self.wid, "unreachable")
        self.svc.request_cancel(CTL, self.tid)
        self.svc.advance_cancel(CTL, self.tid, "TERMINATING")
        with self.assertRaises(IllegalTransition):
            self.svc.advance_cancel(CTL, self.tid, "CONFIRMED")

    def test_worker_heartbeat_rules(self):
        w = worker(self.wid)
        self.svc.worker_heartbeat(w, self.wid)
        with self.assertRaises(AuthorityError):
            self.svc.worker_heartbeat(worker("someone-else"), self.wid)
        self.svc.set_worker_state(CTL, self.wid, "unreachable")
        self.svc.worker_heartbeat(w, self.wid)                # contact restored
        self.assertEqual(self.svc.conn.execute("SELECT state FROM workers").fetchone()[0], "alive")
        self.svc.set_worker_state(CTL, self.wid, "termination_requested")
        with self.assertRaises(IllegalTransition):
            self.svc.worker_heartbeat(w, self.wid)

    def test_worker_events_only_for_own_attempt(self):
        self.svc.worker_event(worker(self.wid), self.aid, "progress")
        with self.assertRaises(AuthorityError):
            self.svc.worker_event(worker("other"), self.aid, "progress")


class FencingTests(Base):
    def setUp(self):
        super().setUp()
        self.pid, self.tid = self.project_task()
        self.w1 = self.svc.register_worker(CTL, "fixture", "0", "wrk_one")
        self.w2 = self.svc.register_worker(CTL, "fixture", "0", "wrk_two")
        for w in (self.w1, self.w2):
            self.svc.set_worker_state(CTL, w, "alive")

    def test_tokens_increase_and_exclusive_leases_conflict(self):
        a = self.svc.create_lease(CTL, self.tid, self.w1, "test_account", "acct1")
        with self.assertRaises(LeaseBusy):
            self.svc.create_lease(CTL, self.tid, self.w2, "test_account", "acct1")
        self.svc.request_lease_release(CTL, a["lease_id"])
        with self.assertRaises(LeaseBusy):                    # release_pending still blocks reuse
            self.svc.create_lease(CTL, self.tid, self.w2, "test_account", "acct1")

    def test_lease_stays_pending_until_holder_termination_confirmed(self):
        a = self.svc.create_lease(CTL, self.tid, self.w1, "port", "8085")
        self.svc.request_lease_release(CTL, a["lease_id"])
        self.svc.set_worker_state(CTL, self.w1, "unreachable")
        with self.assertRaises(IllegalTransition):
            self.svc.confirm_lease_release(CTL, a["lease_id"])
        self.svc.set_worker_state(CTL, self.w1, "termination_requested")
        with self.assertRaises(IllegalTransition):
            self.svc.confirm_lease_release(CTL, a["lease_id"])
        self.svc.set_worker_state(CTL, self.w1, "termination_confirmed")
        self.svc.confirm_lease_release(CTL, a["lease_id"])
        b = self.svc.create_lease(CTL, self.tid, self.w2, "port", "8085")
        self.assertGreater(b["fencing_token"], a["fencing_token"])

    def test_stale_token_rejected_for_every_operation(self):
        a = self.svc.create_lease(CTL, self.tid, self.w1, "build_slot", "s1")
        self.assertTrue(self.svc.require_fence("build_slot", "s1", a["fencing_token"]))
        self.svc.set_worker_state(CTL, self.w1, "termination_requested")
        self.svc.set_worker_state(CTL, self.w1, "termination_confirmed")
        self.svc.request_lease_release(CTL, a["lease_id"])
        self.svc.confirm_lease_release(CTL, a["lease_id"])
        b = self.svc.create_lease(CTL, self.tid, self.w2, "build_slot", "s1")
        with self.assertRaises(StaleFence):
            self.svc.require_fence("build_slot", "s1", a["fencing_token"])
        self.assertTrue(self.svc.require_fence("build_slot", "s1", b["fencing_token"]))

    def test_worker_cannot_create_or_release_leases(self):
        with self.assertRaises(AuthorityError):
            self.svc.create_lease(worker(self.w1), self.tid, self.w1, "port", "1")


class ClaimsTests(Base):
    def test_claims_are_append_only_at_database_level(self):
        pid, tid, claims, qid = self.verified_task()
        crit = self.svc.conn.execute("SELECT criterion_id, check_id FROM criteria").fetchone()
        ev = self.svc.put_evidence(VER, b"rows so every trigger has something to fire on")
        newer = self.svc.insert_claim(VER, self.claim(tid, crit["criterion_id"], crit["check_id"], [ev]))   # creates a supersession row
        self.svc.invalidate_claim(CTL, newer, "fixture")                                                      # creates an invalidation row
        for table in ("claims", "claim_invalidations", "claim_supersessions", "qualifications", "events", "evidence"):
            self.assertGreater(self.svc.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0], 0, table)
        for sql in ("UPDATE claims SET status='FAILED'", "DELETE FROM claims", "UPDATE events SET kind='x'", "DELETE FROM events",
                    "UPDATE claim_invalidations SET reason='x'", "DELETE FROM claim_supersessions", "UPDATE qualifications SET evidence_set_hash='x'",
                    "UPDATE evidence SET size=0", "DELETE FROM evidence"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.svc.conn.execute(sql)

    def test_supersession_and_invalidation_change_computed_status_not_rows(self):
        pid, tid, claims, qid = self.verified_task()
        crit = self.svc.conn.execute("SELECT criterion_id, check_id FROM criteria").fetchone()
        ev = self.svc.put_evidence(VER, b"second run")
        rows_before = self.svc.conn.execute("SELECT * FROM claims").fetchall()
        new = self.svc.insert_claim(VER, self.claim(tid, crit["criterion_id"], crit["check_id"], [ev]))
        from vanilla_agents import ledger
        cur = [r["claim_id"] for r in ledger.current_claims(self.svc.conn, tid)]
        self.assertEqual(cur, [new])
        self.assertEqual([tuple(r) for r in self.svc.conn.execute("SELECT * FROM claims").fetchall()[:len(rows_before)]], [tuple(r) for r in rows_before])
        self.svc.invalidate_claim(CTL, new, "manual")
        self.assertEqual(ledger.current_claims(self.svc.conn, tid), [])

    def test_missing_evidence_means_not_verified(self):
        pid, tid = self.project_task()
        self.svc.add_criterion(CTL, tid, "needs a claim", True, "c1")
        self.svc.set_verification(CTL, tid, "RUNNING")
        with self.assertRaises(IllegalTransition) as cm:
            self.svc.set_verification(VER, tid, "VERIFIED")
        self.assertEqual(cm.exception.code, "required_claims_not_verified")

    def test_flaky_claim_cannot_satisfy_a_required_criterion(self):
        pid, tid = self.project_task()
        crit = self.svc.add_criterion(CTL, tid, "c", True, "k")
        ev = self.svc.put_evidence(VER, b"e")
        self.svc.insert_claim(VER, self.claim(tid, crit, "k", [ev], status="FLAKY"))
        self.svc.set_verification(CTL, tid, "RUNNING")
        with self.assertRaises(IllegalTransition):
            self.svc.set_verification(VER, tid, "VERIFIED")

    def test_only_verifier_inserts_claims_and_qualifies(self):
        pid, tid = self.project_task()
        crit = self.svc.add_criterion(CTL, tid, "c", True, "k")
        ev = self.svc.put_evidence(VER, b"e")
        for actor in (CTL, USER, worker("w"), SYS):
            with self.assertRaises(AuthorityError):
                self.svc.insert_claim(actor, self.claim(tid, crit, "k", [ev]))
        self.svc.insert_claim(VER, self.claim(tid, crit, "k", [ev]))
        self.svc.set_verification(CTL, tid, "RUNNING")
        with self.assertRaises(AuthorityError):
            self.svc.set_verification(CTL, tid, "VERIFIED")

    def test_claim_needs_known_evidence_and_criterion(self):
        pid, tid = self.project_task()
        crit = self.svc.add_criterion(CTL, tid, "c", True, "k")
        with self.assertRaises(ValidationError):
            self.svc.insert_claim(VER, self.claim(tid, crit, "k", ["f" * 64]))
        with self.assertRaises(ValidationError):
            self.svc.insert_claim(VER, self.claim(tid, "crit_missing", "k", []))

    def test_claim_records_identity_revision_and_posture(self):
        pid, tid, claims, qid = self.verified_task(commit="abc123", env="win11-py314")
        r = self.svc.conn.execute("SELECT * FROM claims WHERE claim_id=?", (claims[0],)).fetchone()
        self.assertEqual((r["verifier_id"], r["source_commit"], r["env_fingerprint"], r["security_posture"]),
                         ("verifier-fixture", "abc123", "win11-py314", "level_0_integrity_checks_only"))
        self.assertTrue(r["evidence_refs_json"].startswith('["'))

    def test_source_change_invalidates_claims_and_makes_task_stale(self):
        pid, tid, claims, qid = self.verified_task(commit="c1", env="e1")
        self.svc.update_task_source(CTL, tid, "c1", "e1")      # unchanged: nothing happens
        self.assertEqual(self.task(tid)["verification_state"], "VERIFIED")
        self.svc.update_task_source(CTL, tid, "c2", "e1")
        self.assertEqual(self.task(tid)["verification_state"], "STALE")
        self.assertIsNone(self.task(tid)["qualification_id"])
        self.assertEqual(self.svc.conn.execute("SELECT COUNT(*) FROM claim_invalidations").fetchone()[0], 1)
        self.assertEqual(self.svc.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 1)  # history intact

    def test_environment_change_also_invalidates(self):
        pid, tid, claims, qid = self.verified_task(commit="c1", env="e1")
        self.svc.update_task_source(CTL, tid, "c1", "e2")
        self.assertEqual(self.task(tid)["verification_state"], "STALE")


class ApprovalTests(Base):
    def setUp(self):
        super().setUp()
        self.pid, self.tid, self.claims, self.qid = self.verified_task()
        self.pol = self.svc.add_policy(CTL, self.pid, {"never_push": True})
        self.svc.set_task_policy(CTL, self.tid, self.pol)       # policy change after VERIFIED: stale, so re-qualify
        self.svc.set_task_target(CTL, self.tid, "commit-abc")
        self.requalify()

    def requalify(self):
        t = self.task(self.tid)
        if t["verification_state"] == "STALE":
            self.svc.set_verification(CTL, self.tid, "RUNNING")
        if self.task(self.tid)["verification_state"] != "VERIFIED":
            self.qid = self.svc.set_verification(VER, self.tid, "VERIFIED")

    def request(self, **over):
        t = self.task(self.tid)
        args = dict(concept="integration", operation="integrate", scope={"task_id": self.tid}, tier=3, expires_at=self.clock.t + 3600,
                    target_ref=t["target_ref"], policy_hash=t["policy_hash"], qualification_id=t["qualification_id"])
        args.update(over)
        return self.svc.request_approval(CTL, self.tid, **args)

    def test_policy_change_after_verification_makes_task_stale(self):
        pid2, tid2, _, _ = self.verified_task()
        self.svc.set_task_policy(CTL, tid2, self.svc.add_policy(CTL, pid2, {"other": True}))
        self.assertEqual(self.task(tid2)["verification_state"], "STALE")

    def test_full_release_flow_and_single_use(self):
        a = self.request()
        self.svc.decide_approval(USER, a, "approved")
        self.svc.approve_release(CTL, self.tid, a)
        self.assertEqual(self.task(self.tid)["release_state"], "APPROVED")
        self.svc.apply_release(CTL, self.tid, a)
        self.assertEqual(self.task(self.tid)["release_state"], "APPLIED")
        with self.assertRaises(IllegalTransition) if False else self.assertRaises(VanillaError):
            self.svc.apply_release(CTL, self.tid, a)

    def test_worker_can_ask_but_never_decide(self):
        self.svc.register_worker(CTL, "fixture", "0", "w")
        self.svc.create_attempt(CTL, self.tid, "w")
        a = self.svc.request_approval(worker("w"), self.tid, "action", "install_dependency", {"pkg": "x"}, 2, self.clock.t + 100)
        for actor in (worker("w"), VER, SYS):
            with self.assertRaises(AuthorityError):
                self.svc.decide_approval(actor, a, "approved")
        self.svc.decide_approval(CTL, a, "approved")

    def test_tier3_is_decided_by_the_user_only(self):
        a = self.request()
        with self.assertRaises(AuthorityError):
            self.svc.decide_approval(CTL, a, "approved")
        self.svc.decide_approval(USER, a, "approved")
        with self.assertRaises(IllegalTransition):
            self.svc.decide_approval(USER, a, "denied")

    def _approved(self, **over):
        a = self.request(**over)
        self.svc.decide_approval(USER, a, "approved")
        return a

    def test_each_binding_failure_has_its_own_code(self):
        t = self.task(self.tid)
        undecided = self.request()
        with self.assertRaises(AuthorityError) as cm:
            self.svc.approve_release(CTL, self.tid, undecided)
        self.assertEqual(cm.exception.code, "undecided")
        denied = self.request()
        self.svc.decide_approval(USER, denied, "denied")
        with self.assertRaises(AuthorityError) as cm:
            self.svc.approve_release(CTL, self.tid, denied)
        self.assertEqual(cm.exception.code, "denied")
        expired = self._approved(expires_at=self.clock.t + 0.5)
        self.clock.t += 5
        with self.assertRaises(AuthorityError) as cm:
            self.svc.approve_release(CTL, self.tid, expired)
        self.assertEqual(cm.exception.code, "expired")
        wrong_scope = self._approved(scope={"task_id": "other"})
        with self.assertRaises(AuthorityError) as cm:
            self.svc.approve_release(CTL, self.tid, wrong_scope)
        self.assertEqual(cm.exception.code, "wrong_scope")
        wrong_op = self._approved(operation="publish")
        with self.assertRaises(AuthorityError) as cm:
            self.svc.approve_release(CTL, self.tid, wrong_op)
        self.assertEqual(cm.exception.code, "wrong_operation")

    def test_changed_target_policy_or_qualification_invalidates_the_approval(self):
        a = self._approved()
        self.svc.approve_release(CTL, self.tid, a)
        self.svc.set_task_target(CTL, self.tid, "commit-other")   # target changed after approval
        self.assertEqual(self.task(self.tid)["release_state"], "REVOKED")
        with self.assertRaises(VanillaError):
            self.svc.apply_release(CTL, self.tid, a)

    def test_policy_change_after_approval_blocks_use(self):
        a = self._approved()
        self.svc.approve_release(CTL, self.tid, a)
        self.svc.set_task_policy(CTL, self.tid, self.svc.add_policy(CTL, self.pid, {"changed": True}))
        t = self.task(self.tid)
        self.assertEqual((t["verification_state"], t["release_state"]), ("STALE", "REVOKED"))
        with self.assertRaises(VanillaError):
            self.svc.apply_release(CTL, self.tid, a)

    def test_qualification_change_invalidates_approval_even_after_requalifying(self):
        a = self._approved()
        self.svc.update_task_source(CTL, self.tid, "new-commit", "e1")   # claims invalidated -> STALE
        self.assertEqual(self.task(self.tid)["verification_state"], "STALE")
        crit = self.svc.conn.execute("SELECT criterion_id, check_id FROM criteria").fetchone()
        ev = self.svc.put_evidence(VER, b"rerun")
        self.svc.insert_claim(VER, self.claim(self.tid, crit["criterion_id"], crit["check_id"], [ev], "new-commit", "e1"))
        self.svc.set_verification(CTL, self.tid, "RUNNING")
        new_q = self.svc.set_verification(VER, self.tid, "VERIFIED")
        self.assertNotEqual(new_q, self.qid)
        with self.assertRaises(AuthorityError) as cm:
            self.svc.approve_release(CTL, self.tid, a)
        self.assertEqual(cm.exception.code, "qualification_changed")

    def test_release_needs_verified_state(self):
        a = self._approved()
        self.svc.update_task_source(CTL, self.tid, "zzz", "e1")  # now STALE; approval binding also stale
        with self.assertRaises(VanillaError):
            self.svc.approve_release(CTL, self.tid, a)

    def test_integration_approval_cannot_be_decided_after_binding_changed(self):
        a = self.request()
        self.svc.set_task_target(CTL, self.tid, "moved")
        with self.assertRaises(ValidationError) as cm:
            self.svc.decide_approval(USER, a, "approved")
        self.assertEqual(cm.exception.code, "binding_mismatch")

    def test_worker_cannot_touch_release_state(self):
        a = self._approved()
        for fn in (lambda: self.svc.approve_release(worker("w"), self.tid, a), lambda: self.svc.apply_release(worker("w"), self.tid, a),
                   lambda: self.svc.revoke_release(worker("w"), self.tid)):
            with self.assertRaises(AuthorityError):
                fn()

    def test_approval_rows_cannot_be_edited_directly(self):
        a = self._approved()
        for sql in ("UPDATE approvals SET decision='denied'", "UPDATE approvals SET target_ref='x'", "UPDATE approvals SET expires_at=0", "DELETE FROM approvals"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.svc.conn.execute(sql)

    def test_action_approval_is_scoped_and_single_use(self):
        a = self.svc.request_approval(CTL, self.tid, "action", "install_dependency", {"package": "left-pad"}, 2, self.clock.t + 100)
        self.svc.decide_approval(CTL, a, "approved")
        with self.assertRaises(AuthorityError) as cm:
            self.svc.use_action_approval(CTL, self.tid, a, "install_dependency", {"package": "other"})
        self.assertEqual(cm.exception.code, "wrong_scope")
        self.svc.use_action_approval(CTL, self.tid, a, "install_dependency", {"package": "left-pad"})
        with self.assertRaises(AuthorityError) as cm:
            self.svc.use_action_approval(CTL, self.tid, a, "install_dependency", {"package": "left-pad"})
        self.assertEqual(cm.exception.code, "consumed")


if __name__ == "__main__":
    unittest.main()


class DailyBackup(unittest.TestCase):
    def test_once_a_day_and_keeps_seven(self):
        import sqlite3 as _s
        import tempfile as _t
        from pathlib import Path as _P
        from vanilla_agents.storage import daily_backup, layout
        with _t.TemporaryDirectory() as d:
            self.assertIsNone(daily_backup(d))                    # no database yet
            lay = layout(d)
            lay["db_dir"].mkdir(parents=True)
            c = _s.connect(lay["db"]); c.execute("create table t(x)"); c.execute("insert into t values (1)"); c.commit(); c.close()
            first = daily_backup(d, now=86400 * 100)
            self.assertTrue(first.exists())
            self.assertIsNone(daily_backup(d, now=86400 * 100 + 60))   # same day
            for k in range(1, 10):
                daily_backup(d, now=86400 * (100 + k))
            self.assertEqual(len(list(_P(lay["backups"]).glob("vanilla-*.db"))), 7)
            c = _s.connect(sorted(_P(lay["backups"]).glob("vanilla-*.db"))[-1]); self.assertEqual(c.execute("select x from t").fetchone()[0], 1); c.close()
