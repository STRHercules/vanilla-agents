"""V-030 statusline bridge: parsing Claude Code's status line JSON, throttled session_stats events, the chained status line,
the installer (keeps and restores your old status line), reconnect, and how the state folds the numbers."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from vanilla_agents.app.config import ROOT, load_all
from vanilla_agents.app.state import AppState
from vanilla_agents.events import statusline as SL
from vanilla_agents.events.feed import Feed
from vanilla_agents.events.hooks import _agent

sys.path.insert(0, str(ROOT / "scripts"))
import install_statusline  # noqa: E402
import reconnect  # noqa: E402

# the documented example (https://code.claude.com/docs/en/statusline), trimmed
DOC = {"session_id": "abc12345-6789", "transcript_path": "/p/t.jsonl", "cwd": "/w", "model": {"id": "claude-opus-4-7", "display_name": "Opus"},
       "cost": {"total_cost_usd": 0.01234, "total_duration_ms": 45000, "total_api_duration_ms": 2300, "total_lines_added": 156, "total_lines_removed": 23},
       "context_window": {"total_input_tokens": 15234, "context_window_size": 200000, "used_percentage": 8, "remaining_percentage": 92, "current_usage": None},
       "exceeds_200k_tokens": False,
       "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600}, "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}}}


class Parse(unittest.TestCase):
    def test_documented_example(self):
        self.assertEqual(SL.to_data(DOC), {"model": "Opus", "ctx_pct": 8, "cost_usd": 0.01234, "lines_added": 156, "lines_removed": 23, "duration_ms": 45000,
                                           "five_hour_pct": 23.5, "five_hour_resets": 1738425600, "seven_day_pct": 41.2, "seven_day_resets": 1738857600})

    def test_missing_and_bad_fields_are_dropped(self):
        self.assertEqual(SL.to_data({}), {})
        self.assertEqual(SL.to_data({"model": "x", "cost": None, "context_window": {"used_percentage": None}, "rate_limits": {"five_hour": {}}}), {})
        d = SL.to_data({"model": {"id": "m1"}, "context_window": {"used_percentage": 140}, "cost": {"total_cost_usd": True, "total_lines_added": -3, "total_duration_ms": "9"},
                        "rate_limits": {"five_hour": {"used_percentage": float("nan")}, "seven_day": {"used_percentage": 120}}})
        self.assertEqual(d, {"model": "m1", "ctx_pct": 100, "seven_day_pct": 100})

    def test_default_line(self):
        self.assertEqual(SL.default_line(DOC), "\u2694 Opus \u00b7 ctx 8% \u00b7 5h 24%")
        self.assertEqual(SL.default_line({}), "\u2694 Claude")


class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feed = os.path.join(self.tmp.name, "feed.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_throttled_per_session_with_the_hook_agent_id(self):
        raw = json.dumps(DOC).encode()
        ev = SL.record(raw, self.feed, now=1000)
        self.assertEqual((ev.type, ev.agent), ("session_stats", _agent(DOC)))
        self.assertEqual(ev.agent, "claude-abc12345")
        self.assertIsNone(SL.record(raw, self.feed, now=1010))           # inside 20 s
        other = json.dumps({**DOC, "session_id": "zzz99999"}).encode()
        self.assertIsNotNone(SL.record(other, self.feed, now=1011))      # another session is not held back
        self.assertIsNotNone(SL.record(raw, self.feed, now=1021))
        self.assertEqual(len(Feed(self.feed).read_since(0)), 3)

    def test_nothing_without_session_or_numbers(self):
        self.assertIsNone(SL.record(json.dumps({"model": {"display_name": "Opus"}}).encode(), self.feed, now=1))
        self.assertIsNone(SL.record(json.dumps({"session_id": "s"}).encode(), self.feed, now=1))
        self.assertIsNone(SL.record(b"[]", self.feed, now=1))

    def run_main(self, raw, run=None):
        out = io.BytesIO()
        self.assertEqual(SL.main(stdin=io.BytesIO(raw), stdout=out, feed_path=self.feed, run=run or subprocess.run), 0)
        return out.getvalue().decode("utf-8")

    def test_main_prints_default_line_and_records(self):
        self.assertEqual(self.run_main(json.dumps(DOC).encode()), "\u2694 Opus \u00b7 ctx 8% \u00b7 5h 24%")
        self.assertEqual(Feed(self.feed).read_since(0)[0].data["ctx_pct"], 8)
        self.assertEqual(self.run_main(b"not json"), "\u2694 Claude")     # garbage in: still a line, still exit 0

    def test_main_chains_your_old_status_line(self):
        with open(os.path.join(self.tmp.name, SL.STORE_NAME), "w", encoding="utf-8") as f:
            json.dump({"previous": {"type": "command", "command": "my-line.sh"}}, f)
        seen = []

        def run(cmd, **k):
            seen.append((cmd, k["input"], k["timeout"]))
            return SimpleNamespace(returncode=0, stdout=b"mine: ok\n")
        raw = json.dumps(DOC).encode()
        self.assertEqual(self.run_main(raw, run), "mine: ok")
        self.assertEqual(seen, [("my-line.sh", raw, SL.STATUS_TIMEOUT)])

        def slow(cmd, **k):
            raise subprocess.TimeoutExpired(cmd, k["timeout"])
        self.assertTrue(self.run_main(raw, slow).startswith("\u2694 Opus"))
        self.assertTrue(self.run_main(raw, lambda c, **k: SimpleNamespace(returncode=1, stdout=b"x")).startswith("\u2694"))

    def test_real_previous_command_gets_the_same_stdin(self):
        cmd = '"%s" -c "import sys; print(sys.stdin.read()[:5])"' % sys.executable
        self.assertEqual(SL.run_previous(cmd, b"hello world"), "hello")

    def test_our_own_script_is_never_chained(self):
        p = os.path.join(self.tmp.name, "s.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"previous": {"command": "python /x/scripts/statusline.py"}}, f)
        self.assertIsNone(SL.previous_command(p))
        self.assertIsNone(SL.previous_command(os.path.join(self.tmp.name, "missing.json")))


class Installer(unittest.TestCase):
    CMD = '"py" "/va/scripts/statusline.py"'

    def test_install_keeps_and_remove_restores_yours(self):
        theirs = {"type": "command", "command": "~/.claude/mine.sh", "padding": 2}
        s, st = install_statusline.merged({"model": "opus", "statusLine": theirs}, {}, self.CMD)
        self.assertEqual(s["statusLine"], {"type": "command", "command": self.CMD, "padding": 2})
        self.assertEqual(st["previous"], theirs)
        self.assertEqual(s["model"], "opus")
        s2, st2 = install_statusline.merged(s, st, self.CMD)                # twice: still theirs remembered, not ours
        self.assertEqual((s2, st2), (s, st))
        back, st3 = install_statusline.merged(s2, st2, self.CMD, remove=True)
        self.assertEqual(back, {"model": "opus", "statusLine": theirs})
        self.assertNotIn("previous", st3)

    def test_install_without_a_previous_line(self):
        s, st = install_statusline.merged({}, {}, self.CMD)
        self.assertEqual(st, {"previous": None})
        back, _ = install_statusline.merged(s, st, self.CMD, remove=True)
        self.assertEqual(back, {})
        untouched, _ = install_statusline.merged({"statusLine": {"command": "other"}}, {}, self.CMD, remove=True)
        self.assertEqual(untouched["statusLine"], {"command": "other"})      # not ours: remove leaves it alone

    def test_main_writes_backup_store_and_settings(self):
        with tempfile.TemporaryDirectory() as d:
            settings, store = os.path.join(d, "settings.json"), os.path.join(d, "store.json")
            with open(settings, "w", encoding="utf-8") as f:
                json.dump({"statusLine": {"type": "command", "command": "mine"}}, f)
            self.assertEqual(install_statusline.main(["--settings", settings, "--store", store]), 0)
            with open(settings, encoding="utf-8") as f:
                self.assertIn("statusline.py", json.load(f)["statusLine"]["command"])
            with open(store, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["previous"]["command"], "mine")
            self.assertTrue(any(n.startswith("settings.json.bak-") for n in os.listdir(d)))
            self.assertEqual(install_statusline.main(["--settings", settings, "--store", store, "--remove"]), 0)
            with open(settings, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["statusLine"], {"type": "command", "command": "mine"})

    def test_reconnect_repoints_a_moved_status_line(self):
        old = {"statusLine": {"type": "command", "command": '"python" "C:\\old\\scripts\\statusline.py"'}}
        self.assertTrue(reconnect.stale_statusline(old))
        here = {"statusLine": {"type": "command", "command": install_statusline.command()}}
        self.assertFalse(reconnect.stale_statusline(here))
        self.assertFalse(reconnect.stale_statusline({"statusLine": {"command": "mine"}}))
        self.assertFalse(reconnect.stale_statusline({}))


class Fold(unittest.TestCase):
    def test_state_uses_real_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "feed.jsonl")
            feed = Feed(path)
            feed.append("agent_started", agent="claude-abc12345")
            feed.append("session_stats", agent="claude-abc12345", data=SL.to_data(DOC))
            feed.append("usage_update", agent="claude-abc12345", data={"used_pct": 70})     # transcript estimate: must not override the real figure
            snap = AppState(load_all(), path).snapshot()
            a = snap["agents"][0]
            self.assertEqual((a["mana"], a["ctx_pct"], a["cost_usd"], a["lines_added"], a["model"]), (92, 8, 0.01234, 156, "Opus"))
            self.assertEqual(snap["usage"]["five_hour"]["used_pct"], 23.5)
            self.assertEqual(snap["usage"]["seven_day"]["resets_at"], 1738857600)
            self.assertEqual(snap["usage"]["used_pct"], 70)
            self.assertFalse(any(l["type"] == "session_stats" for l in snap["chat"]))       # numbers only, no chat line


if __name__ == "__main__":
    unittest.main()
