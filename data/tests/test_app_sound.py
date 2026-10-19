import copy
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents.app import schema as S
from vanilla_agents.app import server, settings
from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import ROOT, load_all
from vanilla_agents.app.launch import build_cmd
from tests.test_app_check import assets_for

LOGIC = ROOT / "app" / "web" / "js" / "sound_logic.js"


class Settings(unittest.TestCase):
    def setUp(self):
        self.sd = load_all()["sounds"]
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_missing_or_damaged(self):
        m = settings.merge(self.sd, settings.load(self.path, self.sd))
        self.assertEqual(m["events"]["agent_finished"]["sound"], "peon_work_complete")
        self.assertEqual(m["master"], self.sd["master"])
        self.path.parent.mkdir()
        self.path.write_text("{not json")
        self.assertEqual(settings.load(self.path, self.sd), {"master": {}, "events": {}})

    def test_update_merges_and_persists(self):
        settings.update(self.path, {"events": {"task_verified": {"sound": "level_up"}}}, self.sd)
        m = settings.update(self.path, {"events": {"task_verified": {"volume": 0.3}}, "master": {"mute": True}}, self.sd)
        e = m["events"]["task_verified"]
        self.assertEqual((e["sound"], e["volume"], e["enabled"], m["master"]["mute"]), ("level_up", 0.3, True, True))
        self.assertEqual(json.loads(self.path.read_text())["events"]["task_verified"], {"sound": "level_up", "volume": 0.3})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["settings.json"])   # no temp file left

    def test_validation(self):
        bad = [[], {"x": 1}, {"master": {"volume": 2}}, {"master": {"volume": True}}, {"master": {"loud": 1}}, {"events": {"nope": {}}},
               {"events": {"usage_low": {"sound": "nope"}}}, {"events": {"usage_low": {"enabled": 1}}}, {"events": {"usage_low": {"extra": 1}}},
               {"events": {"usage_low": {"volume": -0.1}}}, {"events": []}]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                settings.validate(b, self.sd)
        self.assertEqual(settings.validate({}, self.sd), {"master": {}, "events": {}})


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        feed = Path(self.tmp.name) / "feed.jsonl"
        feed.write_text("")
        self.file = Path(self.tmp.name) / "settings.json"
        self.srv = server.make_server(server.Ctx(load_all(), str(feed), None, settings_file=self.file))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, body=None, host=None, origin=None, ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": host or "127.0.0.1:%d" % self.port, "Content-Type": ctype, "X-Vanilla-Key": self.srv.ctx.key}
        if origin:
            h["Origin"] = origin
        c.request(method, "/api/settings", json.dumps(body).encode() if method == "POST" else None, h)
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_get_post_roundtrip(self):
        st, d = self.call("GET")
        self.assertEqual((st, d["events"]["task_failed"]["sound"]), (200, "quest_failed"))
        st, d = self.call("POST", {"events": {"task_failed": {"sound": "level_up", "volume": 0.5}}})
        self.assertEqual((st, d["events"]["task_failed"]["sound"]), (200, "level_up"))
        self.assertEqual(self.call("GET")[1]["events"]["task_failed"]["volume"], 0.5)
        self.assertTrue(self.file.is_file())

    def test_rejections(self):
        self.assertEqual(self.call("POST", {"events": {"x": {}}})[0], 400)
        self.assertEqual(self.call("POST", {"master": {"volume": 5}})[0], 400)
        self.assertEqual(self.call("POST", [1])[0], 400)
        self.assertEqual(self.call("POST", {"pad": "x" * 5000})[0], 400)
        self.assertEqual(self.call("POST", {}, origin="http://evil.example")[0], 403)
        self.assertEqual(self.call("POST", {}, host="evil.example")[0], 403)
        self.assertEqual(self.call("POST", {}, ctype="text/plain")[0], 404)
        self.assertEqual(self.call("GET", host="evil.example")[0], 403)
        self.assertFalse(self.file.exists())


class CheckSounds(unittest.TestCase):
    def setUp(self):
        self.cfg = load_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.assets = assets_for(self.cfg, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def probs(self, edit):
        c = copy.deepcopy(self.cfg)
        edit(c["sounds"])
        return " | ".join(check_app(c, assets_dir=self.assets))

    def test_missing_file(self):
        shutil.rmtree(Path(self.assets) / "sound")
        self.assertIn("needs the asset sound/creature", " | ".join(check_app(self.cfg, assets_dir=self.assets)))

    def test_errors(self):
        self.assertEqual(self.probs(lambda s: None), "")
        self.assertIn("'nope' which is not in the sounds list", self.probs(lambda s: s["events"]["usage_low"].update(sound="nope")))
        self.assertIn("between 0 and 1", self.probs(lambda s: s["events"]["usage_low"].update(volume=1.5)))
        self.assertIn("between 0 and 1", self.probs(lambda s: s["master"].update(volume=-1)))
        self.assertIn("sound event 'made_up' is not", self.probs(lambda s: s["events"].update(made_up=dict(s["events"]["usage_low"]))))
        self.assertIn("sound event 'quest_ready' is missing", self.probs(lambda s: s["events"].pop("quest_ready")))
        self.assertIn("nobody uses: 'extra'", self.probs(lambda s: s.update(extra=1)))
        self.assertIn("used twice", self.probs(lambda s: s["sounds"].append(dict(s["sounds"][0]))))
        self.assertIn("'bogus_event'", self.probs(lambda s: s["triggers"].update(bogus_event="usage_low")))
        self.assertIn("raw game file", self.probs(lambda s: s["sounds"][0].update(path="sound/a.blp")))


class Mapping(unittest.TestCase):
    def test_table_covers_every_sound_event(self):
        sd = load_all()["sounds"]
        self.assertEqual(set(sd["triggers"].values()), set(S.SOUND_EVENTS))
        self.assertTrue(set(sd["triggers"]) <= set(S.EVENT_TYPES))

    def run_js(self, events, prev, now, prev_pct, pct):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        sd = load_all()["sounds"]
        args = [sd["triggers"], [{"type": t} for t in events], prev, now, prev_pct, pct, sd["usage_low_pct"]]
        code = "import(%s).then(m=>console.log(JSON.stringify(m.soundEventsFor(...%s))))" % (json.dumps(LOGIC.as_uri()), json.dumps(args))
        return json.loads(subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)

    def test_mapping_logic(self):
        work, idle = [{"id": "a", "state": "working"}], [{"id": "a", "state": "idle"}]
        self.assertEqual(self.run_js(["agent_idle"], work, idle, 10, 10), ["agent_finished"])
        self.assertEqual(self.run_js(["agent_idle"], idle, idle, 10, 10), [])
        every = ["task_verified", "task_failed", "task_blocked", "agent_started", "task_ready", "tool_use"]
        self.assertEqual(self.run_js(every, [], [], 0, 0), ["task_verified", "task_failed", "task_blocked", "agent_started", "quest_ready"])
        self.assertEqual(self.run_js(["usage_update"], [], [], 70, 85), ["usage_low"])
        self.assertEqual(self.run_js(["usage_update"], [], [], 85, 90), [])
        self.assertEqual(self.run_js(["usage_update"], [], [], None, 20), [])


class Launcher(unittest.TestCase):
    def test_autoplay_flag_and_profile(self):
        c = build_cmd("chrome.exe", "http://x/", "C:/p", 1, 2)
        self.assertIn("--autoplay-policy=no-user-gesture-required", c)
        self.assertIn("--user-data-dir=C:/p", c)


if __name__ == "__main__":
    unittest.main()
