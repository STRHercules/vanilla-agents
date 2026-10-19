"""scripts/reconnect.py: re-points hooks / tools installed from another folder, leaves correct or absent ones alone."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import install_hooks  # noqa: E402
import install_mcp  # noqa: E402
import reconnect  # noqa: E402

OLD = os.path.join("C:\\old", "vanilla-agents", "scripts")


class Reconnect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = os.path.join(self.tmp.name, "settings.json")
        self.desktop = os.path.join(self.tmp.name, "desktop.json")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def run_it(self):
        return reconnect.main(["--settings", self.settings, "--desktop-config", self.desktop], run=lambda c, **k: self.calls.append(c), which=lambda n: "claude")

    def test_moved_install_is_repointed(self):
        old_cmd = '"python" "%s"' % os.path.join(OLD, install_hooks.MARK)
        self.write(self.settings, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": old_cmd}]}], "Other": [{"hooks": [{"type": "command", "command": "keep me"}]}]}})
        self.write(self.desktop, {"mcpServers": {install_mcp.NAME: {"command": "python", "args": [os.path.join(OLD, "vanilla_mcp.py")]}, "other": {"command": "x"}}})
        self.assertEqual(self.run_it(), 0)
        s = self.read(self.settings)
        self.assertNotIn(OLD, json.dumps(s))
        self.assertIn(os.path.join(install_hooks.ROOT, "scripts", install_hooks.MARK), json.dumps(s))
        self.assertEqual(s["hooks"]["Other"][0]["hooks"][0]["command"], "keep me")
        d = self.read(self.desktop)
        self.assertEqual(d["mcpServers"][install_mcp.NAME]["args"], [install_mcp.SERVER])
        self.assertIn("other", d["mcpServers"])
        self.assertEqual([c[2] for c in self.calls], ["remove", "add"])     # Claude Code's copy replaced

    def test_nothing_installed_or_already_here_changes_nothing(self):
        self.assertEqual(self.run_it(), 0)
        self.assertFalse(os.path.exists(self.settings) or os.path.exists(self.desktop))
        self.write(self.settings, install_hooks.merged({}, install_hooks.command()))
        self.write(self.desktop, install_mcp.merged({}, "python"))
        before = (os.path.getmtime(self.settings), os.path.getmtime(self.desktop))
        self.run_it()
        self.assertEqual(before, (os.path.getmtime(self.settings), os.path.getmtime(self.desktop)))
        self.assertEqual(self.calls, [])
        self.assertEqual([f for f in os.listdir(self.tmp.name) if ".bak-" in f], [])


if __name__ == "__main__":
    unittest.main()
