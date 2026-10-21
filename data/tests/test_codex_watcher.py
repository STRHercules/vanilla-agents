"""Codex CLI watcher tests: synthetic rollout fixtures only (never real Codex sessions)."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from vanilla_agents.app import server
from vanilla_agents.app.config import load_all
from vanilla_agents.events import codex as C
from vanilla_agents.events import transcripts
from vanilla_agents.events.feed import Feed


class W(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.root = base / "sessions"
        self.root.mkdir()
        self.project = base / "repo"
        self.project.mkdir()
        self.feed = Feed(base / "feed.jsonl")
        self.offsets = base / "offsets.json"
        self.sid = "0199c3a1-7b2d-7c4e-9f10-123456789abc"

    def tearDown(self):
        self.td.cleanup()

    def watcher(self, **kw):
        return C.CodexWatcher(self.feed, self.project, {"codex": {"max_agents": 8}}, root=self.root, offsets_path=self.offsets, **kw)

    def write_session(self, cwd=None, records=()):
        folder = self.root / "2026" / "10" / "20"
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / ("rollout-2026-10-20T10-00-00-" + self.sid + ".jsonl")
        self.add_records(file, [("session_meta", {"id": self.sid, "cwd": str(cwd or self.project)})] + list(records))
        return file

    @staticmethod
    def add_records(file, pairs):
        with file.open("a", encoding="utf-8") as fh:
            for typ, payload in pairs:
                fh.write(json.dumps({"timestamp": "2026-10-20T10:00:00Z", "type": typ, "payload": payload}) + "\n")

    def events(self):
        return [(e.type, e.agent, e.data) for e in self.feed.read_since(0)]

    def test_reports_codex_and_keeps_text_private(self):
        file = self.write_session(records=[
            ("event_msg", {"type": "task_started"}),
            ("response_item", {"type": "function_call", "name": "exec_command", "arguments": "top secret"}),
            ("event_msg", {"type": "task_complete", "last_agent_message": "private answer"}),
        ])
        w = self.watcher()
        self.assertGreater(w.poll(), 0)
        self.assertEqual([e[0] for e in self.events()], ["agent_started", "agent_place", "agent_working", "tool_use", "agent_idle"])
        self.assertEqual(self.events()[0][2]["role"], "Codex")
        self.assertEqual(self.events()[1][2]["session"], self.sid)
        self.assertEqual(self.events()[2][2], {"activity": "working on a Codex request"})
        self.assertEqual(self.events()[3][2], {"tool": "exec_command"})
        raw = Path(self.feed.path).read_text(encoding="utf-8")
        self.assertNotIn("private answer", raw)
        self.assertNotIn("top secret", raw)
        self.assertTrue(file.exists())
        self.assertEqual(w.poll(), 0)

    def test_ignores_other_projects_and_malformed_rows(self):
        file = self.write_session(cwd=Path(self.td.name) / "other", records=[("event_msg", {"type": "task_started"})])
        with file.open("a", encoding="utf-8") as fh:
            fh.write("bad json\n")
        self.assertEqual(self.watcher().poll(), 0)
        self.assertEqual(self.events(), [])

    def test_incremental_and_restart_without_duplicate_work(self):
        file = self.write_session(records=[("event_msg", {"type": "task_started"})])
        w = self.watcher()
        w.poll()
        self.add_records(file, [("event_msg", {"type": "task_complete"})])
        w.poll()
        self.assertEqual([x[0] for x in self.events()].count("agent_idle"), 1)
        after = len(self.events())
        self.watcher().poll()
        self.assertEqual([x[0] for x in self.events()[after:]], ["agent_started", "agent_place"])

    def test_stats_only_and_no_raw_text(self):
        self.write_session(records=[
            ("turn_context", {"model": "gpt-example"}),
            ("event_msg", {"type": "token_count", "info": {"total_token_usage": {"output_tokens": 123}}}),
            ("response_item", {"type": "message", "role": "user", "content": "secret content"}),
        ])
        self.watcher().poll()
        stats = [d for t, _a, d in self.events() if t == "session_stats"]
        self.assertEqual(stats, [{"model": "gpt-example"}, {"tokens_out": 123}])
        self.assertNotIn("secret content", str(self.events()))

    def test_other_turn_events(self):
        self.write_session(records=[
            ("event_msg", {"type": "task_started"}),
            ("event_msg", {"type": "turn_aborted", "reason": "interrupted"}),
            ("event_msg", {"type": "context_compacted"}),
            ("compacted", {}),
            ("event_msg", {"type": "mcp_tool_call_end", "invocation": {"name": "quest_ready"}}),
        ])
        self.watcher().poll()
        self.assertEqual([e[0] for e in self.events()], ["agent_started", "agent_place", "agent_working", "agent_stunned", "agent_rested", "agent_rested", "tool_use"])
        self.assertEqual(self.events()[3][2], {"reason": "interrupted"})
        self.assertEqual(self.events()[6][2], {"tool": "quest_ready"})

    def test_partial_line_waits_for_its_end(self):
        file = self.write_session()
        w = self.watcher()
        w.poll()
        full = (json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n").encode()
        with file.open("ab") as fh:
            fh.write(full[:10])
        w.poll()
        self.assertNotIn("agent_working", [e[0] for e in self.events()])
        with file.open("ab") as fh:
            fh.write(full[10:])
        w.poll()
        self.assertIn("agent_working", [e[0] for e in self.events()])

    def test_windows_path_matching_and_nested_paths(self):
        self.assertTrue(C._belongs("G:\\Game\\Repo\\src", "g:\\game\\repo"))
        self.assertFalse(C._belongs("G:\\Game\\Repo2", "g:\\game\\repo"))
        self.assertFalse(C._belongs(Path(self.td.name) / "other", self.project))
        self.assertFalse(C._belongs("\x00bad", self.project))

    def test_bad_event_types_are_ignored(self):
        self.assertEqual(C._events({"type": "response_item", "payload": {"type": "reasoning", "text": "hidden"}}, {}), [])
        self.assertEqual(C._events({"type": "event_msg", "payload": {"type": "token_count", "info": None}}, {}), [])
        self.assertEqual(C._events({"type": "response_item", "payload": {"type": "message", "content": "hidden"}}, {}), [])

    def test_server_watch_runs_claude_and_codex_together(self):
        self.write_session(records=[("event_msg", {"type": "task_started"})])
        cfg = load_all()
        base = Path(self.td.name)
        cfg["app"]["codex"].update(sessions_dir=str(self.root), db_dir=str(base / "no-codex-db"), offsets_path=str(self.offsets), poll_seconds=1)
        cfg["app"]["transcripts_dir"] = str(base / "claude-projects")
        cfg["app"]["transcript_offsets_path"] = str(base / "claude-offsets.json")
        cfg["app"]["transcript_poll_seconds"] = 1
        ctx = server.Ctx(cfg, str(self.feed.path), None, assets_dir=self.td.name, settings_file=str(base / "s.json"), watch=False)
        try:
            ctx.watch(self.project)
            deadline = time.time() + 5
            while time.time() < deadline and not [e for e in self.feed.read_since(0) if e.type == "agent_started"]:
                time.sleep(0.05)
            self.assertIn("Codex", [e.data.get("role") for e in self.feed.read_since(0) if e.type == "agent_started"])
            # Claude starts later: the watcher was already running for the expected (not yet existing) folder.
            cdir = transcripts.project_dir(self.project, base / "claude-projects")
            cdir.mkdir(parents=True)
            (cdir / (self.sid + ".jsonl")).write_text(
                json.dumps({"type": "user", "message": {"content": "hello"}}) + "\n" +
                json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                                                       "input": {"file_path": str(self.project / "a.py")}}],
                                                            "stop_reason": "end_turn"}}) + "\n", encoding="utf-8")
            deadline = time.time() + 8
            roles = set()
            while time.time() < deadline:
                roles = {e.data.get("role") for e in self.feed.read_since(0) if e.type == "agent_started"}
                if {"Codex", "main session"} <= roles:
                    break
                time.sleep(0.1)
            self.assertEqual(roles, {"Codex", "main session"})
        finally:
            ctx.watch(None)
            ctx.stop.set()

    def test_config_and_environment_overrides(self):
        cfg = {"codex": {"enabled": False, "sessions_dir": str(self.root), "offsets_path": str(self.offsets), "max_agents": 2}}
        self.assertFalse(C.enabled(cfg, env={}))
        self.assertTrue(C.enabled(cfg, env={"VANILLA_CODEX_ENABLED": "1"}))
        self.assertFalse(C.enabled({"codex": {"enabled": True}}, env={"VANILLA_CODEX_ENABLED": "no"}))
        self.assertEqual(C.sessions_root(cfg, env={}), self.root)
        self.assertEqual(C.offsets_file(cfg, env={}), self.offsets)
        self.assertEqual(C.sessions_root(cfg, env={"VANILLA_CODEX_SESSIONS": str(self.root / "x")}), self.root / "x")
        self.assertEqual(C.sessions_root({}, env={"CODEX_HOME": str(self.root / "home")}), self.root / "home" / "sessions")
        self.assertEqual(C.db_dir({}, env={"CODEX_HOME": str(self.root / "home")}), self.root / "home")
        self.assertEqual(C.db_dir({}, env={"VANILLA_CODEX_DB_DIR": str(self.root / "db")}), self.root / "db")
        self.assertEqual(C.CodexWatcher(self.feed, self.project, cfg).max_agents, 2)


if __name__ == "__main__":
    unittest.main()
