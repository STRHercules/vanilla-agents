import io
import json
import os
import sys
import tempfile
import unittest

from vanilla_agents import mcp
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import install_mcp   # noqa: E402


class Mcp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = load_all()
        cfg["app"].update(projects_path=os.path.join(self.tmp.name, "p.json"), approvals_path=os.path.join(self.tmp.name, "a.json"))
        self.feed_path = os.path.join(self.tmp.name, "feed.jsonl")
        self.tools = mcp.Tools(cfg, self.feed_path, clock=lambda: 1000.0)

    def rpc(self, *msgs):
        out = io.StringIO()
        mcp.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out, self.tools)
        return [json.loads(l) for l in out.getvalue().splitlines()]

    def call(self, name, **args):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})[0]["result"]
        return r, r["content"][0]["text"]

    def test_handshake_and_tool_list(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                     {"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual(len(r), 3)                       # the notification gets no answer
        self.assertEqual(r[0]["result"]["protocolVersion"], "2025-03-26")
        names = {t["name"] for t in r[1]["result"]["tools"]}
        self.assertIn("start_quest", names)
        self.assertFalse(names & {"approve", "verify", "pause"})       # owner-only actions are never tools
        self.assertIn("propose_fix", names)                             # V-045: helpers may propose a competing fix ...
        self.assertFalse([n for n in names if any(w in n for w in ("pick", "winner", "contest", "compete", "roll"))])      # ... but never open a competition or pick the winner
        # V-044: imported work items stay proposals: no tool can import, refresh, make a quest from a bounty or touch integration settings
        self.assertEqual([n for n in names if any(w in n for w in ("bounty", "import", "integration", "github", "jira", "linear", "make_quest"))], [])
        # V-051: addons run in the owner's window with its full power: no tool can install, enable, remove, package or download one
        self.assertEqual([n for n in names if any(w in n for w in ("addon", "install", "enable", "disable", "plugin", "workshop", "package", "extension", "script"))], [])
        self.assertEqual(r[2]["error"]["code"], -32601)

    def test_quest_flow_lands_in_the_feed_unapproved(self):
        _, text = self.call("start_quest", title="Add a dark mode", why="easier at night", size="small", risk="low")
        qid = json.loads(text)["quest"]
        self.call("report_progress", activity="reading colours", quest=qid)
        self.call("quest_ready", quest=qid)
        quests = json.loads(self.call("list_quests")[1])
        self.assertEqual((quests[0]["id"], quests[0]["status"], quests[0]["approved"]), (qid, "ready", False))
        types = [e.type for e in Feed(self.feed_path).read_since(0, 100)]
        self.assertEqual(types, ["task_created", "agent_started", "agent_working", "task_ready"])

    def test_requests_and_answers(self):
        f = Feed(self.feed_path)
        f.append("task_created", task="q1", data={"title": "Login screen"})
        f.append("explain_requested", task="q1", data={"title": "Login screen"})
        self.assertEqual(json.loads(self.call("pending_requests")[1])[0]["quest"], "q1")
        self.call("answer_request", quest="q1", text="It is the first screen people see.")
        self.assertEqual(json.loads(self.call("pending_requests")[1]), [])

    def test_bad_calls_are_tool_errors_not_crashes(self):
        r, text = self.call("start_quest", title=" ")
        self.assertTrue(r["isError"])
        r, text = self.call("start_quest", title="x", size="huge")
        self.assertTrue(r["isError"])
        r, _ = self.call("start_quest", title="x", project="nowhere")
        self.assertTrue(r["isError"])


class Installer(unittest.TestCase):
    def test_desktop_config_merge_is_idempotent_and_removable(self):
        cur = {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}
        once = install_mcp.merged(cur, "py")
        self.assertEqual(install_mcp.merged(once, "py"), once)
        self.assertEqual(once["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(install_mcp.merged(once, "py", remove=True), cur)

    def test_writes_backup_and_prints_code_command_without_claude(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Claude", "claude_desktop_config.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as f:
                f.write("{}")
            out = io.StringIO()
            sys.stdout, old = out, sys.stdout
            try:
                rc = install_mcp.main(["--desktop-config", p], which=lambda c: None)
            finally:
                sys.stdout = old
            self.assertEqual(rc, 0)
            self.assertIn("claude mcp add", out.getvalue())
            self.assertTrue(any(n.startswith("claude_desktop_config.json.bak-") for n in os.listdir(os.path.dirname(p))))
            with open(p) as f:
                self.assertIn("vanilla-agents", json.load(f)["mcpServers"])


if __name__ == "__main__":
    unittest.main()
