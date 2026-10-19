import json
import unittest

from vanilla_agents.events import eli5
from vanilla_agents.events.errors import EventSchemaError, TemplateError
from vanilla_agents.events.schema import EVENT_TYPES, SCHEMA_VERSION, SPEC, Event, from_dict, placeholders, validate_parts


class SchemaTests(unittest.TestCase):
    def test_all_spec_types_listed(self):
        self.assertEqual(set(EVENT_TYPES), set(SPEC))
        self.assertEqual(len(EVENT_TYPES), 40)

    def test_valid_and_invalid_parts(self):
        validate_parts("tool_use", "t1", "a", {"tool": "Edit", "file": "x.py"})
        validate_parts("usage_update", None, None, {"used_pct": 42.5})
        bad = [("nope", {}), ("tool_use", {}), ("tool_use", {"tool": "x", "zzz": "y"}), ("usage_update", {"used_pct": 101}),
               ("usage_update", {"used_pct": True}), ("task_created", {"title": 5}), ("task_created", {"title": "x" * 2001})]
        for et, data in bad:
            with self.assertRaises(EventSchemaError, msg=(et, data)):
                validate_parts(et, None, None, data)
        with self.assertRaises(EventSchemaError):
            validate_parts("agent_idle", 5, None, {})

    def test_from_dict_roundtrip_and_rejects(self):
        ev = Event(3, 1.5, "check_finished", "t", "a", {"check": "c", "verdict": "VERIFIED"})
        self.assertEqual(from_dict(json.loads(json.dumps(ev.to_dict()))), ev)
        d = ev.to_dict()
        for mut in ({"v": SCHEMA_VERSION + 1}, {"id": 0}, {"id": "1"}, {"ts": "x"}, {"extra": 1}):
            with self.assertRaises(EventSchemaError, msg=mut):
                from_dict({**d, **mut})
        with self.assertRaises(EventSchemaError):
            from_dict([])


class Eli5Tests(unittest.TestCase):
    def test_every_event_type_has_a_template_and_only_valid_placeholders(self):
        t = eli5.load_templates()
        self.assertEqual(set(t), set(SPEC))
        for et in SPEC:
            ev = Event(1, 0.0, et, "t1", "Builder", {k: (5 if k == "used_pct" else "x") for k in placeholders(et) - {"agent", "task"}})
            self.assertTrue(eli5.render(ev, t))

    def test_render_fills_and_tidies(self):
        t = eli5.load_templates()
        ev = Event(1, 0.0, "tool_use", None, "Builder", {"tool": "Edit", "file": "a.py"})
        self.assertEqual(eli5.render(ev, t), "Builder used Edit a.py")
        self.assertIn("5 percent", eli5.render(Event(2, 0.0, "usage_update", None, None, {"used_pct": 5}), t))

    def test_unknown_placeholder_is_a_clear_error(self):
        t = eli5.load_templates()
        for bad in ("Hi {bogus}", "Hi {agent", "Hi }"):
            with self.assertRaises(TemplateError) as cm:
                eli5.check_templates({**t, "agent_idle": bad})
            self.assertEqual(cm.exception.code, "eli5_unknown_placeholder")
        with self.assertRaises(TemplateError) as cm:
            eli5.render(Event(1, 0.0, "agent_idle", None, None, {}), {"agent_idle": "{bogus}"})
        self.assertEqual(cm.exception.code, "eli5_unknown_placeholder")

    def test_missing_and_unknown_type_templates(self):
        t = eli5.load_templates()
        with self.assertRaises(TemplateError) as cm:
            eli5.check_templates({k: v for k, v in t.items() if k != "tool_use"})
        self.assertEqual(cm.exception.code, "eli5_missing")
        with self.assertRaises(TemplateError) as cm:
            eli5.check_templates({**t, "mystery": "x"})
        self.assertEqual(cm.exception.code, "eli5_unknown_type")


if __name__ == "__main__":
    unittest.main()
