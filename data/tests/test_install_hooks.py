import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import install_hooks as I   # noqa: E402


class InstallHooks(unittest.TestCase):
    def test_adds_once_keeps_others_and_removes_cleanly(self):
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}
        cur = {"model": "x", "hooks": {"PreToolUse": [other]}}
        once = I.merged(cur, "py claude_hook.py")
        twice = I.merged(once, "py claude_hook.py")
        self.assertEqual(once, twice)
        self.assertEqual(once["model"], "x")
        self.assertEqual(once["hooks"]["PreToolUse"][0], other)
        self.assertEqual(set(once["hooks"]), set(I.EVENTS))
        self.assertEqual(I.merged(once, "", remove=True), cur)

    def test_writes_backup_and_refuses_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "settings.json")
            with open(p, "w") as f:
                f.write('{"a": 1}')
            self.assertEqual(I.main(["--settings", p]), 0)
            self.assertTrue(any(n.startswith("settings.json.bak-") for n in os.listdir(d)))
            with open(p) as f:
                self.assertIn("SessionStart", json.load(f)["hooks"])
            with open(p, "w") as f:
                f.write("{broken")
            self.assertEqual(I.main(["--settings", p]), 1)


if __name__ == "__main__":
    unittest.main()
