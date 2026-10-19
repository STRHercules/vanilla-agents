import os
import sys
import tempfile
import unittest

from vanilla_agents.app.config import ROOT, load_all
from vanilla_agents.app.state import AppState
from vanilla_agents.events import schema
from vanilla_agents.events.feed import Feed

sys.path.insert(0, str(ROOT / "scripts"))
import demo_feed  # noqa: E402


class State(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "feed.jsonl")
        self.cfg = load_all()

    def tearDown(self):
        self.tmp.cleanup()

    def test_demo_script_is_schema_valid_and_tells_the_story(self):
        for _dt, et, task, agent, data in demo_feed.script():
            schema.validate_parts(et, task, agent, data)
        demo_feed.write(self.path)
        snap = AppState(self.cfg, self.path).snapshot()
        status = {t["id"]: t["status"] for t in snap["tasks"]}
        self.assertEqual(sorted(status.values()), ["blocked", "complete", "failed", "progress", "ready"])
        self.assertEqual(snap["counts"], {"done": 1, "total": 5})
        self.assertEqual(len(snap["agents"]), 3)
        self.assertEqual(snap["usage"]["used_pct"], 41)
        self.assertEqual({a["state"] for a in snap["agents"]}, {"working", "idle"})
        done = next(t for t in snap["tasks"] if t["status"] == "complete")
        self.assertEqual([o["verdict"] for o in done["objectives"]], ["VERIFIED", "VERIFIED"])

    def test_refresh_follows_feed_incrementally(self):
        st = AppState(self.cfg, self.path)
        self.assertEqual(st.snapshot()["cursor"], 0)
        Feed(self.path).append("task_created", "t1", None, {"title": "A"})
        new = st.refresh()
        self.assertEqual([l["type"] for l in new], ["task_created"])
        self.assertEqual(st.refresh(), [])
        Feed(self.path).append("task_verified", "t1", None, {"title": "A"})
        self.assertEqual(st.refresh()[0]["toast"], "complete")
        self.assertEqual(st.snapshot()["counts"]["done"], 1)

    def test_unreadable_database_is_explained_not_fatal(self):
        home = os.path.join(self.tmp.name, "proj")
        os.makedirs(os.path.join(home, "state", "db"))
        with open(os.path.join(home, "state", "db", "vanilla.db"), "wb") as f:
            f.write(b"not a database")
        snap = AppState(self.cfg, self.path, home).snapshot()
        self.assertIn("could not be read", snap["problem"])


if __name__ == "__main__":
    unittest.main()
