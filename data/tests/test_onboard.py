import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents.app import route, server   # noqa: E402
from vanilla_agents.app.config import load_all   # noqa: E402
from vanilla_agents.app.verify_job import Refused   # noqa: E402
from vanilla_agents.contracts import ValidationError   # noqa: E402
from vanilla_agents.onboard import setup   # noqa: E402
from vanilla_agents.onboard.detect import detect   # noqa: E402
from vanilla_agents.verify.manifest import require_manifest   # noqa: E402
from tests.test_app_verify import Base, bundle   # noqa: E402

SECRET = "ZX-SECRET-HOLDOUT-9931"


def git_repo(root, name, files=None):
    p = Path(root) / name
    p.mkdir(parents=True)
    for rel, text in (files or {}).items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    return str(p)


class Detect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def d(self, files):
        p = Path(self.tmp.name) / ("r%d" % len(list(Path(self.tmp.name).iterdir())))
        p.mkdir()
        for rel, text in files.items():
            (p / rel).parent.mkdir(parents=True, exist_ok=True)
            (p / rel).write_text(text, encoding="utf-8")
        return detect(p)

    def ids(self, files):
        return [c["check_id"] for c in self.d(files)["checks"]]

    def test_each_type(self):
        self.assertEqual(self.ids({"Cargo.toml": "[package]"}), ["cargo_test"])
        self.assertEqual(self.ids({"package.json": json.dumps({"scripts": {"test": "jest"}})}), ["npm_test"])
        self.assertEqual(self.ids({"go.mod": "module x"}), ["go_test"])
        self.assertEqual(self.ids({"pytest.ini": "[pytest]"}), ["pytest"])
        self.assertEqual(self.ids({"tests/test_a.py": "x"}), ["unittest"])
        self.assertEqual(self.ids({"Makefile": "test:\n\techo ok\n"}), ["make_test"])

    def test_unknown_and_stubs_give_no_checks(self):
        self.assertEqual(self.d({"notes.txt": "hi"}), {"type": "unknown", "checks": [], "protected": [], "full_paths": []})
        stub = json.dumps({"scripts": {"test": "echo no test specified && exit 1"}})
        self.assertEqual(self.ids({"package.json": stub}), [])
        self.assertEqual(self.ids({"package.json": "{broken"}), [])

    def test_makefile_only_when_nothing_better(self):
        self.assertEqual(self.ids({"Cargo.toml": "x", "Makefile": "test:\n"}), ["cargo_test"])

    def test_mixed_and_protected(self):
        d = self.d({"Cargo.toml": "x", "package.json": json.dumps({"scripts": {"test": "mocha"}})})
        self.assertEqual((d["type"], [c["check_id"] for c in d["checks"]]), ("rust+npm", ["cargo_test", "npm_test"]))
        self.assertIn("tests/**", d["protected"])
        self.assertEqual(len(d["protected"]), len(set(d["protected"])))


class OnboardFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.reg, self.troot = os.path.join(t, "work", "projects.json"), os.path.join(t, "trusted")
        self.a = git_repo(t, "alpha", {"Cargo.toml": "x"})
        self.b = git_repo(t, "beta", {"package.json": json.dumps({"scripts": {"test": "jest"}})})

    def tearDown(self):
        self.tmp.cleanup()

    def test_entry_hash_db_and_exclude(self):
        s = setup.onboard(self.a, self.reg, self.troot)
        e = setup.load_registry(self.reg)[self.a]
        self.assertEqual((s["checks"], s["already"], e["name"], e["criteria"]), (["cargo_test"], False, "alpha", ["cargo_test"]))
        self.assertEqual(e["trusted_dir"], os.path.join(self.troot, "alpha"))
        self.assertEqual(require_manifest(e["trusted_dir"], expected_hash=e["expected_manifest_hash"]), e["expected_manifest_hash"])
        self.assertTrue(Path(self.a, "state", "db", "vanilla.db").is_file())
        self.assertIn("/state/", Path(self.a, ".git", "info", "exclude").read_text().splitlines())
        self.assertIn("Cargo.toml", e["route"]["full_paths"])

    def test_idempotent(self):
        setup.onboard(self.a, self.reg, self.troot)
        before = Path(self.reg).read_text()
        self.assertTrue(setup.onboard(self.a, self.reg, self.troot)["already"])
        self.assertEqual(Path(self.reg).read_text(), before)

    def test_trusted_folders_do_not_mix(self):
        setup.onboard(self.a, self.reg, self.troot)
        setup.onboard(self.b, self.reg, self.troot)
        r = setup.load_registry(self.reg)
        ea, eb = r[self.a], r[self.b]
        self.assertNotEqual(ea["trusted_dir"], eb["trusted_dir"])
        ids = lambda e: [c["check_id"] for c in json.loads(Path(e["trusted_dir"], "checks.json").read_text())]
        self.assertEqual((ids(ea), ids(eb)), (["cargo_test"], ["npm_test"]))
        self.assertNotEqual(ea["expected_manifest_hash"], eb["expected_manifest_hash"])
        with self.assertRaises(Exception):      # a project's fingerprint never vouches for another project's folder
            require_manifest(ea["trusted_dir"], expected_hash=eb["expected_manifest_hash"])

    def test_same_name_gets_its_own_folder(self):
        other = git_repo(self.tmp.name, "x/alpha", {"go.mod": "m"})
        setup.onboard(self.a, self.reg, self.troot)
        setup.onboard(other, self.reg, self.troot)
        r = setup.load_registry(self.reg)
        self.assertEqual((r[self.a]["name"], r[other]["name"]), ("alpha", "alpha-2"))

    def test_refusals(self):
        with self.assertRaises(setup.OnboardError):
            setup.onboard(os.path.join(self.tmp.name, "nope"), self.reg, self.troot)
        plain = Path(self.tmp.name, "plain")
        plain.mkdir()
        with self.assertRaises(setup.OnboardError):
            setup.onboard(plain, self.reg, self.troot)
        with self.assertRaises(setup.OnboardError):     # trusted folder inside the project would let the project rewrite it
            setup.onboard(self.a, self.reg, os.path.join(self.a, "t"))
        self.assertFalse(os.path.exists(self.reg))

    def test_unknown_type_is_agent_only(self):
        c = git_repo(self.tmp.name, "gamma", {"notes.txt": "x"})
        self.assertEqual(setup.onboard(c, self.reg, self.troot)["checks"], [])
        self.assertEqual(setup.load_registry(self.reg)[c]["criteria"], [])

    def test_regen_keeps_checks_and_updates_hash(self):
        setup.onboard(self.a, self.reg, self.troot)
        e = setup.load_registry(self.reg)[self.a]
        Path(e["trusted_dir"], "MANIFEST.json").write_text("{}")      # as if the checker code changed
        h = setup.rebuild(self.reg, self.a)
        self.assertEqual(h, setup.load_registry(self.reg)[self.a]["expected_manifest_hash"])
        require_manifest(e["trusted_dir"], expected_hash=h)


class Holdout(unittest.TestCase):
    DEF = {"check_id": "secret_check", "argv": ["python", "-c", "print('%s')" % SECRET], "rule": {"type": "output_contains", "pattern": SECRET}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.reg, self.troot = os.path.join(t, "projects.json"), os.path.join(t, "trusted")
        self.a = git_repo(t, "alpha", {"Cargo.toml": "x"})
        setup.onboard(self.a, self.reg, self.troot)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_regenerates_hash_and_stays_in_trusted_folder(self):
        old = setup.load_registry(self.reg)[self.a]["expected_manifest_hash"]
        r = setup.add_holdout(self.reg, "alpha", self.DEF)       # by short name
        e = setup.load_registry(self.reg)[self.a]
        self.assertEqual((r["holdouts"], r["hash"] != old, e["expected_manifest_hash"]), (1, True, r["hash"]))
        self.assertIn(SECRET, Path(e["trusted_dir"], "holdout.json").read_text())
        require_manifest(e["trusted_dir"], expected_hash=r["hash"])
        self.assertNotIn(SECRET, Path(self.reg).read_text())
        self.assertNotIn(SECRET, json.dumps(r))
        self.assertEqual(setup.add_holdout(self.reg, self.a, dict(self.DEF, check_id="b2"))["holdouts"], 2)
        self.assertEqual(setup.add_holdout(self.reg, self.a, self.DEF)["holdouts"], 2)      # same id replaces

    def test_regen_keeps_holdouts_pinned(self):
        setup.add_holdout(self.reg, self.a, self.DEF)
        h = setup.rebuild(self.reg, self.a)
        e = setup.load_registry(self.reg)[self.a]
        self.assertIn("trusted:holdout.json", json.loads(Path(e["trusted_dir"], "MANIFEST.json").read_text())["files"])
        require_manifest(e["trusted_dir"], expected_hash=h)

    def test_bad_definitions_refused(self):
        with self.assertRaises(ValidationError):
            setup.add_holdout(self.reg, self.a, {"check_id": "x", "argv": ["a"], "rule": {"type": "nope"}})
        with self.assertRaises(ValidationError):
            setup.add_holdout(self.reg, self.a, dict(self.DEF, check_id="cargo_test"))
        with self.assertRaises(ValidationError):
            setup.add_holdout(self.reg, self.a, dict(self.DEF, check_id="hidden-1"))
        with self.assertRaises(setup.OnboardError):
            setup.add_holdout(self.reg, os.path.join(self.tmp.name, "other"), self.DEF)

    def test_content_never_in_any_payload(self):
        setup.add_holdout(self.reg, self.a, self.DEF)
        cfg = load_all()
        cfg["app"].update(projects_path=self.reg, trusted_root=self.troot, runs_path=os.path.join(self.tmp.name, "runs.jsonl"))
        ctx = server.Ctx(cfg, os.path.join(self.tmp.name, "feed.jsonl"), self.a, settings_file=os.path.join(self.tmp.name, "s.json"))
        srv = server.make_server(ctx, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            seen = []
            for path in ("/api/projects", "/api/checks", "/api/state", "/api/runs", "/api/zones", "/api/config"):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                c.request("GET", path)
                seen.append(c.getresponse().read().decode())
            self.assertEqual([x for x in seen if SECRET in x or "secret_check" in x], [])
        finally:
            ctx.stop.set()
            srv.shutdown()
            srv.server_close()


class StrictLookup(Base):
    def test_unknown_and_second_project_refused(self):
        other = os.path.join(self.tmp.name, "other")
        self.assertIsNone(self.job(bundle("VERIFIED")).entry(other))        # one entry in the registry, a different project: no fallback
        with self.assertRaisesRegex(Refused, "not set up"):
            self.job(bundle("VERIFIED")).start(other)
        with self.assertRaisesRegex(Refused, "not set up"):
            self.job(bundle("VERIFIED")).start(None)
        self.assertEqual(self.calls, [])

    def test_each_project_gets_its_own_entry(self):
        other = os.path.join(self.tmp.name, "other")
        reg = json.loads(Path(self.cfg["app"]["projects_path"]).read_text())
        reg[other] = {"repo": other, "trusted_dir": other, "expected_manifest_hash": "h2", "criteria": ["npm_test"], "default_base_ref": "HEAD", "name": "other"}
        Path(self.cfg["app"]["projects_path"]).write_text(json.dumps(reg))
        j = self.job(bundle("VERIFIED"))
        self.assertEqual((j.entry(self.tmp.name)["expected_manifest_hash"], j.entry(other)["expected_manifest_hash"]), ("h", "h2"))
        self.assertEqual(j.entry(other.upper() if os.name == "nt" else other)["name"], "other")

    def test_no_checks_refused_plainly(self):
        reg = json.loads(Path(self.cfg["app"]["projects_path"]).read_text())
        reg[self.tmp.name]["criteria"] = []
        Path(self.cfg["app"]["projects_path"]).write_text(json.dumps(reg))
        with self.assertRaisesRegex(Refused, "No automatic checks"):
            self.job(bundle("VERIFIED")).start(self.tmp.name)


class RouteOverride(unittest.TestCase):
    BASE = {"max_fast_files": 5, "max_fast_lines": 200, "full_paths": ["Cargo.toml"], "trusted_paths": ["x/**"]}

    def test_merge_keeps_only_valid_keys(self):
        m = route.merge_rules(self.BASE, {"max_fast_files": 1, "full_paths": ["package.json"], "trusted_paths": [], "max_fast_lines": 0, "bogus": 1})
        self.assertEqual(m, dict(self.BASE, max_fast_files=1, full_paths=["package.json"]))
        self.assertEqual(route.merge_rules(self.BASE, None), self.BASE)
        self.assertEqual(route.merge_rules(self.BASE, {"max_fast_files": True}), self.BASE)

    def test_override_changes_the_route_for_that_project_only(self):
        ch = [{"path": "package.json", "lines": 1}]
        self.assertEqual(route.classify(ch, self.BASE)["route"], "fast")
        self.assertEqual(route.classify(ch, route.merge_rules(self.BASE, {"full_paths": ["package.json"]}))["route"], "full")



class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        cfg = load_all()
        cfg["app"].update(projects_path=os.path.join(t, "projects.json"), trusted_root=os.path.join(t, "trusted"), runs_path=os.path.join(t, "runs.jsonl"))
        self.repo = git_repo(t, "demo", {"go.mod": "module demo"})
        self.ctx = server.Ctx(cfg, os.path.join(t, "feed.jsonl"), None, settings_file=os.path.join(t, "s.json"))
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, body, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/api/onboard", json.dumps(body), {"Content-Type": "application/json", "X-Vanilla-Key": self.ctx.key, **({"Origin": origin} if origin else {})})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_onboard_lists_and_opens(self):
        st, body = self.post({"path": self.repo})
        self.assertEqual(st, 200)
        self.assertEqual((body["onboarded"]["name"], body["onboarded"]["checks"]), ("demo", ["go_test"]))
        self.assertEqual(body["project"]["path"], self.repo)
        self.assertEqual([p["name"] for p in self.ctx.projects()], ["demo"])
        self.assertEqual(self.ctx.verify.entry(self.repo)["criteria"], ["go_test"])
        self.assertTrue(self.post({"path": self.repo})[1]["onboarded"]["already"])

    def test_bad_input_and_foreign_origin(self):
        self.assertEqual(self.post({"path": os.path.join(self.tmp.name, "missing")})[0], 400)
        self.assertEqual(self.post({"path": 5})[0], 400)
        self.assertEqual(self.post({"path": self.repo}, "http://evil.example")[0], 403)
        self.assertFalse(os.path.exists(self.ctx.verify.registry_path))


if __name__ == "__main__":
    unittest.main()
