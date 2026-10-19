"""Spellbook: durable run log (D-240), read-only checks list (D-241), config rules. No real runs."""
import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents.app import checks_api, runs, server
from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import load_all
from tests.test_app_verify import Base, bundle


def rec(i, verdict="VERIFIED", checks=("cargo_test",), seconds=12.0):
    return {"run_id": "r%d" % i, "ts": 1000.0 + i, "commit": "a" * 40, "verdict": verdict, "seconds": seconds, "title": "Verify x", "reason": "",
            "checks": [{"check_id": c, "status": verdict} for c in checks]}


class RunsLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmp.name, "sub", "runs.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_empty(self):
        self.assertEqual(runs.read(self.p), [])

    def test_newest_first_and_limit(self):
        for i in range(5):
            runs.append(self.p, rec(i), 10 ** 6)
        self.assertEqual([r["run_id"] for r in runs.read(self.p, 3)], ["r4", "r3", "r2"])

    def test_only_known_fields_are_kept(self):
        runs.append(self.p, dict(rec(1), output="SECRET OUTPUT", env={"K": "v"}), 10 ** 6)
        self.assertEqual(set(runs.read(self.p)[0]), set(runs.KEEP))
        self.assertNotIn("SECRET", Path(self.p).read_text())

    def test_rotation_bounds_size_and_keeps_recent(self):
        for i in range(60):
            runs.append(self.p, rec(i), 2000)
        self.assertLess(os.path.getsize(self.p), 2200)
        self.assertTrue(os.path.exists(self.p + ".1"))
        self.assertEqual(runs.read(self.p, 1)[0]["run_id"], "r59")
        self.assertLessEqual(os.path.getsize(self.p) + os.path.getsize(self.p + ".1"), 4400)

    def test_damaged_lines_skipped(self):
        runs.append(self.p, rec(1), 10 ** 6)
        with open(self.p, "a") as f:
            f.write("{torn\n[1,2]\n")
        runs.append(self.p, rec(2), 10 ** 6)
        self.assertEqual([r["run_id"] for r in runs.read(self.p)], ["r2", "r1"])

    def test_last_by_check_uses_newest_run_with_that_check(self):
        rs = [rec(3, "FAILED", ["a"]), rec(2, "VERIFIED", ["a", "b"]), rec(1, "VERIFIED", ["c"])]
        last = runs.last_by_check(rs)
        self.assertEqual((last["a"]["verdict"], last["b"]["verdict"], last["c"]["verdict"]), ("FAILED", "VERIFIED", "VERIFIED"))


class Finish(Base):
    def test_run_is_logged_with_verdict_checks_duration_commit(self):
        j = self.job(bundle("VERIFIED"))
        self.run_job(j)
        r = runs.read(j.runs_path)[0]
        self.assertEqual((r["verdict"], r["commit"], r["checks"]), ("VERIFIED", "a" * 40, [{"check_id": "cargo_test", "status": "VERIFIED", "seconds": 0}]))
        self.assertGreaterEqual(r["seconds"], 0)
        self.assertTrue(r["run_id"].startswith("ui-"))

    def test_failed_and_crashed_runs_are_logged_too(self):
        j = self.job(bundle("FAILED"))
        self.run_job(j)
        self.assertEqual(runs.read(j.runs_path)[0]["verdict"], "FAILED")
        j2 = self.job(lambda req: 1 / 0)
        self.run_job(j2)
        r = runs.read(j2.runs_path)[0]
        self.assertEqual(r["verdict"], "BLOCKED")
        self.assertIn("stopped unexpectedly", r["reason"])

    def test_unwritable_log_never_breaks_the_run(self):
        j = self.job(bundle("VERIFIED"))
        j.runs_path = self.tmp.name          # a folder: cannot be appended to
        self.assertEqual(self.run_job(j)[-1], "task_verified")
        self.assertFalse(j.running)


class ChecksApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_all()
        self.trusted = self.tmp.name
        defs = [{"check_id": "cargo_test", "argv": ["cargo", "test"], "cwd": ".", "env": ["USERPROFILE", "APPDATA"], "repeatability": "repeatable",
                 "rule": {"type": "min_tests_discovered", "pattern": "x"}, "timeout": 1800},
                {"check_id": "lint_all", "argv": ["C:\\secret\\tool.exe"], "repeatability": "needs_reset"}]
        Path(self.trusted, "checks.json").write_text(json.dumps(defs))
        self.entry = {"repo": "C:\\secret\\repo", "trusted_dir": self.trusted, "expected_manifest_hash": "f" * 64, "criteria": ["cargo_test"]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_ids_labels_repeatability_expected(self):
        d = checks_api.check_list(self.entry, self.cfg, [rec(1, "FAILED")])
        c = {x["id"]: x for x in d["checks"]}
        self.assertEqual([x["id"] for x in d["checks"]], ["cargo_test", "lint_all"])
        self.assertEqual(c["cargo_test"]["label"], self.cfg["strings"]["ui"]["check.cargo_test"])
        self.assertEqual(c["cargo_test"]["expected_seconds"], 900)
        self.assertEqual(c["lint_all"]["expected_seconds"], self.cfg["app"]["verify_default_seconds"])
        self.assertEqual((c["cargo_test"]["registered"], c["lint_all"]["registered"]), (True, False))
        self.assertEqual(c["lint_all"]["repeatability"], "needs_reset")
        self.assertEqual(c["cargo_test"]["last"]["verdict"], "FAILED")
        self.assertIsNone(c["lint_all"]["last"])
        self.assertEqual(c["lint_all"]["label"], "Check: lint_all")

    def test_nothing_secret_leaks(self):
        text = json.dumps(checks_api.check_list(self.entry, self.cfg, []))
        for bad in ("secret", "argv", "USERPROFILE", "APPDATA", "ffff", "tool.exe", self.trusted.replace("\\", "\\\\")):
            self.assertNotIn(bad, text)

    def test_no_project_and_unreadable_trusted_folder(self):
        self.assertEqual(checks_api.check_list(None, self.cfg, [])["checks"], [])
        e = dict(self.entry, trusted_dir=os.path.join(self.tmp.name, "nope"))
        self.assertEqual([x["id"] for x in checks_api.check_list(e, self.cfg, [])["checks"]], ["cargo_test"])


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        cfg = load_all()
        cfg["app"].update(projects_path=os.path.join(t, "projects.json"), runs_path=os.path.join(t, "runs.jsonl"), sse_check_seconds=0.05)
        Path(t, "checks.json").write_text(json.dumps([{"check_id": "cargo_test", "argv": ["x"], "repeatability": "repeatable"}]))
        with open(cfg["app"]["projects_path"], "w") as f:
            json.dump({t: {"repo": t, "trusted_dir": t, "expected_manifest_hash": "h", "criteria": ["cargo_test"]}}, f)
        runs.append(cfg["app"]["runs_path"], rec(1), 10 ** 6)
        self.ctx = server.Ctx(cfg, os.path.join(t, "feed.jsonl"), t)
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, host=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"}
        if host:
            h["Host"] = host
        c.request(method, path, body, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_checks_endpoint(self):
        st, body = self.call("GET", "/api/checks")
        d = json.loads(body)
        self.assertEqual(st, 200)
        self.assertEqual((d["checks"][0]["id"], d["checks"][0]["last"]["verdict"]), ("cargo_test", "VERIFIED"))

    def test_runs_endpoint_and_host_check(self):
        self.assertEqual(json.loads(self.call("GET", "/api/runs")[1])["runs"][0]["run_id"], "r1")
        self.assertEqual(self.call("GET", "/api/checks", "evil.example")[0], 403)

    def test_checks_is_read_only(self):
        self.assertEqual(self.call("POST", "/api/checks", body="{}")[0], 404)


class SpellbookConfig(unittest.TestCase):
    def setUp(self):
        self.cfg = load_all()

    def probs(self, cfg):
        return "\n".join(check_app(cfg))

    def test_shipped_config_is_clean(self):
        self.assertEqual(check_app(self.cfg), [])

    def test_hotkeys(self):
        self.assertEqual((self.cfg["hotkeys"]["spellbook"], self.cfg["hotkeys"]["backpack"]), ("p", "b"))
        c = copy.deepcopy(self.cfg)
        c["hotkeys"]["spellbook"] = "l"
        self.assertIn("same key", self.probs(c))

    def test_bad_spell_settings_are_reported(self):
        c = copy.deepcopy(self.cfg)
        c["panels"]["spellbook"]["spells"].append({"id": "cargo_test", "icon": c["panels"]["spellbook"]["icons"]["default"]})
        c["panels"]["spellbook"]["options"]["slots"] = 7
        c["panels"]["spellbook"]["text"]["never"] = "no.such.key"
        c["metaphors"]["spell_marks"]["VERIFIED"]["color"] = "pink"
        del c["metaphors"]["spell_marks"]["NEVER"]
        p = self.probs(c)
        for s in ("listed twice", "whole number of rows", "no.such.key", "'pink'", "spell result mark 'NEVER'"):
            self.assertIn(s, p)

    def test_spells_only_in_spellbook(self):
        c = copy.deepcopy(self.cfg)
        c["panels"]["chat"]["spells"] = []
        self.assertIn("only the spellbook", self.probs(c))

    def test_every_check_label_and_tip_has_text(self):
        ui, tips = self.cfg["strings"]["ui"], self.cfg["strings"]["tooltips"]
        for s in self.cfg["panels"]["spellbook"]["spells"]:
            self.assertIn("check." + s["id"], ui)
            self.assertIn("spell_" + s["id"], tips)
            self.assertTrue(tips["spell_" + s["id"]]["use"])


if __name__ == "__main__":
    unittest.main()


class HiddenCheckLabelTests(unittest.TestCase):
    def test_hidden_check_is_one_unnamed_line(self):
        from vanilla_agents.app import checks_api
        ui = {"check.hidden": "A hidden check", "check.default": "Check: {check}"}
        self.assertEqual(checks_api.label(ui, "hidden-2"), "A hidden check")
