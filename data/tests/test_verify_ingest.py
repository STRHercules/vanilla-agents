"""Stage 2 controller ingestion: a verifier bundle becomes claims in ONE transaction; anything wrong writes nothing."""
import copy
import os
import pathlib

from vanilla_agents.contracts import AuthorityError, IllegalTransition, ValidationError
from vanilla_agents.verify.client import VerifierFailed, run_verifier
from vanilla_agents.verify.manifest import require_manifest
from vanilla_agents.verify.verifier import run_verification
from tests import vhelpers as h
from tests.helpers import CTL, USER, VER, Base, worker

FILES = {"app.py": h.APP, "tests/__init__.py": "", "tests/test_app.py": h.GOOD_TEST,
         "tools/flaky.py": "import os\nn = int(open('.count').read()) + 1 if os.path.exists('.count') else 1\nopen('.count', 'w').write(str(n))\n"
                           "print('Ran 1 test')\nraise SystemExit(1 if n == 1 else 0)\n"}


class IngestCase(Base):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        self.base = h.make_repo(self.repo, FILES)
        self.pid, self.tid = self.project_task()
        self.crit = self.svc.add_criterion(CTL, self.tid, "unit tests pass", True, "unit")

    def trusted(self, checks):
        self.trusted_dir = h.make_trusted(os.path.join(self.tmp, "trusted"), checks)
        self.expected = require_manifest(self.trusted_dir)

    def make_bundle(self, checks=None, patch=None, run_id="run_i1"):
        self.trusted(checks or [h.UNIT_CHECK])
        req = h.request(self.tmp, self.repo, self.base, self.trusted_dir, [{"criterion_id": self.crit, "check_id": "unit", "required": True}], patch, run_id)
        req["task_id"] = self.tid
        return run_verification(req)

    def start(self, bundle):
        """What the controller does before ingesting: record the task source the verifier reported, then move to RUNNING."""
        self.svc.update_task_source(CTL, self.tid, bundle["source_commit"], bundle["env_fingerprint"])
        self.svc.set_verification(CTL, self.tid, "RUNNING")

    def ingest(self, bundle, blobs, expected=None, actor=CTL):
        return self.svc.ingest_verification(actor, bundle, blobs, expected or self.expected)

    def counts(self):
        c = self.svc.conn
        return tuple(c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("claims", "verification_runs", "evidence", "qualifications"))


class IngestHappyPathTests(IngestCase):
    def test_good_bundle_verifies_the_task_with_one_event(self):
        b, blobs = self.make_bundle()
        self.start(b)
        n_events = len(self.events())
        res = self.ingest(b, blobs)
        t = self.task(self.tid)
        self.assertEqual((res["verification_state"], t["verification_state"]), ("VERIFIED", "VERIFIED"))
        self.assertEqual(t["qualification_id"], res["qualification_id"])
        self.assertEqual(len(self.events()), n_events + 1)
        self.assertEqual(self.events()[-1]["kind"], "verification_ingested")
        claim = self.svc.conn.execute("SELECT * FROM claims").fetchone()
        self.assertEqual((claim["status"], claim["method"], claim["verifier_id"], claim["run_id"]), ("VERIFIED", "deterministic", "vanilla-verifier", "run_i1"))
        self.assertEqual(len(claim["bundle_hash"]), 64)
        run = self.svc.conn.execute("SELECT * FROM verification_runs").fetchone()
        self.assertEqual((run["outcome"], run["trusted_manifest_hash"], run["bundle_hash"]), ("VERIFIED", self.expected, claim["bundle_hash"]))
        self.assertEqual(self.svc.evidence_scan()["tampered"], [])
        self.assertTrue(all(self.svc.evidence.read(h_) is not None for h_ in b["checks"][0]["evidence"]))

    def test_failed_bundle_marks_the_task_failed_and_keeps_the_failing_claim(self):
        patch = h.make_patch(self.repo, {"app.py": h.BROKEN_APP})
        b, blobs = self.make_bundle(patch=patch)
        self.start(b)
        res = self.ingest(b, blobs)
        self.assertEqual(res["verification_state"], "FAILED")
        self.assertIsNone(self.task(self.tid)["qualification_id"])
        self.assertEqual(self.svc.conn.execute("SELECT status FROM claims").fetchone()[0], "FAILED")

    def test_flaky_bundle_marks_the_task_flaky(self):
        b, blobs = self.make_bundle(checks=[{**h.UNIT_CHECK, "argv": ["{python}", "tools/flaky.py"], "rule": {"type": "min_tests_discovered", "pattern": r"Ran (\d+) tests?", "n": 1}}])
        self.start(b)
        self.assertEqual(self.ingest(b, blobs)["verification_state"], "FLAKY")

    def test_blocking_diff_finding_fails_the_task_even_when_checks_pass(self):
        weakened = h.GOOD_TEST.replace("        self.assertEqual(add(0, 0), 0)\n", "        pass\n")
        b, blobs = self.make_bundle(patch=h.make_patch(self.repo, {"tests/test_app.py": weakened}))
        self.assertEqual(b["checks"][0]["status"], "VERIFIED")
        self.start(b)
        res = self.ingest(b, blobs)
        self.assertEqual(res["verification_state"], "FAILED")
        self.assertIsNone(res["qualification_id"])

    def test_not_verified_criterion_leaves_the_task_inconclusive(self):
        self.svc.add_criterion(CTL, self.tid, "needs a check nobody defined", True, "undefined_check")
        b, blobs = self.make_bundle()
        self.start(b)
        res = self.ingest(b, blobs)
        self.assertEqual(res["verification_state"], "INCONCLUSIVE")

    def test_subprocess_end_to_end(self):
        self.trusted([h.UNIT_CHECK])
        req = h.request(self.tmp, self.repo, self.base, self.trusted_dir, [{"criterion_id": self.crit, "check_id": "unit", "required": True}], None, "run_sub")
        req["task_id"] = self.tid
        b, blobs = run_verifier(req)
        self.start(b)
        self.assertEqual(self.ingest(b, blobs)["verification_state"], "VERIFIED")

    def test_later_source_change_makes_the_ingested_claims_stale(self):
        b, blobs = self.make_bundle()
        self.start(b)
        self.ingest(b, blobs)
        self.svc.update_task_source(CTL, self.tid, "a-newer-commit", b["env_fingerprint"])
        self.assertEqual(self.task(self.tid)["verification_state"], "STALE")

    def test_evidence_altered_after_ingestion_is_caught_by_revalidation(self):
        b, blobs = self.make_bundle()
        self.start(b)
        self.ingest(b, blobs)
        ev = b["checks"][0]["evidence"][1]
        pathlib.Path(self.svc.evidence.path_for(ev)).write_bytes(b"tampered after verification")
        self.assertEqual(len(self.svc.revalidate_evidence(CTL, self.tid)), 1)
        self.assertEqual(self.task(self.tid)["verification_state"], "STALE")

    def test_migration_two_columns_exist(self):
        cols = {r[1] for r in self.svc.conn.execute("PRAGMA table_info(claims)")}
        runs = {r[1] for r in self.svc.conn.execute("PRAGMA table_info(verification_runs)")}
        self.assertTrue({"method", "bundle_hash"} <= cols)
        self.assertTrue({"patch_hash", "env_fingerprint", "outcome", "bundle_hash"} <= runs)


class IngestRejectionTests(IngestCase):
    def assertWritesNothing(self, fn, exc, code=None):
        before = self.counts()
        with self.assertRaises(exc) as cm:
            fn()
        if code:
            self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.task(self.tid)["verification_state"], "RUNNING")

    def test_incomplete_bundle_promotes_nothing(self):
        b, blobs = self.make_bundle()
        self.start(b)
        cut = copy.deepcopy(b)
        cut["complete"] = False
        self.assertWritesNothing(lambda: self.ingest(cut, blobs), ValidationError, "bundle_incomplete")
        cut2 = copy.deepcopy(b)
        del cut2["checks"]
        self.assertWritesNothing(lambda: self.ingest(cut2, blobs), ValidationError)

    def test_bundle_from_another_manifest_is_rejected(self):
        b, blobs = self.make_bundle()
        self.start(b)
        self.assertWritesNothing(lambda: self.ingest(b, blobs, expected="f" * 64), ValidationError, "manifest_mismatch")

    def test_tampered_or_missing_blobs_are_rejected(self):
        b, blobs = self.make_bundle()
        self.start(b)
        bad = dict(blobs)
        first = sorted(bad)[0]
        bad[first] = bad[first] + b"x"
        self.assertWritesNothing(lambda: self.ingest(b, bad), ValidationError, "blob_mismatch")
        short = {k: v for k, v in blobs.items() if k != sorted(blobs)[0]}
        self.assertWritesNothing(lambda: self.ingest(b, short), ValidationError, "missing_blob")

    def test_same_run_cannot_be_ingested_twice(self):
        b, blobs = self.make_bundle()
        self.start(b)
        self.ingest(b, blobs)
        self.svc.set_verification(CTL, self.tid, "STALE")
        self.svc.set_verification(CTL, self.tid, "RUNNING")
        before = self.counts()
        with self.assertRaises(ValidationError) as cm:
            self.ingest(b, blobs)
        self.assertEqual(cm.exception.code, "run_exists")
        self.assertEqual(self.counts(), before)

    def test_task_must_be_running(self):
        b, blobs = self.make_bundle()
        self.svc.update_task_source(CTL, self.tid, b["source_commit"], b["env_fingerprint"])    # NOT moved to RUNNING
        before = self.counts()
        with self.assertRaises(IllegalTransition) as cm:
            self.ingest(b, blobs)
        self.assertEqual(cm.exception.code, "not_running")
        self.assertEqual(self.counts(), before)

    def test_a_bundle_for_a_different_source_rolls_everything_back(self):
        b, blobs = self.make_bundle()
        self.svc.update_task_source(CTL, self.tid, "some-other-commit", b["env_fingerprint"])
        self.svc.set_verification(CTL, self.tid, "RUNNING")
        self.assertWritesNothing(lambda: self.ingest(b, blobs), ValidationError, "claim_source_mismatch")

    def test_only_the_controller_may_ingest(self):
        b, blobs = self.make_bundle()
        self.start(b)
        for actor in (worker("w"), VER, USER):
            before = len(self.events("rejected"))
            self.assertWritesNothing(lambda: self.ingest(b, blobs, actor=actor), AuthorityError)
            self.assertEqual(len(self.events("rejected")), before + 1)

    def test_verifier_failure_means_nothing_to_ingest(self):
        self.trusted([h.UNIT_CHECK])
        pathlib.Path(self.trusted_dir, "checks.json").write_text("[]")          # manifest no longer matches
        req = h.request(self.tmp, self.repo, self.base, self.trusted_dir, [{"criterion_id": self.crit, "check_id": "unit", "required": True}], None, "run_bad")
        req["task_id"] = self.tid
        self.svc.set_verification(CTL, self.tid, "RUNNING")
        before = self.counts()
        with self.assertRaises(VerifierFailed):
            run_verifier(req)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.task(self.tid)["verification_state"], "RUNNING")


if __name__ == "__main__":
    import unittest
    unittest.main()
