"""Stage 2 pure logic: manifest, patch validation, diff integrity, redaction, verdict rules and the bundle schema."""
import json
import os
import pathlib
import shutil
import tempfile
import unittest

from vanilla_agents.contracts import ValidationError
from vanilla_agents.verify import bundle as B
from vanilla_agents.verify import diffint, manifest, patchcheck, redact, rules


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="va_m_")
        self.addCleanup(shutil.rmtree, self.d, True)
        for n, c in (("checks.json", "[]"), ("protected.json", "[]")):
            pathlib.Path(os.path.join(self.d, n)).write_text(c)
        self.m = manifest.write_manifest(self.d, ["checks.json", "protected.json"])

    def test_untouched_files_pass_and_hash_is_stable(self):
        h1 = manifest.require_manifest(self.d)
        self.assertEqual(h1, manifest.require_manifest(self.d))
        self.assertEqual(len(h1), 64)

    def test_changed_trusted_file_refuses(self):
        pathlib.Path(os.path.join(self.d, "checks.json")).write_text('[{"check_id":"x"}]')
        with self.assertRaises(manifest.ManifestMismatch) as cm:
            manifest.require_manifest(self.d)
        self.assertIn("checks.json changed", str(cm.exception))

    def test_missing_trusted_file_refuses(self):
        os.remove(os.path.join(self.d, "protected.json"))
        with self.assertRaises(manifest.ManifestMismatch):
            manifest.require_manifest(self.d)

    def test_changed_verifier_code_refuses(self):
        m = json.loads(pathlib.Path(os.path.join(self.d, manifest.MANIFEST_NAME)).read_text())
        m["files"]["code:rules.py"] = "0" * 64                      # as if rules.py had been edited
        pathlib.Path(os.path.join(self.d, manifest.MANIFEST_NAME)).write_text(json.dumps(m))
        with self.assertRaises(manifest.ManifestMismatch) as cm:
            manifest.require_manifest(self.d)
        self.assertIn("code:rules.py changed", str(cm.exception))

    def test_missing_manifest_and_unknown_entries_refuse(self):
        os.remove(os.path.join(self.d, manifest.MANIFEST_NAME))
        with self.assertRaises(manifest.ManifestMismatch):
            manifest.require_manifest(self.d)
        pathlib.Path(os.path.join(self.d, manifest.MANIFEST_NAME)).write_text(json.dumps({"version": 1, "files": {"weird:thing": "0" * 64}}))
        with self.assertRaises(manifest.ManifestMismatch):
            manifest.require_manifest(self.d)

    def test_manifest_hash_changes_with_content(self):
        before = manifest.manifest_hash(self.m)
        pathlib.Path(os.path.join(self.d, "checks.json")).write_text("[1]")
        self.assertNotEqual(before, manifest.manifest_hash(manifest.build_manifest(self.d, ["checks.json", "protected.json"])))


def diff_for(path, added=(), removed=(), status="modified", mode=None):
    lines = ["diff --git a/%s b/%s" % (path, path)]
    if status == "added":
        lines += ["new file mode 100644", "--- /dev/null", "+++ b/%s" % path]
    elif status == "deleted":
        lines += ["deleted file mode 100644", "--- a/%s" % path, "+++ /dev/null"]
    else:
        lines += ["--- a/%s" % path, "+++ b/%s" % path]
    lines += ["@@ -1,%d +1,%d @@" % (len(removed), len(added))] + ["-" + r for r in removed] + ["+" + a for a in added]
    return ("\n".join(lines) + "\n").encode()


class PatchTests(unittest.TestCase):
    def test_parse_simple_modify_add_delete(self):
        data = diff_for("src/a.py", ["x = 2"], ["x = 1"]) + diff_for("src/new.py", ["n"], status="added") + diff_for("old.txt", [], ["bye"], status="deleted")
        ch = patchcheck.validate_patch(patchcheck.parse_patch(data))
        self.assertEqual([(c.path, c.status) for c in ch], [("src/a.py", "modified"), ("src/new.py", "added"), ("old.txt", "deleted")])
        self.assertEqual(ch[0].added, ["x = 2"])
        self.assertEqual(ch[0].removed, ["x = 1"])

    def test_unsafe_paths_are_rejected(self):
        for bad in ("../outside.txt", "a/../../b", "/etc/passwd", "C:/Windows/x", "src\\evil.py", ".git/config", ".github/../.git/hooks/x", "a//b", "a/./b", "file.txt:stream", ""):
            with self.assertRaises(patchcheck.PatchRejected, msg=bad):
                patchcheck.validate_patch(patchcheck.parse_patch(diff_for(bad, ["x"], ["y"])) if bad else [patchcheck.FileChange(path="")])

    def test_dot_github_is_allowed_but_dot_git_is_not(self):
        patchcheck.validate_patch(patchcheck.parse_patch(diff_for(".github/workflows/ci.yml", ["x"], ["y"])) if False else [patchcheck.FileChange(path="docs/a.md")])
        self.assertFalse(patchcheck.safe_path(".git/config"))
        self.assertFalse(patchcheck.safe_path(".GIT/config"))
        self.assertFalse(patchcheck.safe_path(".gitmodules"))
        for ok in (".gitignore", ".gitattributes", ".github/workflows/ci.yml"):      # real repos contain these; the first real run failed on .gitignore
            self.assertTrue(patchcheck.safe_path(ok), ok)

    def test_symlinks_binary_and_non_utf8_are_rejected(self):
        with self.assertRaises(patchcheck.PatchRejected):
            patchcheck.validate_patch([patchcheck.FileChange(path="l", mode="120000")])
        with self.assertRaises(patchcheck.PatchRejected):
            patchcheck.validate_patch(patchcheck.parse_patch(b"diff --git a/x.bin b/x.bin\nGIT binary patch\nliteral 3\n"))
        with self.assertRaises(patchcheck.PatchRejected):
            patchcheck.parse_patch(b"\xff\xfe\x00bad")

    def test_size_and_file_count_limits(self):
        with self.assertRaises(patchcheck.PatchRejected):
            patchcheck.parse_patch(b"x" * (patchcheck.MAX_PATCH_BYTES + 1))
        many = [patchcheck.FileChange(path="f%d.txt" % i) for i in range(patchcheck.MAX_FILES + 1)]
        with self.assertRaises(patchcheck.PatchRejected):
            patchcheck.validate_patch(many)

    def test_rename_is_parsed(self):
        data = b"diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
        ch = patchcheck.parse_patch(data)
        self.assertEqual((ch[0].status, ch[0].old_path, ch[0].path), ("renamed", "old.py", "new.py"))


def changes(*items):
    return patchcheck.parse_patch(b"".join(items))


class DiffIntegrityTests(unittest.TestCase):
    PROT = ["tests/**", "scripts/*.ps1", "Cargo.toml"]

    def kinds(self, *items):
        return {f["kind"] for f in diffint.analyze(changes(*items), self.PROT)}

    def test_glob_matching(self):
        self.assertTrue(diffint.matches("tests/a/b.py", ["tests/**"]))
        self.assertTrue(diffint.matches("tests", ["tests/**"]))
        self.assertTrue(diffint.matches("scripts/check.ps1", ["scripts/*.ps1"]))
        self.assertFalse(diffint.matches("src/tests_helper.py", ["tests/**"]))
        self.assertFalse(diffint.matches("scripts/sub/check.ps1", ["scripts/*.ps1"]) and False)

    def test_removed_assertion_is_blocking(self):
        f = diffint.analyze(changes(diff_for("tests/test_a.py", [], ["        self.assertEqual(add(1, 2), 3)"])), self.PROT)
        kinds = {x["kind"]: x for x in f}
        self.assertTrue(kinds["assertions_removed"]["blocking"])
        self.assertIn("protected_path_changed", kinds)

    def test_replaced_assertion_is_not_flagged_as_removed(self):
        self.assertNotIn("assertions_removed", self.kinds(diff_for("tests/test_a.py", ["    assert x == 2"], ["    assert x == 1"])))

    def test_removed_test_function_and_skip_marker_and_deleted_file(self):
        self.assertIn("tests_removed", self.kinds(diff_for("tests/test_a.py", [], ["    def test_important(self):"])))
        self.assertIn("skip_added", self.kinds(diff_for("tests/test_a.py", ["    @unittest.skip('later')"], [])))
        self.assertIn("skip_added", self.kinds(diff_for("lib/foo_test.rs", ["#[ignore]"], [])))
        self.assertIn("test_file_deleted", self.kinds(diff_for("tests/test_a.py", [], ["x"], status="deleted")))

    def test_non_test_code_changes_are_not_test_findings(self):
        self.assertEqual(self.kinds(diff_for("src/app.py", ["    return a + b"], ["    return a - b"])), set())

    def test_build_config_is_reported_but_not_blocking(self):
        f = diffint.analyze(changes(diff_for("Cargo.toml", ['opt-level = 0'], ['opt-level = 3'])), self.PROT)
        self.assertEqual({x["kind"] for x in f}, {"protected_path_changed", "build_config_changed"})
        self.assertFalse(any(x["blocking"] for x in f))

    def test_protected_path_change_alone_is_not_blocking(self):
        f = diffint.analyze(changes(diff_for("tests/helpers.py", ["x = 2"], ["x = 1"])), self.PROT)
        self.assertEqual([(x["kind"], x["blocking"]) for x in f], [("protected_path_changed", False)])


class RedactTests(unittest.TestCase):
    def test_secrets_and_personal_strings_are_removed(self):
        text = ("password=hunter2 token: abc123 API_KEY=zzz Authorization: Bearer abcdefghijklmnop1234 mail me at someone@corp.example.org "
                "hash " + "a" * 40 + " path C:\\Users\\someone\\project /home/bob/x noreply: 1+x@users.noreply.github.com")
        out = redact.redact(text)
        for secret in ("hunter2", "abc123", "zzz", "abcdefghijklmnop1234", "someone@corp.example.org", "a" * 40, "someone", "bob"):
            self.assertNotIn(secret, out)
        self.assertIn("users.noreply.github.com", out)           # allowed identities are kept
        self.assertIn("C:\\Users\\[user]", out)

    def test_harmless_text_is_untouched(self):
        self.assertEqual(redact.redact("Ran 12 tests in 0.5s\n\nOK"), "Ran 12 tests in 0.5s\n\nOK")


UNIT = {"check_id": "unit", "argv": ["x"], "timeout": 30, "rule": {"type": "min_tests_discovered", "pattern": r"Ran (\d+) tests?", "skipped_pattern": r"skipped=(\d+)", "n": 1}}


class RuleTests(unittest.TestCase):
    def ev(self, check, code, out, timed_out=False, cwd="."):
        return rules.evaluate(check, code, out, cwd, timed_out)

    def test_pass_fail_and_silent_success(self):
        self.assertEqual(self.ev(UNIT, 0, "Ran 5 tests in 0.1s\n\nOK")[0], "VERIFIED")
        self.assertEqual(self.ev(UNIT, 1, "Ran 5 tests\nFAILED")[0], "FAILED")
        st, obs, _ = self.ev(UNIT, 0, "")
        self.assertEqual(st, "INCONCLUSIVE")
        self.assertIn("no test summary", obs)
        self.assertEqual(self.ev(UNIT, 0, "Ran 0 tests in 0.0s\n\nOK")[0], "INCONCLUSIVE")

    def test_all_tests_skipped_is_inconclusive(self):
        st, obs, _ = self.ev(UNIT, 0, "Ran 3 tests in 0.0s\n\nOK (skipped=3)")
        self.assertEqual(st, "INCONCLUSIVE")
        self.assertIn("skipped", obs)
        self.assertEqual(self.ev(UNIT, 0, "Ran 3 tests in 0.0s\n\nOK (skipped=1)")[0], "VERIFIED")

    def test_output_contains_and_silent_allowed_and_exit_code_in(self):
        c = {"check_id": "c", "argv": ["x"], "rule": {"type": "output_contains", "pattern": "ALL GOOD"}}
        self.assertEqual(self.ev(c, 0, "ALL GOOD")[0], "VERIFIED")
        self.assertEqual(self.ev(c, 0, "")[0], "INCONCLUSIVE")
        s = {"check_id": "s", "argv": ["x"], "rule": {"type": "silent_allowed"}}
        self.assertEqual(self.ev(s, 0, "")[0], "VERIFIED")
        self.assertEqual(self.ev(s, 2, "")[0], "FAILED")
        e = {"check_id": "e", "argv": ["x"], "rule": {"type": "exit_code_in", "codes": [0, 3]}}
        self.assertEqual(self.ev(e, 3, "")[0], "VERIFIED")
        self.assertEqual(self.ev(e, 4, "")[0], "FAILED")

    def test_artifact_exists_needs_a_non_empty_file(self):
        d = tempfile.mkdtemp(prefix="va_a_")
        self.addCleanup(shutil.rmtree, d, True)
        c = {"check_id": "a", "argv": ["x"], "rule": {"type": "artifact_exists", "paths": ["out/report.xml"]}}
        self.assertEqual(self.ev(c, 0, "", cwd=d)[0], "INCONCLUSIVE")
        os.makedirs(os.path.join(d, "out"))
        pathlib.Path(os.path.join(d, "out", "report.xml")).write_text("")
        self.assertEqual(self.ev(c, 0, "", cwd=d)[0], "INCONCLUSIVE")        # empty artifact does not count
        pathlib.Path(os.path.join(d, "out", "report.xml")).write_text("<ok/>")
        self.assertEqual(self.ev(c, 0, "", cwd=d)[0], "VERIFIED")

    def test_environment_failures_are_blocked_not_failed(self):
        self.assertEqual(self.ev(UNIT, 3221226505, "")[0], "BLOCKED")                       # rustc stack overrun seen on this machine
        self.assertEqual(self.ev(UNIT, 1, "error: Access is denied. (os error 5)")[0], "BLOCKED")
        self.assertEqual(self.ev(UNIT, 1, "No space left on device")[0], "BLOCKED")
        self.assertEqual(self.ev(UNIT, 1, "AssertionError: 1 != 2")[0], "FAILED")           # a normal test failure stays a failure

    def test_timeout_is_blocked_unless_declared_a_failure(self):
        self.assertEqual(self.ev(UNIT, None, "", timed_out=True)[0], "BLOCKED")
        self.assertEqual(self.ev({**UNIT, "timeout_is_failure": True}, None, "", timed_out=True)[0], "FAILED")

    def test_check_definitions_must_say_how_success_is_proven(self):
        rules.validate_check_def(UNIT)
        for bad in ({"check_id": "x", "argv": ["a"]}, {"check_id": "x", "argv": "python -m unittest", "rule": UNIT["rule"]},
                    {"check_id": "x", "argv": ["a"], "rule": {"type": "magic"}}, {"check_id": "x", "argv": ["a"], "rule": {"type": "min_tests_discovered"}},
                    {"check_id": "x", "argv": ["a"], "rule": {"type": "artifact_exists", "paths": []}}, {"check_id": "x", "argv": ["a"], "rule": {"type": "output_contains"}},
                    {**UNIT, "timeout": 99999}, {**UNIT, "repeatability": "sometimes"}, {"check_id": "x", "argv": [], "rule": UNIT["rule"]}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                rules.validate_check_def(bad)


def good_bundle():
    h = "a" * 64
    return {"run_id": "run_1", "task_id": "task_1", "base_commit": "b", "source_commit": "s", "patch_hash": "none", "manifest_hash": h, "verifier_id": "v",
            "verifier_version": "1", "env_fingerprint": "e", "started_at": 1.0, "ended_at": 2.0, "outcome": "VERIFIED",
            "checks": [{"check_id": "unit", "status": "VERIFIED", "expected": "x", "observed": "y", "evidence": [h, "b" * 64], "repeatability": "repeatable",
                        "attempts": [], "rule_results": []}], "diff_findings": [], "not_verified": [], "complete": True}


class BundleTests(unittest.TestCase):
    def test_good_bundle_validates_and_hash_is_stable(self):
        b = good_bundle()
        self.assertTrue(B.validate_bundle(b))
        self.assertEqual(B.bundle_hash(b), B.bundle_hash(json.loads(json.dumps(b))))

    def test_incomplete_bundle_is_rejected(self):
        b = good_bundle()
        b["complete"] = False
        with self.assertRaises(ValidationError) as cm:
            B.validate_bundle(b)
        self.assertEqual(cm.exception.code, "bundle_incomplete")
        del b["complete"]
        with self.assertRaises(ValidationError):
            B.validate_bundle(b)

    def test_missing_unknown_and_malformed_fields(self):
        for mutate in (lambda b: b.pop("manifest_hash"), lambda b: b.update(extra=1), lambda b: b.update(outcome="GREAT"), lambda b: b.update(manifest_hash="z" * 64),
                       lambda b: b["checks"][0].update(evidence=["nothex" + "0" * 58]), lambda b: b["checks"][0].update(evidence=[]),
                       lambda b: b["checks"][0].pop("attempts"), lambda b: b["diff_findings"].append({"kind": "x"}), lambda b: b.update(started_at="now")):
            b = good_bundle()
            mutate(b)
            with self.assertRaises(ValidationError):
                B.validate_bundle(b)


if __name__ == "__main__":
    unittest.main()
