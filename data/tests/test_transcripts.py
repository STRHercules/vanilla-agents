"""Transcript watcher tests: synthetic fixtures only (never real transcripts)."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from vanilla_agents.events import transcripts as T
from vanilla_agents.events.feed import Feed


def asst(blocks, stop="tool_use", usage=None):
    return {"type": "assistant", "message": {"content": blocks, "stop_reason": stop, "usage": usage or {}}}


def tool(name, tid="t1", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def line(rec):
    return (json.dumps(rec) + "\n").encode()


class W(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        r = Path(self.td.name)
        self.proj, self.feed = r / "proj", Feed(r / "feed.jsonl")
        self.proj.mkdir()
        self.cfg = {"transcript_offsets_path": str(r / "off.json")}
        self.f = self.proj / "abcd1234.jsonl"

    def tearDown(self):
        self.td.cleanup()

    def w(self, **kw):
        return T.TranscriptWatcher(self.feed, self.proj, dict(self.cfg, **kw))

    def types(self):
        return [(e.type, e.agent, e.data) for e in self.feed.read_since(0)]

    def test_dir_name_rule(self):
        self.assertEqual(T.claude_dir_name(r"C:\Users\someone\Projects\demo"), "C--Users-someone-Projects-demo")
        root = Path(self.td.name)
        (root / "C--x-My-Proj").mkdir()
        self.assertEqual(T.find_project_dir(r"c:\x\my proj", root).name, "C--x-My-Proj")
        self.assertIsNone(T.find_project_dir(r"C:\nope", root))

    def test_mapping_and_incremental(self):
        self.f.write_bytes(line({"type": "user", "message": {"content": "SECRET TEXT"}}))
        w = self.w()
        w.poll()
        with open(self.f, "ab") as fh:
            fh.write(line(asst([{"type": "thinking"}, tool("Read", file_path="/a/b.txt")])))
            fh.write(line({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom"}]}}))
            fh.write(line(asst([{"type": "text", "text": "hi"}], stop="end_turn", usage={"input_tokens": 30000, "cache_read_input_tokens": 30000})))
        w.poll()
        w.poll()   # nothing new: no duplicates
        ev = self.types()
        kinds = [k for k, _, _ in ev]
        self.assertEqual(kinds, ["agent_started", "agent_working", "tool_use", "tool_use", "usage_update", "agent_idle"])
        self.assertEqual(ev[2][2], {"tool": "Read", "file": "/a/b.txt"})
        self.assertIn("error", ev[3][2]["tool"])
        self.assertEqual(ev[4][2]["used_pct"], 40)
        self.assertNotIn("SECRET", json.dumps(ev))
        self.assertEqual(ev[0][1], "Claude session abcd")

    def test_partial_line_waits(self):
        w = self.w()
        self.f.write_bytes(b"")
        w.poll()
        full = line(asst([tool("Bash", command="ls " + "x" * 500)]))
        with open(self.f, "ab") as fh:
            fh.write(full[:30])
        w.poll()
        self.assertEqual([k for k, _, _ in self.types()], ["agent_started"])
        with open(self.f, "ab") as fh:
            fh.write(full[30:])
        w.poll()
        last = self.types()[-1]
        self.assertEqual(last[0], "tool_use")
        self.assertEqual(len(last[2]["command"]), T.CMD_CAP)

    def test_junk_and_unknown_ignored(self):
        self.f.write_bytes(b"{not json\n" + line({"type": "weird", "x": 1}) + line([1, 2]) + line({"type": "assistant", "message": "str"}) + line(asst([tool("Edit", file_path="/q")])))
        self.w().poll()
        self.assertEqual([k for k, _, _ in self.types()], ["agent_started", "tool_use"])

    def test_offsets_persist_and_rotation(self):
        self.f.write_bytes(line(asst([tool("A", command="x" * 50)])))
        self.w().poll()
        n = len(self.types())
        w2 = self.w()
        self.assertEqual(w2.offsets[str(self.f)], self.f.stat().st_size)
        w2.poll()
        self.assertEqual(len(self.types()), n + 1)   # only the new agent_started of this run
        self.f.write_bytes(line(asst([tool("B")])))   # replaced by a smaller file (A line was longer)
        w2.poll()
        self.assertEqual(self.types()[-1][2]["tool"], "B")
        self.f.unlink()
        w2.poll()
        self.assertNotIn(str(self.f), w2.offsets)
        self.assertNotIn(str(self.f), json.loads(Path(self.cfg["transcript_offsets_path"]).read_text()))

    def test_subagent_and_cap(self):
        sub = self.proj / "abcd1234" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a1b2c3.jsonl").write_bytes(line(asst([tool("Grep")])))
        (sub / "agent-a1b2c3.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "look around"}))
        self.f.write_bytes(line(asst([tool("Read", file_path="/z")])))
        self.w().poll()
        names = {a for _, a, _ in self.types()}
        self.assertTrue(any(a and a.startswith("Explore ") for a in names))
        self.assertIn("Claude session abcd", names)
        ev = [d for k, a, d in self.types() if k == "agent_started" and a.startswith("Explore")]
        self.assertEqual(ev[0]["role"], "look around")
        self.feed.path.unlink()
        self.assertEqual(len(self.w(transcript_max_agents=1)._candidates()), 1)

    def test_idle_after_silence_and_old_files_skipped(self):
        self.f.write_bytes(line(asst([tool("Read")])))
        now = time.time()
        w = self.w(transcript_idle_minutes=10)
        w.clock = lambda: now
        w.poll()
        self.assertEqual(self.types()[-1][0], "tool_use")
        w.clock = lambda: now + 11 * 60
        w.poll()
        self.assertEqual(self.types()[-1][0], "agent_idle")
        n = len(self.types())
        w.poll()
        self.assertEqual(len(self.types()), n)   # not repeated
        w.clock = lambda: now + 10 * 3600
        self.assertEqual(w._candidates(), [])

    def test_baseline_reads_only_tail(self):
        self.f.write_bytes(b"".join(line(asst([tool("T%d" % i)])) for i in range(5000)))
        w = self.w()
        w.poll()
        self.assertLessEqual(len(self.types()), 1 + T.BASELINE_EVENTS)
        self.assertEqual(self.types()[-1][2]["tool"], "T4999")

    def test_secret_redacted_by_feed(self):
        self.f.write_bytes(line(asst([tool("Bash", command="curl -H 'Authorization: Bearer abcdefghijklmnop1234567890XYZ'")])))
        self.w().poll()
        self.assertNotIn("abcdefghijklmnop1234567890XYZ", json.dumps(self.types()))


if __name__ == "__main__":
    unittest.main()
