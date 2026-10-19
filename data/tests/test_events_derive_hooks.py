import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from vanilla_agents.events import hooks
from vanilla_agents.events.derive import current_state, derive_events
from vanilla_agents.events.feed import Feed
from vanilla_agents.storage import layout, logical_dump, connect_ro
from tests.helpers import CTL, Base, rmtree_force

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DeriveTests(Base):
    def test_derive_maps_history_and_is_read_only(self):
        _, tid = self.project_task()
        w = self.svc.register_worker(CTL)
        self.svc.transition_execution(CTL, tid, "LEASING")
        self.svc.transition_execution(CTL, tid, "QUEUED")
        lease = self.svc.create_lease(CTL, tid, w, "slot", "s1")
        self.svc.set_worker_state(CTL, w, "alive")
        self.svc.block_task(CTL, tid, "approval_needed")
        db = layout(self.home)["db"]
        con = connect_ro(db)
        before = logical_dump(con)
        con.close()
        evs = derive_events(db)
        types = [e.type for e in evs]
        for t in ("task_created", "task_ready", "lease_acquired", "agent_started", "task_blocked"):
            self.assertIn(t, types)
        self.assertEqual([e.id for e in evs], sorted(e.id for e in evs))
        acq = [e for e in evs if e.type == "lease_acquired"][0]
        self.assertAlmostEqual(acq.data["expires_at"], current_state(db)["leases"][0]["expires_at"], places=3)     # D-410
        blocked = [e for e in evs if e.type == "task_blocked"][0]
        self.assertEqual(blocked.data["reason"], "approval_needed")
        self.assertEqual(derive_events(db, after_seq=evs[-1].id), [])
        st = current_state(db)
        self.assertEqual(st["tasks"][0]["execution"], "BLOCKED")
        self.assertEqual(st["leases"][0]["resource"], "slot:s1")
        self.assertEqual(st["workers"][0]["state"], "alive")
        con = connect_ro(db)
        self.assertEqual(logical_dump(con), before)
        con.close()
        self.assertTrue(lease)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="va_hook_")
        self.addCleanup(rmtree_force, self.tmp)
        self.path = os.path.join(self.tmp, "feed.jsonl")
        self.feed = Feed(self.path)

    def run_hook(self, obj):
        hooks.handle(json.dumps(obj), self.feed)
        return self.feed.read_since(0)

    def test_each_hook_kind_maps(self):
        base = {"session_id": "abcdef123456"}
        self.run_hook({**base, "hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}})
        self.run_hook({**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
                       "tool_response": {"stdout": "token=sekret99 done"}})
        self.run_hook({**base, "hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}, "tool_response": "ok"})
        self.run_hook({**base, "hook_event_name": "Stop"})
        self.run_hook({**base, "hook_event_name": "SubagentStop", "agent_type": "builder"})
        evs = self.run_hook({**base, "hook_event_name": "Notification", "message": "needs input"})
        self.assertEqual([e.type for e in evs], ["agent_working", "tool_use", "tool_use", "agent_idle", "agent_idle", "agent_idle"])
        self.assertEqual(evs[0].agent, "claude-abcdef12")
        self.assertEqual(evs[1].data["command"], "ls")
        self.assertNotIn("sekret99", evs[1].data["output_excerpt"])
        self.assertEqual(evs[2].data["file"], "a.py")
        self.assertEqual(evs[4].agent, "builder")
        self.assertEqual(evs[5].data["note"], "needs input")
        self.assertEqual(self.run_hook({"hook_event_name": "Whatever"}), evs)

    def test_main_swallows_everything_and_returns_zero(self):
        for raw in (b"", b"not json", b"[]", b'{"hook_event_name":"Stop"}'):
            self.assertEqual(hooks.main(io.BytesIO(raw), feed_path=self.path), 0)
        self.assertEqual(hooks.main(io.BytesIO(b'{"hook_event_name":"Stop"}'), feed_path=self.tmp), 0)   # unwritable path (a directory)

    def test_script_exits_zero_with_silent_output(self):
        env = {**os.environ, "VANILLA_FEED": self.path}
        for raw in (b"garbage", b'{"hook_event_name":"Stop","session_id":"s1"}'):
            r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "claude_hook.py")], input=raw, env=env, capture_output=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
        self.assertEqual([e.type for e in self.feed.read_since(0)], ["agent_idle"])


if __name__ == "__main__":
    unittest.main()
