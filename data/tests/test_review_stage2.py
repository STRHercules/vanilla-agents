"""Fixes for the independent Stage 2 review (docs/STAGE2_REVIEW.md items 1-2)."""
import os
import shutil
import subprocess
import unittest

from vanilla_agents.verify import patchcheck
from vanilla_agents.verify.checkout import make_checkout
from . import vhelpers as V


class ParserHunkTests(unittest.TestCase):
    def test_header_looking_added_line_is_content(self):
        patch = (b"diff --git a/tests/t.py b/tests/t.py\n--- a/tests/t.py\n+++ b/tests/t.py\n@@ -1,2 +1,2 @@\n"
                 b"-    assert x == 1\n++ docs\n keep\n")
        (ch,) = patchcheck.parse_patch(patch)
        self.assertEqual(ch.path, "tests/t.py")
        self.assertEqual(ch.removed, ["    assert x == 1"])
        self.assertEqual(ch.added, ["+ docs"])

    def test_header_looking_removed_line_is_content(self):
        patch = (b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1 @@\n--- x\n keep\n")
        (ch,) = patchcheck.parse_patch(patch)
        self.assertEqual(ch.removed, ["-- x"])
        self.assertEqual(ch.path, "a.py")

    def test_two_files_still_split(self):
        patch = (b"diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-x\n+y\n"
                 b"diff --git a/b b/b\n--- a/b\n+++ b/b\n@@ -1 +1 @@\n-p\n+q\n")
        self.assertEqual([c.path for c in patchcheck.parse_patch(patch)], ["a", "b"])


class NestedRepoTests(unittest.TestCase):
    def test_patch_applies_when_work_root_is_inside_another_repo(self):
        tmp = V.new_tmp()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, "src")
        base = V.make_repo(repo, {"app.py": V.APP})
        patch = V.make_patch(repo, {"app.py": V.BROKEN_APP})
        outer = os.path.join(tmp, "outer")
        subprocess.run(["git", "init", "-q", outer], check=True, capture_output=True)
        out = make_checkout(repo, base, patch, os.path.join(outer, "co"), [])
        with open(os.path.join(out["work"], "app.py")) as f:
            self.assertEqual(f.read(), V.BROKEN_APP)


class SumRuleTests(unittest.TestCase):
    def test_sum_rule_adds_every_cargo_summary(self):
        from vanilla_agents.verify.rules import evaluate
        out = "test result: ok. 3 passed; 0 failed; 0 ignored\ntest result: ok. 2 passed; 0 failed; 1 ignored\ntest result: ok. 0 passed; 0 failed\n"
        chk = {"rule": {"type": "min_tests_discovered", "pattern": r"test result: ok\. ([1-9]\d*) passed", "skipped_pattern": r"(\d+) ignored", "sum": True, "n": 5}}
        st, obs, _ = evaluate(chk, 0, out, ".")
        self.assertEqual(st, "VERIFIED")
        self.assertIn("5 test(s)", obs)


if __name__ == "__main__":
    unittest.main()
