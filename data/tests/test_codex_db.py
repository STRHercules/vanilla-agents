"""Codex desktop app SQLite history (V-056): synthetic schema only; no real prompts or item_json are read."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from vanilla_agents.events import codex as C
from vanilla_agents.events import codex_db as D
from vanilla_agents.events.feed import Feed

SID = "0199c3a1-7b2d-7c4e-9f10-123456789abc"


class DbW(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.home = self.base / "codex"
        self.home.mkdir()
        self.feed = Feed(self.base / "feed.jsonl")
        self.managed = set()
        state = sqlite3.connect(self.home / "state_5.sqlite")
        state.execute("create table threads (id text primary key, cwd text, updated_at integer, model text, git_branch text, archived integer)")
        state.execute("insert into threads values (?,?,?,?,?,?)", (SID, "\\\\?\\" + str(self.project), int(time.time()), "gpt-test", "main", 0))
        state.commit()
        state.close()
        self.history = sqlite3.connect(self.home / "thread_history_1.sqlite")
        self.history.execute("create table thread_turns (thread_id text, turn_id text, status text, started_at integer, rollout_ordinal integer)")
        self.history.execute("create table thread_items (thread_id text, item_type text, rollout_ordinal integer)")
        self.history.execute("insert into thread_turns values (?,?,?,?,?)", (SID, "turn-1", "inProgress", int(time.time()), 1))
        for n, typ in enumerate(("userMessage", "commandExecution", "fileChange", "reasoning", "sleep"), 1):
            self.history.execute("insert into thread_items values (?,?,?)", (SID, typ, n))
        self.history.commit()

    def tearDown(self):
        self.history.close()
        self.td.cleanup()

    def watcher(self):
        return D.CodexDbWatcher(self.feed, self.project, {"codex": {"max_agents": 1}}, db_dir=self.home, managed=self.managed)

    def events(self):
        return [(e.type, e.agent, e.data) for e in self.feed.read_since(0)]

    def test_active_thread_and_tools_without_item_text(self):
        w = self.watcher()
        self.assertIn(SID, [t["id"] for t in w.refresh_ids()])
        self.assertGreater(w.poll(), 0)
        ev = self.events()
        kinds = [e[0] for e in ev]
        self.assertEqual(kinds[:3], ["agent_started", "agent_place", "session_stats"])
        self.assertEqual(ev[0][2], {"role": "Codex"})
        self.assertEqual(ev[1][2]["session"], SID)
        self.assertFalse(ev[1][2]["cwd"].startswith("\\\\?\\"))
        self.assertTrue(ev[1][2]["cwd"].endswith("project"))
        self.assertEqual(ev[2][2], {"model": "gpt-test"})
        self.assertEqual([d["tool"] for t, _a, d in ev if t == "tool_use"], ["command", "file change", "sleep"])
        self.assertIn("agent_working", kinds)
        a = next(a for a in w.agents.values() if a["id"].startswith("codex-"))
        self.assertEqual(a["state"], "working")
        self.assertNotIn("item_json", json.dumps(ev))

    def test_turn_completion_and_new_items(self):
        w = self.watcher()
        w.poll()
        before = len(self.events())
        self.history.execute("update thread_turns set status='completed'")
        self.history.execute("insert into thread_items values (?,?,?)", (SID, "contextCompaction", 6))
        self.history.execute("insert into thread_items values (?,?,?)", (SID, "commandExecution", 7))
        self.history.commit()
        w.poll()
        kinds = [e[0] for e in self.events()[before:]]
        self.assertEqual(kinds, ["agent_rested", "tool_use", "agent_idle"])
        self.assertEqual(self.events()[-1][2], {})
        self.assertEqual(next(iter(w.agents.values()))["state"], "idle")

    def test_managed_sessions_are_skipped_by_the_jsonl_watcher(self):
        sessions = self.base / "sessions" / "2026" / "10" / "20"
        sessions.mkdir(parents=True)
        (sessions / ("rollout-2026-10-20T10-00-00-" + SID + ".jsonl")).write_text(
            json.dumps({"type": "session_meta", "payload": {"id": SID, "cwd": str(self.project)}}) + "\n", encoding="utf-8")
        w = self.watcher()
        w.refresh_ids()
        jsonl = C.CodexWatcher(self.feed, self.project, {"codex": {"max_agents": 8}}, root=self.base / "sessions",
                               offsets_path=self.base / "off.json", skip_sessions=self.managed)
        self.assertEqual(jsonl.poll(), 0)
        self.assertEqual(self.events(), [])

    def test_missing_or_unreadable_database_is_a_no_op(self):
        empty = self.base / "empty"
        empty.mkdir()
        w = D.CodexDbWatcher(self.feed, self.project, db_dir=empty, managed=self.managed)
        self.assertIsNone(w.refresh_ids())
        self.assertEqual(w.poll(), 0)
        self.assertEqual(self.events(), [])
        self.assertEqual(D.clean_path("\\\\?\\R:\\x"), "R:\\x")


if __name__ == "__main__":
    unittest.main()
