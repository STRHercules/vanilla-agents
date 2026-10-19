import unittest

from vanilla_agents.verify import diffint
from vanilla_agents.verify.manifest import ManifestMismatch, manifest_hash, load_manifest
from vanilla_agents.verify.patchcheck import FileChange, parse_patch
from vanilla_agents.verify.verifier import run_verification
from tests import vhelpers as vh


def kinds(*changes):
    return {f["kind"] for f in diffint.analyze(list(changes), [])}


class ExpectedManifestHash(unittest.TestCase):
    def test_mismatch_refused_and_match_accepted(self):
        tmp = vh.new_tmp()
        trusted = vh.make_trusted(tmp + "/t", [vh.UNIT_CHECK])
        req = vh.request(tmp, tmp, "b", trusted, [])
        req["expected_manifest_hash"] = "0" * 64
        with self.assertRaises(ManifestMismatch):
            run_verification(req)
        req["expected_manifest_hash"] = manifest_hash(load_manifest(trusted))
        try:
            run_verification(req)
        except ManifestMismatch:
            self.fail("matching hash must not be refused")
        except Exception:
            pass                                    # later failures (bogus repo) are irrelevant here


class Shadowing(unittest.TestCase):
    def test_strong_names_block(self):
        for n in ("unittest.py", "sub/conftest.py", "sitecustomize.py"):
            self.assertIn("module_shadowing", kinds(FileChange(path=n, status="added", added=["x = 1"])))
        f = diffint.analyze([FileChange(path="unittest.py", status="added")], [])
        self.assertTrue(f[0]["blocking"])

    def test_stdlib_name_is_non_blocking_and_modify_is_clean(self):
        f = diffint.analyze([FileChange(path="json.py", status="added")], [])
        self.assertEqual((f[0]["kind"], f[0]["blocking"]), ("module_shadowing", False))
        self.assertNotIn("module_shadowing", kinds(FileChange(path="conftest.py", old_path="conftest.py", status="modified")))
        self.assertNotIn("module_shadowing", kinds(FileChange(path="app.py", status="added")))


PATCH = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,6 +1,4 @@
 class T(unittest.TestCase):
     def test_a(self):
-        x = f()
-        y = g(x)
-        self.assertEqual(y, 3)
+        pass
     def test_b(self):
"""


class Gutting(unittest.TestCase):
    def test_gutted_body_with_kept_assertion(self):
        removed = ["        x = f()", "        self.assertEqual(y, 3)"]
        added = ["        pass", "        self.assertEqual(y, 3)"]
        ch = FileChange(path="tests/test_a.py", removed=removed, added=added, hunks=[(removed, added)])
        self.assertIn("test_body_gutted", kinds(ch))

    def test_parsed_hunks_and_honest_edit(self):
        ch = parse_patch(PATCH.encode())[0]
        self.assertEqual(len(ch.hunks), 1)
        self.assertIn("assertions_removed", kinds(ch))          # caught already when the assertion is dropped
        real = FileChange(path="tests/test_a.py", removed=["        x = f()"], added=["        x = f2()"], hunks=[(["        x = f()"], ["        x = f2()"])])
        self.assertNotIn("test_body_gutted", kinds(real))

    def test_known_limit_nontrivial_stub_not_flagged(self):
        # known limit (D-055): a body replaced by a non-trivial no-op such as `x = 1` is not recognised
        r, a = ["        x = f()", "        self.assertEqual(y, 3)"], ["        x = 1", "        self.assertEqual(y, 3)"]
        self.assertNotIn("test_body_gutted", kinds(FileChange(path="tests/test_a.py", removed=r, added=a, hunks=[(r, a)])))
