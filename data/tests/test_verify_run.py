"""Stage 2 integration: the real verifier against fixture repositories (temp folders, no network, no AI)."""
import os
import pathlib
import shutil
import subprocess
import unittest
from unittest import mock

from vanilla_agents.verify.client import VerifierFailed, run_verifier
from vanilla_agents.verify.manifest import ManifestMismatch
from vanilla_agents.verify.verifier import run_verification
from tests import vhelpers as h
from tests.helpers import rmtree_force

CRIT = [{"criterion_id": "crit_1", "check_id": "unit", "required": True}]
BASE_FILES = {"app.py": h.APP, "tests/__init__.py": "", "tests/test_app.py": h.GOOD_TEST}


def script_check(cid, script, rule=None, **extra):
    return {"check_id": cid, "argv": ["{python}", "tools/%s" % script], "cwd": ".", "timeout": 30, "env": [], "repeatability": "repeatable",
            "rule": rule or {"type": "min_tests_discovered", "pattern": r"Ran (\d+) tests?", "n": 1}, **extra}


class VerifierCase(unittest.TestCase):
    files = BASE_FILES

    def setUp(self):
        self.tmp = h.new_tmp()
        self.addCleanup(rmtree_force, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        self.base = h.make_repo(self.repo, self.files)

    def verify(self, checks, criteria=CRIT, patch=None, run_id="run_t1"):
        trusted = h.make_trusted(os.path.join(self.tmp, "trusted"), checks)
        self.trusted = trusted
        return run_verification(h.request(self.tmp, self.repo, self.base, trusted, criteria, patch, run_id))

    def blob_text(self, blobs, bundle, check_id, index=-1):
        r = next(c for c in bundle["checks"] if c["check_id"] == check_id)
        return blobs[r["evidence"][index]].decode()

    def status(self, bundle, check_id="unit"):
        return next(c["status"] for c in bundle["checks"] if c["check_id"] == check_id)


class BasicVerdictTests(VerifierCase):
    def test_passing_project_is_verified_with_evidence(self):
        b, blobs = self.verify([h.UNIT_CHECK])
        self.assertEqual((b["outcome"], self.status(b), b["complete"]), ("VERIFIED", "VERIFIED", True))
        self.assertEqual(len(b["checks"][0]["evidence"]), 2)
        self.assertTrue(all(len(x) == 64 for x in blobs))
        self.assertIn("Ran 2 tests", self.blob_text(blobs, b, "unit"))
        self.assertEqual(b["not_verified"], [])

    def test_failing_change_is_failed_after_a_retry(self):
        patch = h.make_patch(self.repo, {"app.py": h.BROKEN_APP})
        b, _ = self.verify([h.UNIT_CHECK], patch=patch)
        self.assertEqual((b["outcome"], self.status(b)), ("FAILED", "FAILED"))
        self.assertEqual([a["status"] for a in b["checks"][0]["attempts"]], ["FAILED", "FAILED"])
        self.assertEqual(b["not_verified"][0]["criterion_id"], "crit_1")

    def test_exit_zero_with_no_output_is_inconclusive_not_a_pass(self):
        files = {**BASE_FILES, "tools/silent.py": "import sys\nsys.exit(0)\n"}
        self.setUp_files(files)
        b, _ = self.verify([script_check("unit", "silent.py")])
        self.assertEqual((self.status(b), b["outcome"]), ("INCONCLUSIVE", "INCONCLUSIVE"))
        self.assertIn("no test summary", b["checks"][0]["observed"])

    def test_zero_tests_discovered_is_inconclusive(self):
        self.setUp_files({"app.py": h.APP, "tests/__init__.py": ""})
        b, _ = self.verify([h.UNIT_CHECK])
        self.assertEqual(self.status(b), "INCONCLUSIVE")
        self.assertIn("only 0 test(s)", b["checks"][0]["observed"])

    def test_all_tests_skipped_is_inconclusive(self):
        skipped = "import unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skip('later')\n    def test_a(self):\n        self.assertTrue(True)\n"
        self.setUp_files({"app.py": h.APP, "tests/__init__.py": "", "tests/test_a.py": skipped})
        b, _ = self.verify([h.UNIT_CHECK])
        self.assertEqual(self.status(b), "INCONCLUSIVE")
        self.assertIn("skipped", b["checks"][0]["observed"])

    def setUp_files(self, files):
        shutil.rmtree(self.repo, ignore_errors=True)
        self.base = h.make_repo(self.repo, files)

    def test_criterion_without_a_check_definition_is_not_verified(self):
        crit = CRIT + [{"criterion_id": "crit_2", "check_id": "no_such_check", "required": True}]
        b, _ = self.verify([h.UNIT_CHECK], criteria=crit)
        self.assertEqual([c["check_id"] for c in b["checks"]], ["unit"])
        self.assertEqual(b["not_verified"][0]["check_id"], "no_such_check")
        self.assertEqual(b["outcome"], "NOT_VERIFIED")

    def test_optional_criterion_does_not_block(self):
        crit = CRIT + [{"criterion_id": "crit_2", "check_id": "optional", "required": False}]
        b, _ = self.verify([h.UNIT_CHECK], criteria=crit)
        self.assertEqual(b["outcome"], "VERIFIED")
        self.assertEqual(len(b["not_verified"]), 1)


class FlakyAndEnvironmentTests(VerifierCase):
    files = {**BASE_FILES,
             "tools/flaky.py": "import os\nn = int(open('.count').read()) + 1 if os.path.exists('.count') else 1\nopen('.count', 'w').write(str(n))\n"
                               "print('Ran 1 test')\nraise SystemExit(1 if n == 1 else 0)\n",
             "tools/envfail.py": "print('error: Access is denied. (os error 5)')\nraise SystemExit(1)\n"}

    def test_fail_then_pass_is_flaky_after_three_more_runs(self):
        b, _ = self.verify([script_check("unit", "flaky.py")])
        att = [a["status"] for a in b["checks"][0]["attempts"]]
        self.assertEqual(att, ["FAILED", "VERIFIED", "VERIFIED", "VERIFIED", "VERIFIED"])
        self.assertEqual((self.status(b), b["outcome"]), ("FLAKY", "FLAKY"))

    def test_non_repeatable_checks_are_not_retried(self):
        c = script_check("unit", "flaky.py", repeatability="non_repeatable")
        b, _ = self.verify([c])
        self.assertEqual(len(b["checks"][0]["attempts"]), 1)
        self.assertEqual(self.status(b), "FAILED")

    def test_environment_failure_is_blocked_with_bounded_retries(self):
        b, _ = self.verify([script_check("unit", "envfail.py")])
        self.assertEqual(self.status(b), "BLOCKED")
        self.assertEqual(len(b["checks"][0]["attempts"]), 3)             # first run plus two degraded retries, never more
        self.assertEqual(b["outcome"], "BLOCKED")

    def test_degraded_argv_is_used_for_environment_retries(self):
        c = script_check("unit", "envfail.py", degraded_argv=["{python}", "-c", "print('Ran 3 tests')"])
        b, _ = self.verify([c])
        self.assertEqual([a["status"] for a in b["checks"][0]["attempts"]], ["BLOCKED", "VERIFIED"])
        self.assertEqual(self.status(b), "VERIFIED")


class AdversarialPatchTests(VerifierCase):
    def test_editing_the_tests_cannot_make_broken_code_pass(self):
        rigged = h.GOOD_TEST.replace("add(1, 2), 3", "add(1, 2), -1").replace("add(0, 0), 0", "add(0, 0), 0")
        patch = h.make_patch(self.repo, {"app.py": h.BROKEN_APP, "tests/test_app.py": rigged})
        b, _ = self.verify([h.UNIT_CHECK], patch=patch)
        self.assertEqual(self.status(b), "FAILED")                        # the TRUSTED tests ran, not the edited ones
        self.assertEqual(b["outcome"], "FAILED")
        kinds = {f["kind"] for f in b["diff_findings"]}
        self.assertIn("protected_path_changed", kinds)

    def test_deleting_an_assertion_blocks_verification_even_if_checks_pass(self):
        weakened = h.GOOD_TEST.replace("        self.assertEqual(add(0, 0), 0)\n", "        pass\n")
        patch = h.make_patch(self.repo, {"tests/test_app.py": weakened})
        b, _ = self.verify([h.UNIT_CHECK], patch=patch)
        self.assertEqual(self.status(b), "VERIFIED")                      # trusted tests still pass...
        self.assertTrue(any(f["kind"] == "assertions_removed" and f["blocking"] for f in b["diff_findings"]))
        self.assertEqual(b["outcome"], "FAILED")                          # ...but the weakening itself stops promotion

    def test_new_protected_test_files_are_proposed_not_run(self):
        patch = h.make_patch(self.repo, {"tests/test_new.py": "import unittest\n\n\nclass N(unittest.TestCase):\n    def test_n(self):\n        self.assertTrue(True)\n"})
        b, _ = self.verify([h.UNIT_CHECK], patch=patch)
        self.assertIn("2 test(s)", b["checks"][0]["observed"])            # the worker's added test was not part of the verified tree
        self.assertTrue(any(f["kind"] == "protected_path_changed" for f in b["diff_findings"]))
        self.assertEqual(b["outcome"], "VERIFIED")

    def test_path_traversal_patch_is_rejected_and_writes_nothing_outside(self):
        evil = b"diff --git a/../escape.txt b/../escape.txt\nnew file mode 100644\n--- /dev/null\n+++ b/../escape.txt\n@@ -0,0 +1 @@\n+pwned\n"
        b, _ = self.verify([h.UNIT_CHECK], patch=evil)
        self.assertEqual((b["outcome"], b["checks"]), ("FAILED", []))
        self.assertEqual(b["diff_findings"][0]["kind"], "patch_rejected")
        self.assertTrue(b["diff_findings"][0]["blocking"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "verify", "escape.txt")))
        self.assertEqual(b["not_verified"][0]["reason"], "checkout or patch rejected")

    def test_symlink_and_binary_patches_are_rejected(self):
        link = b"diff --git a/ln b/ln\nnew file mode 120000\n--- /dev/null\n+++ b/ln\n@@ -0,0 +1 @@\n+/etc\n"
        b, _ = self.verify([h.UNIT_CHECK], patch=link)
        self.assertEqual(b["diff_findings"][0]["kind"], "patch_rejected")
        binp = b"diff --git a/x.bin b/x.bin\nnew file mode 100644\nGIT binary patch\nliteral 2\n"
        b2, _ = self.verify([h.UNIT_CHECK], patch=binp, run_id="run_bin")
        self.assertEqual(b2["diff_findings"][0]["kind"], "patch_rejected")

    def test_patch_that_does_not_apply_is_rejected(self):
        bad = h.make_patch(self.repo, {"app.py": h.APP + "# note\n"}).replace(b"return a + b", b"return a * b")
        b, _ = self.verify([h.UNIT_CHECK], patch=bad)
        self.assertEqual(b["diff_findings"][0]["kind"], "patch_rejected")
        self.assertEqual(b["outcome"], "FAILED")

    def test_refuses_to_reuse_a_checkout_folder(self):
        self.verify([h.UNIT_CHECK], run_id="run_same")
        b, _ = run_verification(h.request(self.tmp, self.repo, self.base, self.trusted, CRIT, None, "run_same"))
        self.assertEqual(b["outcome"], "FAILED")                          # a finished run's folder is never silently reused
        self.assertEqual(b["diff_findings"][0]["kind"], "checkout_failed")


class ManifestEnforcementTests(VerifierCase):
    def test_tampered_check_definitions_refuse_to_run(self):
        self.verify([h.UNIT_CHECK])
        pathlib.Path(self.trusted, "checks.json").write_text("[]")
        with self.assertRaises(ManifestMismatch):
            run_verification(h.request(self.tmp, self.repo, self.base, self.trusted, CRIT, None, "run_t2"))

    def test_tampered_protected_list_refuses_to_run(self):
        self.verify([h.UNIT_CHECK])
        pathlib.Path(self.trusted, "protected.json").write_text("[]")
        with self.assertRaises(ManifestMismatch):
            run_verification(h.request(self.tmp, self.repo, self.base, self.trusted, CRIT, None, "run_t3"))

    def test_nothing_is_created_when_the_manifest_fails(self):
        self.verify([h.UNIT_CHECK])
        pathlib.Path(self.trusted, "checks.json").write_text("[]")
        try:
            run_verification(h.request(self.tmp, self.repo, self.base, self.trusted, CRIT, None, "run_t4"))
        except ManifestMismatch:
            pass
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "verify", "run_t4")))


class ProcessAndOutputTests(VerifierCase):
    files = {**BASE_FILES,
             "tools/tree.py": "import subprocess, sys, time\nc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                              "open('child.pid', 'w').write(str(c.pid))\nprint('child started', flush=True)\ntime.sleep(60)\n",
             "tools/secret.py": "print('password=hunter2 and token=abc123def')\nprint('Ran 1 test')\n",
             "tools/big.py": "print('x' * 400000)\nprint('Ran 1 test')\n",
             "tools/env.py": "import os\nprint(sorted(os.environ))\nprint('Ran 1 test')\n"}

    @staticmethod
    def alive(pid):
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
            return str(pid) in out
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def test_timeout_kills_the_whole_process_tree(self):
        c = script_check("unit", "tree.py", timeout=3)
        b, _ = self.verify([c])
        self.assertEqual(self.status(b), "BLOCKED")
        self.assertTrue(b["checks"][0]["attempts"][0]["timed_out"])
        pid = int(pathlib.Path(self.tmp, "verify", "run_t1", "work", "child.pid").read_text())
        for _ in range(20):
            if not self.alive(pid):
                break
            import time
            time.sleep(0.25)
        self.assertFalse(self.alive(pid), "the grandchild process survived the timeout")

    def test_secrets_are_redacted_before_storage(self):
        b, blobs = self.verify([script_check("unit", "secret.py")])
        text = self.blob_text(blobs, b, "unit")
        self.assertNotIn("hunter2", text)
        self.assertNotIn("abc123def", text)
        self.assertIn("password=[REDACTED]", text)
        self.assertTrue(all(b"hunter2" not in d for d in blobs.values()))

    def test_huge_output_is_capped_head_and_tail(self):
        b, blobs = self.verify([script_check("unit", "big.py")])
        text = self.blob_text(blobs, b, "unit")
        self.assertLess(len(text), 140000)
        self.assertIn("bytes omitted", text)
        self.assertIn("Ran 1 test", text)                                 # the tail (where the summary lives) is kept
        self.assertEqual(self.status(b), "VERIFIED")

    def test_environment_is_an_allowlist(self):
        with mock.patch.dict(os.environ, {"VANILLA_TEST_SECRET": "s3cr3t", "GITHUB_TOKEN": "ghp_x", "USERPROFILE": "C:\\Users\\someone"}):
            b, blobs = self.verify([script_check("unit", "env.py")])
        text = self.blob_text(blobs, b, "unit")
        for name in ("VANILLA_TEST_SECRET", "GITHUB_TOKEN", "USERPROFILE", "APPDATA"):
            self.assertNotIn(name, text)
        self.assertIn("PYTHONDONTWRITEBYTECODE", text)

    def test_unstartable_check_is_blocked(self):
        c = {"check_id": "unit", "argv": ["definitely-not-a-real-program-xyz"], "cwd": ".", "timeout": 10, "env": [],
             "rule": {"type": "silent_allowed"}}
        b, _ = self.verify([c])
        self.assertEqual(self.status(b), "BLOCKED")


class ClientTests(VerifierCase):
    def test_subprocess_round_trip_returns_a_valid_bundle(self):
        trusted = h.make_trusted(os.path.join(self.tmp, "trusted"), [h.UNIT_CHECK])
        b, blobs = run_verifier(h.request(self.tmp, self.repo, self.base, trusted, CRIT))
        self.assertEqual(b["outcome"], "VERIFIED")
        from vanilla_agents.verify.bundle import validate_bundle
        self.assertTrue(validate_bundle(b))
        self.assertTrue(set(b["checks"][0]["evidence"]) <= set(blobs))

    def test_refused_run_produces_no_bundle(self):
        trusted = h.make_trusted(os.path.join(self.tmp, "trusted"), [h.UNIT_CHECK])
        pathlib.Path(trusted, "checks.json").write_text("[]")
        with self.assertRaises(VerifierFailed) as cm:
            run_verifier(h.request(self.tmp, self.repo, self.base, trusted, CRIT))
        self.assertIn("manifest_mismatch", str(cm.exception))

    def test_crashing_verifier_produces_no_bundle(self):
        req = h.request(self.tmp, self.repo, self.base, os.path.join(self.tmp, "does-not-exist"), CRIT)
        with self.assertRaises(VerifierFailed):
            run_verifier(req)


if __name__ == "__main__":
    unittest.main()


class HoldoutTests(VerifierCase):
    SECRET = "SECRET_HOLDOUT_TOKEN_xyz"

    def run_holdout(self, script_body, holdouts=True):
        trusted = h.make_trusted(os.path.join(self.tmp, "trusted"), [h.UNIT_CHECK])
        hd = {"check_id": "my_" + self.SECRET, "argv": ["{python}", "-c", script_body], "cwd": ".", "timeout": 30, "env": [], "repeatability": "repeatable",
              "rule": {"type": "output_contains", "pattern": "PASSMARK"}}
        import json
        from vanilla_agents.verify.manifest import write_manifest
        with open(os.path.join(trusted, "holdout.json"), "w", encoding="utf-8") as f:
            json.dump([hd], f)
        write_manifest(trusted, ["checks.json", "protected.json", "holdout.json"])
        req = h.request(self.tmp, self.repo, self.base, trusted, CRIT, None, "run_hold")
        if not holdouts:
            req["holdouts"] = False
        return run_verification(req)

    def test_holdout_runs_and_leaks_nothing(self):
        import json
        b, blobs = self.run_holdout("print('PASSMARK %s')" % self.SECRET)
        self.assertEqual(b["outcome"], "VERIFIED")
        hid = next(c for c in b["checks"] if c["check_id"] == "hidden-1")
        self.assertEqual(hid["status"], "VERIFIED")
        everything = json.dumps(b) + b"".join(blobs.values()).decode("utf-8", "replace")
        for needle in (self.SECRET, "PASSMARK", "my_", "-c"):
            self.assertNotIn(needle, everything)

    def test_failing_holdout_fails_run_without_detail(self):
        b, blobs = self.run_holdout("print('nope %s')" % self.SECRET)
        self.assertNotEqual(b["outcome"], "VERIFIED")
        self.assertIn("hidden check", b["not_verified"][0]["reason"])
        import json
        self.assertNotIn(self.SECRET, json.dumps(b) + b"".join(blobs.values()).decode("utf-8", "replace"))

    def test_fast_route_request_skips_holdouts_only_when_told(self):    # D-304
        b, _ = self.run_holdout("print('nope')", holdouts=False)
        self.assertEqual((b["outcome"], [c["check_id"] for c in b["checks"] if c["check_id"].startswith("hidden-")]), ("VERIFIED", []))

    def test_holdouts_run_by_default(self):
        b, _ = self.run_holdout("print('nope')")
        self.assertIn("hidden-1", [c["check_id"] for c in b["checks"]])
