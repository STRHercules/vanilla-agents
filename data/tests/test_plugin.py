"""V-031 Claude Code plugin (data/plugin): the committed files match the generator, and the hooks match the hook installer exactly."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import install_hooks  # noqa: E402
import make_plugin  # noqa: E402

PLUGIN = os.path.join(ROOT, "plugin")


def load(rel):
    with open(os.path.join(PLUGIN, *rel.split("/")), encoding="utf-8") as f:
        return json.load(f)


class Plugin(unittest.TestCase):
    def test_committed_files_are_generated(self):
        self.assertEqual(make_plugin.main(["--check"]), 0, "run: python scripts/make_plugin.py")

    def test_hooks_match_the_installer(self):
        hooks = load("hooks/hooks.json")["hooks"]
        want = install_hooks.merged({}, "X")["hooks"]
        self.assertEqual(list(hooks), install_hooks.EVENTS)
        for ev in install_hooks.EVENTS:
            (entry,) = hooks[ev]
            (h,) = entry["hooks"]
            self.assertEqual(bool(h.get("async")), ev in install_hooks.ASYNC, ev)
            self.assertEqual(entry.get("matcher"), want[ev][0].get("matcher"), ev)
            self.assertEqual((h["type"], h["timeout"]), ("command", want[ev][0]["hooks"][0]["timeout"]))
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}/../scripts/claude_hook.py"', h["command"])

    def test_paths_point_at_real_scripts(self):
        mcp = load(".mcp.json")["mcpServers"]["vanilla-agents"]
        for arg in mcp["args"] + [make_plugin.SCRIPTS + "claude_hook.py"]:
            path = arg.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN)
            self.assertTrue(os.path.isfile(path), path)

    def test_manifest_and_marketplace(self):
        p, m = load(".claude-plugin/plugin.json"), load(".claude-plugin/marketplace.json")
        self.assertEqual(p["name"], "vanilla-agents")
        self.assertEqual((m["name"], m["owner"]["name"]), ("vanilla-agents-local", "Vanilla Agents"))
        self.assertEqual([(e["name"], e["source"]) for e in m["plugins"]], [("vanilla-agents", "./")])
        self.assertFalse(p["name"].startswith(("claude-", "anthropic-")))


if __name__ == "__main__":
    unittest.main()
