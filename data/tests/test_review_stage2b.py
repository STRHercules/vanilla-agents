import os
import tempfile
import time
import unittest

from vanilla_agents.verify import diffint, rules
from vanilla_agents.verify.patchcheck import FileChange


def kinds(**kw):
    return {f["kind"] for f in diffint.analyze([FileChange(**kw)], [])}


class DiffIntTests(unittest.TestCase):
    def test_rename_out_of_test_path(self):
        self.assertIn("tests_removed", kinds(path="src/old.py", old_path="tests/test_a.py", status="renamed"))

    def test_modified_assertion(self):
        self.assertIn("assertions_modified", kinds(path="tests/test_a.py", removed=["    self.assertEqual(f(), 3)"], added=["    assert True"]))

    def test_async_def_and_skiptest_and_case(self):
        self.assertIn("tests_removed", kinds(path="Tests/test_a.py", removed=["async def test_x(self):"]))
        self.assertIn("skip_added", kinds(path="TESTS/test_a.py", added=["    self.skipTest('x')"]))
        self.assertIn("skip_added", kinds(path="tests/test_a.py", added=["@unittest.expectedFailure"]))

    def test_rust_inline_tests(self):
        self.assertIn("tests_removed", kinds(path="src/lib.rs", removed=["#[test]"]))
        self.assertIn("assertions_removed", kinds(path="src/lib.rs", removed=["assert_eq!(a, b);"]))


class ArtifactTests(unittest.TestCase):
    def test_preexisting_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "out.bin")
            open(f, "w").write("x")
            os.utime(f, (time.time() - 100, time.time() - 100))
            chk = {"rule": {"type": "artifact_exists", "paths": ["out.bin"]}}
            self.assertEqual(rules.evaluate(chk, 0, "", d, False, time.time())[0], "INCONCLUSIVE")
            self.assertEqual(rules.evaluate(chk, 0, "", d, False, time.time() - 200)[0], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
