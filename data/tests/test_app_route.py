"""Small/big work classifier (D-300..D-309): pure rules, wiring in Verify now, and the guarantee that fast never skips holdouts for trusted work."""
import json
import os
import tempfile
import unittest

from vanilla_agents.app import route, runs
from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed
from tests.test_app_verify import Base, bundle

RULES = {"max_fast_files": 3, "max_fast_lines": 50, "full_paths": ["Cargo.toml", "*.lock"], "trusted_paths": ["vanilla_agents/verify/**", "tests/**"]}


def ch(*pairs):
    return [{"path": p, "lines": n} for p, n in pairs]


class Classify(unittest.TestCase):
    def test_small_change_is_fast_without_holdouts(self):
        r = route.classify(ch(("src/a.rs", 5), ("src/b.rs", 5)), RULES)
        self.assertEqual((r["route"], r["holdouts"], r["reasons"][0][0]), ("fast", False, "small"))

    def test_unknown_changes_are_full(self):
        r = route.classify(None, RULES)
        self.assertEqual((r["route"], r["holdouts"], r["reasons"][0][0]), ("full", True, "unknown"))

    def test_big_by_files_or_lines(self):
        self.assertEqual(route.classify(ch(*[("f%d" % i, 1) for i in range(4)]), RULES)["reasons"], [("files", {"files": 4})])
        self.assertEqual(route.classify(ch(("a", 51)), RULES)["reasons"], [("lines", {"lines": 51})])

    def test_build_files_force_full_even_in_subfolders(self):
        for p in ("Cargo.toml", "crates/x/Cargo.toml", "deep/Cargo.lock"):
            self.assertEqual(route.classify(ch((p, 1)), RULES)["route"], "full")

    def test_trusted_code_is_never_fast_and_keeps_holdouts(self):
        for p in ("vanilla_agents/verify/verifier.py", "tests/test_x.py"):
            r = route.classify(ch((p, 1)), RULES)
            self.assertEqual((r["route"], r["holdouts"], r["reasons"][0][0]), ("full", True, "trusted"))
        r = route.classify(ch(("scripts/x.py", 1)), RULES, trusted=["scripts/**"])   # protected.json of the trusted folder counts too
        self.assertEqual((r["route"], r["holdouts"]), ("full", True))

    def test_empty_diff_is_fast(self):
        self.assertEqual(route.classify([], RULES)["route"], "fast")

    def test_git_changes_without_a_commit_is_unknown(self):
        self.assertIsNone(route.git_changes(".", None, "a" * 40))
        self.assertIsNone(route.git_changes(tempfile.gettempdir(), "b" * 40, "a" * 40))     # not a git folder


class Wiring(Base):
    def setUp(self):
        super().setUp()
        self.cfg["app"]["route"] = dict(RULES)

    def run_with(self, changes, last="c" * 40, entry_extra=None):
        if last:
            runs.append(self.cfg["app"]["runs_path"], {"run_id": "r0", "ts": 1, "commit": last, "verdict": "VERIFIED", "checks": [], "title": "Verify " + os.path.basename(os.path.dirname(self.tmp.name)), "reason": ""}, 10 ** 6)
        if entry_extra:
            reg = json.load(open(self.cfg["app"]["projects_path"]))
            reg[self.tmp.name].update(entry_extra)
            json.dump(reg, open(self.cfg["app"]["projects_path"], "w"))
        job = self.job(bundle("VERIFIED"))
        job.changes = lambda repo, since, head: (self.seen.append((since, head)) or changes)
        self.run_job(job)
        return [e for e in Feed(self.feed_path).read_since(0, 100) if e.type == "route_chosen"][0]

    def test_small_change_takes_fast_route_and_skips_holdouts(self):
        self.seen = []
        ev = self.run_with(ch(("src/a.rs", 3)))
        self.assertEqual((ev.data["route"], self.calls[0]["holdouts"], self.seen), ("fast", False, [("c" * 40, "a" * 40)]))
        self.assertIn("small change", ev.data["why"])

    def test_trusted_change_takes_full_route_with_holdouts(self):
        self.seen = []
        ev = self.run_with(ch(("tests/t.py", 1)))
        self.assertEqual((ev.data["route"], self.calls[0]["holdouts"]), ("full", True))

    def test_no_earlier_good_check_means_full(self):
        self.seen = []
        ev = self.run_with(ch(("src/a.rs", 1)), last=None)
        self.assertEqual((ev.data["route"], self.calls[0]["holdouts"], self.seen), ("full", True, []))

    def test_fast_route_runs_only_the_fast_checks(self):
        self.seen = []
        self.run_with(ch(("src/a.rs", 1)), entry_extra={"criteria": ["cargo_test", "slow"], "fast_criteria": ["cargo_test"]})
        self.assertEqual([c["check_id"] for c in self.calls[0]["criteria"]], ["cargo_test"])

    def test_full_route_runs_every_check(self):
        self.seen = []
        self.run_with(None, entry_extra={"criteria": ["cargo_test", "slow"], "fast_criteria": ["cargo_test"]})
        self.assertEqual([c["check_id"] for c in self.calls[0]["criteria"]], ["cargo_test", "slow"])


class Settings(unittest.TestCase):
    def test_shipped_rules_are_clean_and_empty_trusted_list_is_refused(self):
        cfg = load_all()
        self.assertEqual(check_app(cfg), [])
        cfg["app"]["route"]["trusted_paths"] = []
        self.assertTrue(any("trusted_paths" in p for p in check_app(cfg)))
        cfg = load_all()
        cfg["app"]["route"]["max_fast_files"] = 0
        self.assertTrue(any("max_fast_files" in p for p in check_app(cfg)))
