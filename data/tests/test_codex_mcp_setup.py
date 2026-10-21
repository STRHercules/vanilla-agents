"""The optional Codex MCP registration (V-053): add-only, idempotent, and never overwrites another server."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class CodexMCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = Path(__file__).resolve().parents[1] / "scripts" / "install_codex_mcp.py"
        spec = importlib.util.spec_from_file_location("codex_mcp_setup_test", src)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_add_does_not_modify_existing_codex_server(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout=json.dumps({"command": "x", "args": ["elsewhere.py"]}), stderr="")

        with patch.object(self.mod, "SCRIPT", Path(__file__)):
            self.assertEqual(self.mod.main([], run=run, which=lambda n: "/bin/codex"), 1)
        self.assertEqual(len(calls), 1)

    def test_register_and_idempotent(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            if cmd[2] == "get":
                return SimpleNamespace(returncode=1, stdout="", stderr="not registered")
            return SimpleNamespace(returncode=0, stdout="added", stderr="")

        with patch.object(self.mod, "SCRIPT", Path(__file__)):
            self.assertEqual(self.mod.main([], run=run, which=lambda n: "/bin/codex"), 0)
        self.assertEqual(calls[-1][:4], ["codex", "mcp", "add", "vanilla-agents"])
        self.assertTrue(self.mod._ours({"name": "vanilla-agents", "transport": {"type": "stdio", "command": "python", "args": [str(self.mod.SCRIPT)]}}))
        self.assertFalse(self.mod._ours({"name": "vanilla-agents", "transport": {"type": "stdio", "command": "python", "args": ["other.py"]}}))

    def test_dry_run_changes_nothing(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return SimpleNamespace(returncode=1, stdout="", stderr="not registered")

        with patch.object(self.mod, "SCRIPT", Path(__file__)):
            self.assertEqual(self.mod.main(["--dry-run"], run=run, which=lambda n: "/bin/codex"), 0)
        self.assertEqual(len(calls), 1)

    def test_remove_only_this_checkout(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout=json.dumps({"transport": {"type": "stdio", "args": ["other.py"]}}), stderr="")

        with patch.object(self.mod, "SCRIPT", Path(__file__)):
            self.assertEqual(self.mod.main(["--remove"], run=run, which=lambda n: "/bin/codex"), 0)
        self.assertEqual(len(calls), 1)

    def test_no_codex_is_a_plain_error(self):
        self.assertEqual(self.mod.main([], run=lambda cmd: None, which=lambda n: None), 1)
        self.assertEqual(self.mod.main(["--remove"], run=lambda cmd: None, which=lambda n: None), 0)

    @unittest.skipUnless(os.name == "nt", "Windows .cmd shim rule")
    def test_default_runner_starts_a_cmd_shim(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "codex.cmd").write_text("@echo off\r\necho shim works\r\n", encoding="ascii")
            with patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ.get("PATH", ""), "COMSPEC": os.environ.get("COMSPEC", "cmd.exe")}):
                r = self.mod._run(["codex", "version"])
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "shim works"))


if __name__ == "__main__":
    unittest.main()
