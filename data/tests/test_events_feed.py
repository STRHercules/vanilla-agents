import os
import shutil
import tempfile
import threading
import unittest

from vanilla_agents.events.errors import EventSchemaError, FeedError
from vanilla_agents.events.feed import Feed


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="va_feed_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "sub", "feed.jsonl")
        self.feed = Feed(self.path, clock=lambda: 100.0)

    def test_ids_rise_and_read_since_uses_cursor(self):
        self.assertEqual(self.feed.last_id(), 0)
        ids = [self.feed.append("agent_idle", agent="a").id for _ in range(3)]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual([e.id for e in self.feed.read_since(0)], [1, 2, 3])
        self.assertEqual([e.id for e in self.feed.read_since(2)], [3])
        self.assertEqual(self.feed.read_since(3), [])
        self.assertEqual(len(self.feed.read_since(0, limit=2)), 2)
        self.assertEqual(Feed(self.path).last_id(), 3)   # a fresh reader/writer continues the numbering

    def test_redaction_and_cap_before_write(self):
        self.feed.append("tool_use", agent="a", data={"tool": "Bash", "command": "curl -H 'Authorization: Bearer abcdefgh12345678' x",
                                                       "output_excerpt": "password=hunter2 C:\\Users\\bob\\x " + "z" * 5000})
        raw = open(self.path, encoding="ascii").read()
        self.assertNotIn("hunter2", raw)
        self.assertNotIn("abcdefgh12345678", raw)
        self.assertNotIn("bob", raw)
        self.assertLessEqual(len(self.feed.read_since(0)[0].data["output_excerpt"]), 2000)

    def test_invalid_event_not_written(self):
        with self.assertRaises(EventSchemaError):
            self.feed.append("tool_use", data={})
        self.assertFalse(os.path.exists(self.path))

    def test_rotation_keeps_ids_and_drops_oldest(self):
        f = Feed(self.path, max_bytes=300, keep=2)
        for _ in range(30):
            f.append("agent_idle", agent="a")
        self.assertEqual(f.last_id(), 30)
        got = [e.id for e in f.read_since(0, 1000)]
        self.assertEqual(got, sorted(got))
        self.assertEqual(got[-1], 30)
        self.assertGreater(f.oldest_id(), 1)
        self.assertEqual(got, list(range(got[0], 31)))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "sub", "feed.3.jsonl")))

    def test_torn_and_junk_lines_are_skipped(self):
        self.feed.append("agent_idle")
        with open(self.path, "ab") as fh:
            fh.write(b'{"v":1,"id":2,"ts"')   # crash mid-write, no newline
        e = self.feed.append("agent_idle")
        self.assertEqual(e.id, 2)
        with open(self.path, "ab") as fh:
            fh.write(b"not json\n")
        self.assertEqual([x.id for x in self.feed.read_since(0)], [1, 2])

    def test_oversize_event_refused(self):
        with self.assertRaises(FeedError):
            self.feed.append("tool_use", data={"tool": "t", "file": "f" * 2000, "command": "c" * 2000, "output_excerpt": "o" * 2000}, agent="a" * 2000, task="t" * 2000)
            self.feed.append("tool_use", data={"tool": "\u00e9" * 2000, "file": "\u00e9" * 2000, "command": "\u00e9" * 2000, "output_excerpt": "\u00e9" * 2000})

    def test_concurrent_appends_get_unique_consecutive_ids(self):
        def work():
            for _ in range(10):
                self.feed.append("agent_idle", agent="a")
        ts = [threading.Thread(target=work) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual([e.id for e in self.feed.read_since(0, 100)], list(range(1, 41)))


if __name__ == "__main__":
    unittest.main()
