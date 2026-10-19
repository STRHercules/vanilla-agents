"""Raid frames and buff/debuff icons (D-270..D-276): pure logic, config rules, server snapshot."""
import copy
import unittest

from vanilla_agents.app import auras
from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import load_all


def agent(i, state="idle"):
    return {"id": i, "role": "builder", "state": state}


def task(i, status, owner):
    return {"id": i, "status": status, "agent": owner}


def snap(agents=(), tasks=(), leases=(), verify=None):
    return {"agents": list(agents), "tasks": list(tasks), "leases": list(leases), "verify": verify or {}}


def ids(lst):
    return [(a["id"], a["count"]) for a in lst]


class Compute(unittest.TestCase):
    def test_empty_session_has_nothing(self):
        r = auras.compute(snap())
        self.assertEqual((r["raid"], r["player"], r["raid_more"]), ([], [], 0))

    def test_bar_is_quests_passed_of_owned(self):
        r = auras.compute(snap([agent("a")], [task("1", "complete", "a"), task("2", "progress", "a"), task("3", "complete", "b")]))["raid"][0]
        self.assertEqual((r["passed"], r["total"], r["pct"]), (1, 2, 50))

    def test_agent_without_quest_is_zero_not_a_guess(self):
        r = auras.compute(snap([agent("a", "working")]))["raid"][0]
        self.assertEqual((r["total"], r["pct"], ids(r["auras"])), (0, 0, [("running", 1)]))

    def test_agent_failed_blocked_counts_and_no_verified(self):
        r = auras.compute(snap([agent("a")], [task("1", "failed", "a"), task("2", "failed", "a"), task("3", "blocked", "a"), task("4", "complete", "a")]))["raid"][0]
        self.assertEqual(ids(r["auras"]), [("failed", 2), ("blocked", 1)])

    def test_verified_only_when_every_owned_quest_passed(self):
        self.assertEqual(ids(auras.compute(snap([agent("a")], [task("1", "complete", "a")]))["raid"][0]["auras"]), [("verified", 1)])

    def test_paused_marks_agents_waiting_for_the_checker_only(self):
        s = snap([agent("a"), agent("b")], [task("1", "ready", "a"), task("2", "progress", "b")], verify={"paused": True})
        r = auras.compute(s)["raid"]
        self.assertEqual([ids(x["auras"]) for x in r], [[("paused", 1)], []])
        s["verify"] = {}
        self.assertEqual(ids(auras.compute(s)["raid"][0]["auras"]), [])

    def test_player_icons_in_fixed_order(self):
        s = snap([agent("a")], [task("1", "failed", "a"), task("2", "blocked", None)], ["db"], {"running": True, "paused": True})
        self.assertEqual(ids(auras.compute(s)["player"]), [("running", 1), ("paused", 1), ("failed", 1), ("blocked", 1), ("lease", 1)])

    def test_player_verified_when_all_quests_done(self):
        self.assertEqual(ids(auras.compute(snap(tasks=[task("1", "complete", None)]))["player"]), [("verified", 1)])
        self.assertEqual(auras.compute(snap(tasks=[task("1", "complete", None), task("2", "new", None)]))["player"], [])

    def test_lease_timer_is_the_soonest_expiry(self):
        s = snap(leases=["a", "b"])
        s["lease_expires"] = [200.0, 100.0]
        self.assertEqual(auras.compute(s)["player"][0], {"id": "lease", "count": 2, "expires_at": 100.0})
        self.assertNotIn("expires_at", auras.compute(snap(leases=["a"]))["player"][0])      # unknown expiry: no timer, never a guess

    def test_leases_show_count(self):
        self.assertEqual(ids(auras.compute(snap(leases=["a", "b"]))["player"]), [("lease", 2)])

    def test_max_frames_and_more(self):
        r = auras.compute(snap([agent(str(i)) for i in range(5)]), 3)
        self.assertEqual((len(r["raid"]), r["raid_more"]), (3, 2))


class Config(unittest.TestCase):
    def setUp(self):
        self.cfg = load_all()

    def test_shipped_config_is_valid(self):
        self.assertEqual([p for p in check_app(self.cfg) if "aura" in p or "raid" in p], [])

    def test_every_aura_name_has_a_definition(self):
        self.assertEqual(sorted(self.cfg["metaphors"]["auras"]), sorted(auras.ORDER))

    def test_bad_kind_unknown_aura_and_zero_columns_are_caught(self):
        c = copy.deepcopy(self.cfg)
        c["metaphors"]["auras"]["running"]["kind"] = "banana"
        c["metaphors"]["auras"]["extra"] = dict(c["metaphors"]["auras"]["lease"])
        c["panels"]["raid_frames"]["options"]["columns"] = 0
        text = "\n".join(check_app(c))
        for want in ("banana", "extra", "at least 1"):
            self.assertIn(want, text)

    def test_missing_icon_file_is_caught(self):
        c = copy.deepcopy(self.cfg)
        c["metaphors"]["auras"]["failed"]["icon"] = "interface/icons/not_there.png"
        self.assertTrue(any("not_there" in p for p in check_app(c)))


if __name__ == "__main__":
    unittest.main()
