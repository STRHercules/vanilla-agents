"""Stage 2c review fixes: manifest completeness, job containment failure, Windows path aliases."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from vanilla_agents.verify import manifest, patchcheck, runner


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        for n in ("checks.json", "protected.json"):
            pathlib.Path(self.d, n).write_text("[]")

    def test_required_and_core_files_are_pinned(self):
        m = manifest.write_manifest(self.d, [])
        for k in ("trusted:checks.json", "trusted:protected.json", "core:audit.py", "core:contracts.py", "core:__init__.py", "code:runner.py"):
            self.assertIn(k, m["files"])

    def test_manifest_missing_required_entry_is_refused(self):
        m = manifest.write_manifest(self.d, [])
        del m["files"]["trusted:protected.json"]
        pathlib.Path(self.d, manifest.MANIFEST_NAME).write_text(json.dumps(m))
        with self.assertRaises(manifest.ManifestMismatch):
            manifest.require_manifest(self.d)

    def test_expected_hash(self):
        manifest.write_manifest(self.d, [])
        h = manifest.require_manifest(self.d)
        self.assertEqual(manifest.require_manifest(self.d, expected_hash=h), h)
        with self.assertRaises(manifest.ManifestMismatch):
            manifest.require_manifest(self.d, expected_hash="0" * 64)


class RunnerTests(unittest.TestCase):
    def test_normal_run_still_works(self):
        r = runner.run_check([sys.executable, "-c", "print('hi')"], os.getcwd(), dict(os.environ), 30)
        self.assertEqual((r["exit_code"], "hi" in r["output"]), (0, True))

    @unittest.skipUnless(os.name == "nt", "Job Objects are Windows only")
    def test_failed_assignment_is_a_start_error_not_a_pass(self):
        with mock.patch.object(runner._k32, "AssignProcessToJobObject", return_value=0):
            r = runner.run_check([sys.executable, "-c", "print('ran')"], os.getcwd(), dict(os.environ), 30)
        self.assertTrue(r.get("start_error"))
        self.assertIsNone(r["exit_code"])
        self.assertNotIn("ran", r["output"].replace("not contained", ""))

    @unittest.skipUnless(os.name == "nt", "Job Objects are Windows only")
    def test_failed_job_creation_is_a_start_error(self):
        with mock.patch.object(runner._k32, "CreateJobObjectW", return_value=None):
            r = runner.run_check([sys.executable, "-c", "pass"], os.getcwd(), dict(os.environ), 30)
        self.assertTrue(r.get("start_error"))


class SafePathTests(unittest.TestCase):
    def test_rejects_windows_aliases(self):
        for p in ("PROGRA~1/x.py", "src/NUL", "nul.txt", "a/CON.py", "COM1", "lpt9.log", "src/aux", "a/b.", "a/b ", "a/c. "):
            self.assertFalse(patchcheck.safe_path(p), p)

    def test_accepts_normal_paths(self):
        for p in ("src/app.py", "tests/test_a.py", "console.py", "a/null.txt", "a/b.c/d.py", "COM10.txt"):
            self.assertTrue(patchcheck.safe_path(p), p)


if __name__ == "__main__":
    unittest.main()
