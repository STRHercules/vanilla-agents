import unittest

from vanilla_agents.app.worker_level import annotate_agents, difficulty


class WorkerLevel(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(difficulty({"size": "small", "risk": "low"}), {"band": "easy", "legendary": False})
        self.assertEqual(difficulty({"size": "medium", "risk": "low"})["band"], "medium")
        self.assertEqual(difficulty({"size": "big", "risk": "medium"})["band"], "hard")
        self.assertEqual(difficulty({"size": "big", "risk": "high"}), {"band": "hard", "legendary": True})

    def test_unknown_is_medium(self):
        self.assertEqual(difficulty(None)["band"], "medium")
        self.assertEqual(difficulty({})["band"], "medium")

    def test_agents_get_their_quests_band(self):
        snap = {"tasks": [{"id": "q1", "size": "small", "risk": "low"}], "agents": [{"id": "a", "task": "q1"}, {"id": "b", "task": None}]}
        out = annotate_agents(snap)
        self.assertEqual([a["difficulty"]["band"] for a in out["agents"]], ["easy", "medium"])
        self.assertNotIn("difficulty", snap["agents"][0])


if __name__ == "__main__":
    unittest.main()
