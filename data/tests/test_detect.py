import unittest

from vanilla_agents.app import detect

LOG = [("c3", "tidy"), ("c2", "Finish proj:q:save-state"), ("c1", "start")]


class Finished(unittest.TestCase):
    def test_named_commit_and_later_pass(self):
        self.assertEqual(list(detect.finished(["proj:q:save-state"], LOG, [{"commit": "c3", "verdict": "VERIFIED"}])), ["proj:q:save-state"])

    def test_pass_on_the_commit_itself(self):
        self.assertTrue(detect.finished(["proj:q:save-state"], LOG, [{"commit": "c2", "verdict": "VERIFIED"}]))

    def test_no_pass_or_pass_only_before_means_not_done(self):
        self.assertEqual(detect.finished(["proj:q:save-state"], LOG, []), {})
        self.assertEqual(detect.finished(["proj:q:save-state"], LOG, [{"commit": "c1", "verdict": "VERIFIED"}]), {})
        self.assertEqual(detect.finished(["proj:q:save-state"], LOG, [{"commit": "c3", "verdict": "fail"}]), {})

    def test_unnamed_quest_is_not_done(self):
        self.assertEqual(detect.finished(["proj:q:other"], LOG, [{"commit": "c3", "verdict": "VERIFIED"}]), {})


if __name__ == "__main__":
    unittest.main()
