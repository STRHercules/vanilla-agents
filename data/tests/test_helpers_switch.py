import unittest

from vanilla_agents.app import settings


class HelpersSwitch(unittest.TestCase):
    def test_setting_validates_and_defaults_off(self):
        sd = {"sounds": [], "events": {}, "master": {}}
        self.assertEqual(settings.validate({"helpers": "ask"}, sd)["helpers"], "ask")
        with self.assertRaises(ValueError):
            settings.validate({"helpers": "yes"}, sd)
        self.assertEqual(settings.merge({"master": {}, "events": {}}, {})["helpers"], "off")


if __name__ == "__main__":
    unittest.main()
