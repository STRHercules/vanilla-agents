"""Workshop extras: daily quests (pure + posting once a day), your own office GLBs (/api/office), addons (/api/addons, /addons/*.js,
no path escape), per-helper mana / last-seen / waiting in the state, the phone page, and the buildings recipe list."""
import datetime
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents.app import server, social
from vanilla_agents.app.config import load_all
from vanilla_agents.app.state import Folder
from vanilla_agents.events.feed import Feed

MON_10 = datetime.datetime(2026, 10, 19, 10, 0)     # a Monday
SUN_10 = datetime.datetime(2026, 10, 18, 10, 0)


class Dailies(unittest.TestCase):
    D = {"enabled": True, "quests": [{"title": "Morning check", "why": "", "at": "09:00", "days": ["mon", "fri"]},
                                     {"title": "Every day", "why": "", "at": "11:30", "days": []}]}

    def test_due_by_day_and_time_once(self):
        self.assertEqual([i for i, _ in social.due_dailies(self.D, {}, MON_10)], [0])
        self.assertEqual(social.due_dailies(self.D, {"0": "2026-10-19"}, MON_10), [])       # already posted today
        self.assertEqual(social.due_dailies(self.D, {}, SUN_10), [])                         # not its day; the other is later
        self.assertEqual([i for i, _ in social.due_dailies(self.D, {}, SUN_10.replace(hour=12))], [1])

    def test_off_by_default_and_bad_time_skipped(self):
        self.assertEqual(social.due_dailies({"enabled": False, "quests": self.D["quests"]}, {}, MON_10), [])
        self.assertEqual(social.due_dailies(None, {}, MON_10), [])
        self.assertEqual(social.due_dailies({"enabled": True, "quests": [{"title": "x", "at": "nine", "days": []}]}, {}, MON_10), [])
        self.assertFalse(load_all()["app"]["daily"]["enabled"])

    def test_post_writes_quests_and_remembers(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_all()
            cfg["app"]["daily"] = self.D
            feed, made = Feed(str(Path(d) / "f.jsonl")), str(Path(d) / "made.json")
            ids = social.post_dailies(feed, cfg, None, made, MON_10)
            self.assertEqual(len(ids), 1)
            self.assertEqual(social.post_dailies(feed, cfg, None, made, MON_10.replace(hour=11)), [])
            with open(made) as fh:
                self.assertEqual(json.load(fh), {"0": "2026-10-19"})
            evs = feed.read_since(0)
            self.assertEqual([e.data["title"] for e in evs if e.type == "task_created"], ["Morning check"])


class FolderExtras(unittest.TestCase):
    def test_mana_seen_and_waiting(self):
        cfg = load_all()
        f = Folder(cfg["strings"]["eli5"], cfg["metaphors"], 50, 40)
        with tempfile.TemporaryDirectory() as d:
            feed = Feed(str(Path(d) / "f.jsonl"))
            feed.append("task_created", "q1", None, {"title": "Q"})
            feed.append("agent_working", "q1", "a1", {"activity": "Edit x"})
            feed.append("usage_update", None, "a1", {"used_pct": 30})
            feed.append("task_ready", "q1", "a1", {"title": "Q"})
            evs = feed.read_since(0)
            for ev in evs:
                f.apply(ev)
        a = next(x for x in f.snapshot()["agents"] if x["id"] == "a1")
        self.assertEqual(a["mana"], 70)
        self.assertTrue(a["seen"])
        self.assertTrue(a["waiting"])


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "office").mkdir()
        (root / "office" / "my_office.glb").write_bytes(b"glTF")
        (root / "office" / "hq.glb").write_bytes(b"glTF")
        (root / "office" / "hq.json").write_text(json.dumps({"label": "HQ", "music": ["stormwind"], "start": [1, 2], "evil": "no"}))
        (root / "addons").mkdir()
        (root / "addons" / "hello.js").write_text("export default () => {};")
        (root / "secret.js").write_text("nope")
        self.ctx = server.Ctx(load_all(), str(root / "feed.jsonl"), None, assets_dir=root, watch=False, settings_file=str(root / "settings.json"))
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_office_lists_glbs_with_sidecar(self):
        st, data = self.get("/api/office")
        self.assertEqual(st, 200)
        b = {x["key"]: x for x in json.loads(data)["buildings"]}
        self.assertEqual(set(b), {"custom:hq", "custom:my_office"})
        self.assertEqual((b["custom:hq"]["label"], b["custom:hq"]["music"], b["custom:hq"]["start"], b["custom:hq"]["glb"]), ("HQ", ["stormwind"], [1, 2], "office/hq.glb"))
        self.assertNotIn("evil", b["custom:hq"])
        self.assertEqual(b["custom:my_office"]["label"], "My Office (yours)")
        self.assertTrue(b["custom:my_office"]["custom"])

    def test_addons_listed_served_and_contained(self):
        st, data = self.get("/api/addons")
        d = json.loads(data)       # V-051: full entries; an old single file is a "legacy" addon and loads unless switched off
        self.assertEqual([(a["key"], a["legacy"]) for a in d["addons"]], [("hello.js", True)])
        self.assertEqual([a["key"] for a in d["load"]], ["hello.js"])
        st, data = self.get("/addons/hello.js")
        self.assertEqual((st, data), (200, b"export default () => {};"))
        for bad in ("/addons/../secret.js", "/addons/%2e%2e/secret.js", "/addons/%252e%252e/secret.js", "/addons/hello.txt"):
            self.assertEqual(self.get(bad)[0], 404, bad)

    def test_phone_page_is_served(self):
        st, data = self.get("/phone.html")
        self.assertEqual(st, 200)
        self.assertIn(b"/api/state", data)
        self.assertIn(b"$('owner').hidden = !key", data)        # V-040: read-only unless opened with the window key


class Buildings(unittest.TestCase):
    def test_recipes_are_well_formed(self):
        p = Path(__file__).resolve().parents[1] / "vanilla_agents" / "assets" / "buildings.json"
        rs = json.loads(p.read_text())
        self.assertEqual(len({r["key"] for r in rs}), len(rs))
        for r in rs:
            self.assertTrue(r["root"].lower().endswith(".wmo"), r["key"])
            self.assertIsInstance(r["sets"], list)
            self.assertTrue(r["music"])
        self.assertIn("goldshire_c", {r["key"] for r in rs})


if __name__ == "__main__":
    unittest.main()
