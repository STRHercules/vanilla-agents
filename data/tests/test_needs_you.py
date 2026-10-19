"""V-026: Claude waiting on the owner / stopped by the service / session ended -> events and state; whispers handed over once at Stop;
telemetry hooks installed as async."""
import json
import os
import sys
import tempfile
import unittest

from vanilla_agents.app import social
from vanilla_agents.app.config import load_all
from vanilla_agents.app.state import Folder
from vanilla_agents.events import hooks
from vanilla_agents.events.feed import Feed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import install_hooks  # noqa: E402

H = {"session_id": "abc123", "cwd": "/x"}


class Mapping(unittest.TestCase):
    def kinds(self, **hook):
        return [(t, d) for t, _a, d in hooks.to_events(dict(H, **hook))]

    def test_notifications(self):
        self.assertEqual(self.kinds(hook_event_name="Notification", notification_type="permission_prompt", message="Claude needs your permission to use Bash")[0][1]["reason"], "permission")
        self.assertEqual(self.kinds(hook_event_name="Notification", notification_type="idle_prompt", message="Claude is waiting for your input")[0][1]["reason"], "input")
        self.assertEqual(self.kinds(hook_event_name="Notification", notification_type="auth_success"), [])
        self.assertEqual(self.kinds(hook_event_name="Notification", message="Claude needs your permission to use Edit")[0][0], "agent_waiting")      # older Claude Code: no type
        self.assertEqual(self.kinds(hook_event_name="Notification", message="something else")[0][0], "agent_idle")

    def test_permission_failure_end_prompt(self):
        self.assertEqual(self.kinds(hook_event_name="PermissionRequest", tool_name="Bash")[0], ("agent_waiting", {"reason": "permission", "note": "wants to use Bash"}))
        self.assertEqual(self.kinds(hook_event_name="StopFailure", error_type="rate_limit")[0], ("agent_stunned", {"reason": "rate_limit"}))
        self.assertEqual(self.kinds(hook_event_name="StopFailure")[0][1]["reason"], "unknown")
        self.assertEqual(self.kinds(hook_event_name="SessionEnd", reason="logout")[0][0], "agent_left")
        self.assertEqual(self.kinds(hook_event_name="UserPromptSubmit")[0][0], "agent_working")

    def test_state(self):
        cfg = load_all()
        f = Folder(cfg["strings"]["eli5"], cfg["metaphors"], 50, 40)
        with tempfile.TemporaryDirectory() as d:
            feed = Feed(os.path.join(d, "f.jsonl"))
            hooks.handle(json.dumps(dict(H, hook_event_name="SessionStart")), feed)
            hooks.handle(json.dumps(dict(H, hook_event_name="Notification", notification_type="permission_prompt", message="needs Bash")), feed)
            evs = feed.read_since(0)
            for ev in evs:
                f.apply(ev)
            a = f.snapshot()["agents"][0]
            self.assertEqual((a["needs"], a["needs_note"]), ("permission", "needs Bash"))
            hooks.handle(json.dumps(dict(H, hook_event_name="PostToolUse", tool_name="Bash", tool_input={"command": "ls"})), feed)
            for ev in feed.read_since(evs[-1].id):
                f.apply(ev)
            self.assertNotIn("needs", f.snapshot()["agents"][0])
            last = feed.last_id()
            hooks.handle(json.dumps(dict(H, hook_event_name="SessionEnd", reason="other")), feed)
            for ev in feed.read_since(last):
                f.apply(ev)
            self.assertEqual(f.snapshot()["agents"], [])


class Whispers(unittest.TestCase):
    def test_stop_hands_over_each_whisper_once_and_never_loops(self):
        with tempfile.TemporaryDirectory() as d:
            feed, seen = Feed(os.path.join(d, "f.jsonl")), os.path.join(d, "seen.json")
            agent = hooks._agent(H)
            social.whisper(feed, agent, "please also update the readme")
            out = hooks.stop_block(feed, dict(H, hook_event_name="Stop"), seen)
            self.assertEqual(json.loads(out)["decision"], "block")
            self.assertIn("update the readme", json.loads(out)["reason"])
            self.assertEqual(hooks.stop_block(feed, dict(H, hook_event_name="Stop"), seen), "")                      # shown once
            social.whisper(feed, agent, "and the changelog")
            self.assertEqual(hooks.stop_block(feed, dict(H, hook_event_name="Stop", stop_hook_active=True), seen), "")  # never twice in a row


class Install(unittest.TestCase):
    def test_new_events_and_async_flags(self):
        hk = install_hooks.merged({}, "cmd")["hooks"]
        for ev in ("PermissionRequest", "StopFailure", "SessionEnd"):
            self.assertIn(ev, hk)
        self.assertTrue(hk["PostToolUse"][0]["hooks"][0]["async"])
        for ev in ("SessionStart", "UserPromptSubmit", "Stop"):           # these may print text Claude must see: in step
            self.assertNotIn("async", hk[ev][0]["hooks"][0])


if __name__ == "__main__":
    unittest.main()


class MoreHooks(unittest.TestCase):
    def kinds(self, **hook):
        return [(t, d) for t, _a, d in hooks.to_events(dict(H, **hook))]

    def test_new_events(self):
        self.assertEqual(self.kinds(hook_event_name="PostToolUseFailure", tool_name="Bash", error="exit 1")[0], ("tool_failed", {"tool": "Bash", "error": "exit 1"}))
        self.assertEqual(self.kinds(hook_event_name="PreCompact")[0], ("agent_rested", {"phase": "start"}))
        self.assertEqual(self.kinds(hook_event_name="TaskCreated", task={"subject": "Write the tests"})[0], ("todo_update", {"text": "Write the tests"}))
        self.assertEqual(self.kinds(hook_event_name="TaskCompleted", subject="Write the tests")[0][1]["done"], "yes")
        self.assertEqual(self.kinds(hook_event_name="TaskCreated"), [])
        self.assertIn("helper", self.kinds(hook_event_name="SubagentStart", agent_type="Explore")[0][1]["activity"])
        self.assertEqual(self.kinds(hook_event_name="CwdChanged", new_cwd="/work/game")[0][1]["activity"], "moved to game")

    def test_state_todo_mana_and_clashes(self):
        cfg = load_all()
        f = Folder(cfg["strings"]["eli5"], cfg["metaphors"], 50, 40)
        with tempfile.TemporaryDirectory() as d:
            feed = Feed(os.path.join(d, "f.jsonl"))
            for sid in ("s1", "s2"):
                hooks.handle(json.dumps({"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": "/x/game.py"}}), feed)
            hooks.handle(json.dumps(dict(H, hook_event_name="TaskCreated", subject="A")), feed)
            hooks.handle(json.dumps(dict(H, hook_event_name="TaskCompleted", subject="A")), feed)
            hooks.handle(json.dumps(dict(H, hook_event_name="PostCompact")), feed)
            for ev in feed.read_since(0):
                f.apply(ev)
        snap = f.snapshot()
        self.assertEqual(len(snap["clashes"]), 1)
        self.assertEqual(snap["clashes"][0]["file"], "/x/game.py")
        a = next(x for x in snap["agents"] if x["id"] == hooks._agent(H))
        self.assertEqual(a["todo"], [{"text": "A", "done": True}])
        self.assertEqual(a["mana"], 100)
