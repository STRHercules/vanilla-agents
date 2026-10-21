"""Tokens per minute (damage meter Tokens tab): output tokens counted once per message from transcripts, reported at most every
15 s as session_stats tokens_out, kept as samples on the helper; the transcript watcher also reports where a main session runs (V-038/V-039)."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from vanilla_agents.app.config import load_all
from vanilla_agents.app.state import Folder
from vanilla_agents.events import transcripts as T
from vanilla_agents.events.feed import Feed
from vanilla_agents.events.redact_util import clean

SID = "0b5f0c3e-1b7a-4c1e-9d2c-7e1f5a9c0d11"


def asst(mid, out, cwd=None):
    r = {"type": "assistant", "message": {"id": mid, "content": [], "stop_reason": "tool_use", "usage": {"output_tokens": out}}}
    if cwd:
        r["cwd"] = cwd
    return (json.dumps(r) + "\n").encode()


class Count(unittest.TestCase):
    def test_one_message_over_several_records_counts_once(self):
        ag = T._Agent("x")
        for mid, n in (("m1", 10), ("m1", 10), ("m1", 25), ("m2", 5), ("m2", 3)):
            T.count_tokens(json.loads(asst(mid, n)), ag)
        self.assertEqual(ag.tok_total, 30)
        T.count_tokens({"type": "user", "message": {"usage": {"output_tokens": 999}}}, ag)
        T.count_tokens({"type": "assistant", "message": {"usage": {"output_tokens": "lots"}}}, ag)
        self.assertEqual(ag.tok_total, 30)


class Watch(unittest.TestCase):
    def test_reports_tokens_and_place(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            proj, feed = r / "proj", Feed(r / "feed.jsonl")
            proj.mkdir()
            now = [time.time()]
            w = T.TranscriptWatcher(feed, proj, {"transcript_offsets_path": str(r / "off.json")}, clock=lambda: now[0])
            f = proj / (SID + ".jsonl")
            f.write_bytes(asst("m1", 40, cwd=d) + asst("m2", 60))
            w.poll()
            with open(f, "ab") as fh:
                fh.write(asst("m3", 100))
            w.poll()                      # within 15 s: not reported yet
            now[0] += 16
            w.poll()
            evs = feed.read_since(0)
            place = [e for e in evs if e.type == "agent_place"]
            self.assertEqual(len(place), 1)
            self.assertEqual(place[0].data["session"], SID)
            self.assertEqual(place[0].data["cwd"], clean(d))     # the feed hides the PC user's name in paths
            toks = [e.data["tokens_out"] for e in evs if e.type == "session_stats"]
            self.assertEqual(toks, [100, 200])


class Fold(unittest.TestCase):
    def test_samples_for_the_meter(self):
        cfg = load_all()
        f = Folder(cfg["strings"]["eli5"], cfg["metaphors"], 50, 40)

        class E:
            def __init__(self, i, ts, n):
                self.id, self.ts, self.type, self.agent, self.task, self.data = i, ts, "session_stats", "a", None, {"tokens_out": n}
        for i, (ts, n) in enumerate([(100, 50), (130, 250), (160, 400)]):
            f.apply(E(i + 1, ts, n))
        f.apply(E(9, 170, 10))           # a restarted count (new baseline) drops older, larger samples
        a = f.snapshot()["agents"][0]
        self.assertEqual((a["tokens_out"], a["tok"]), (10, [[170, 10]]))
        self.assertEqual(f.snapshot()["chat"], [])         # numbers only, never a chat line


if __name__ == "__main__":
    unittest.main()
