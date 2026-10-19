"""D-414: a quest finished by a normal session shows done and carries the note."""
import unittest

from vanilla_agents.app.config import load_all
from vanilla_agents.app.state import Folder
from vanilla_agents.events.schema import Event


class DoneElsewhere(unittest.TestCase):
    def test_quest_becomes_complete_with_note(self):
        cfg = load_all()
        f = Folder(cfg["strings"]["eli5"] if "strings" in cfg and "eli5" in cfg.get("strings", {}) else cfg.get("templates", cfg), cfg["metaphors"])
        f.apply(Event(id=1, ts=1.0, type="task_created", task="q1", data={"title": "Fix it"}))
        f.apply(Event(id=2, ts=2.0, type="done_elsewhere", task="q1", data={"title": "Fix it", "who": "you", "note": "by a normal session"}))
        t = f.tasks["q1"]
        self.assertEqual((t["status"], t["done_elsewhere"]), ("complete", "by a normal session"))


if __name__ == "__main__":
    unittest.main()


class LeaseExpiry(unittest.TestCase):
    def test_expires_at_is_a_number(self):
        from vanilla_agents.events.errors import EventSchemaError
        from vanilla_agents.events.schema import validate_parts
        validate_parts("lease_acquired", "t", "a", {"resource": "r", "expires_at": 123.5})
        with self.assertRaises(EventSchemaError):
            validate_parts("lease_acquired", "t", "a", {"resource": "r", "expires_at": "soon"})
