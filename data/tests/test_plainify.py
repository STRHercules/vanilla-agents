"""Plain-language quests (D-360..D-369): glossary, needs_plain, overrides, parser skip rows and ignore_docs. Deterministic, temp dirs only."""
import json
import tempfile
import unittest
from pathlib import Path

from vanilla_agents.onboard import idea, plainify
from vanilla_agents.app.config import CONFIG_DIR

G = json.loads((CONFIG_DIR / "glossary.json").read_text(encoding="utf-8"))["terms"]


def repo(files, t=None):
    d = tempfile.TemporaryDirectory()
    t.addCleanup(d.cleanup)
    for n, t in files.items():
        (Path(d.name) / n).write_text(t, encoding="utf-8")
    return d


class Plainify(unittest.TestCase):
    def test_known_jargon_swapped(self):
        t, needs = plainify.plainify("Load every WMO and M2 with shaders", G)
        self.assertEqual(t, "Load every building model and character or creature model with lighting and surface effect")
        self.assertFalse(needs)

    def test_unknown_jargon_flagged(self):
        for s in ("Fix `frobnicate_tick`", "Port the QZX handler", "do thing_two now", "Tune fooBar"):
            self.assertTrue(plainify.plainify(s, G)[1], s)

    def test_plain_text_not_flagged(self):
        self.assertFalse(plainify.plainify("Show names over heads", G)[1])

    def test_glossary_is_clean(self):
        for k in ("WMO", "M2", "DB2", "GFID", "SMSG", "CMSG", "ADT", "doodad", "wasm", "gateway", "shader", "LOD"):
            self.assertIn(k, G)
        self.assertTrue(all(v.strip() and v.lower() != k.lower() for k, v in G.items()))


ROAD = """# Roadmap
1. Rework the WMO loader
2. Unexplained FROB_THING
3. Done since: everything; NOT done: more
4. what exists: a thing; others missing
"""


class Plan(unittest.TestCase):
    def test_skip_status_rows(self):
        d = repo({"ROADMAP.md": ROAD}, self)
        self.assertEqual([t for t, _ in idea.quests(d.name)], ["Rework the WMO loader", "Unexplained FROB_THING"])

    def test_ignore_docs(self):
        d = repo({"ROADMAP.md": "1. Real work item\n", "OVERVIEW.md": "# Next\n- Old overview item\n"}, self)
        self.assertEqual(len(idea.quests(d.name)), 2)
        self.assertEqual([t for t, _ in idea.quests(d.name, ["overview.md"])], ["Real work item"])

    def test_plain_details_and_needs_count(self):
        d = repo({"ROADMAP.md": ROAD}, self)
        items = plainify.plan(d.name, "p", None, G)
        self.assertEqual(items[0]["title"], "Rework the building model loader")
        self.assertEqual(items[0]["details"], "Rework the WMO loader")
        self.assertEqual(plainify.needs_plain_count(items), 1)

    def test_overrides(self):
        d = repo({"ROADMAP.md": ROAD}, self)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        qid = idea.quest_id("p", "Unexplained FROB_THING")
        (Path(root.name) / "p").mkdir()
        (Path(root.name) / "p" / "quests.json").write_text(json.dumps({"ignore_docs": [], qid: {"plain": "Fix the frob", "why": "It works", "size": "big", "risk": "high"}, "bad": 3}), encoding="utf-8")
        items = plainify.plan(d.name, "p", root.name, G)
        q = items[1]
        self.assertEqual((q["title"], q["why"], q["size"], q["risk"], q["needs_plain"]), ("Fix the frob", "It works", "big", "high", False))
        self.assertEqual(plainify.needs_plain_count(items), 0)

    def test_overrides_bad_file_and_values(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        (Path(root.name) / "p").mkdir()
        (Path(root.name) / "p" / "quests.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(plainify.load_overrides(root.name, "p"), ({}, []))
        d = repo({"ROADMAP.md": "1. Some work item\n"}, self)
        qid = idea.quest_id("p", "Some work item")
        (Path(root.name) / "p" / "quests.json").write_text(json.dumps({qid: {"plain": "  ", "size": "huge", "risk": "x"}}), encoding="utf-8")
        q = plainify.plan(d.name, "p", root.name, G)[0]
        self.assertEqual((q["title"], q["size"], q["risk"]), ("Some work item", "", ""))

    def test_seed_carries_fields(self):
        d = repo({"ROADMAP.md": ROAD}, self)
        got = []
        feed = type("F", (), {"append": lambda s, t, task=None, data=None: got.append((task, data))})()
        n = idea.seed_quests(d.name, "p", feed, set(), plainify.plan(d.name, "p", None, G))
        self.assertEqual(n, 2)
        self.assertEqual(got[0][1]["details"], "Rework the WMO loader")
        self.assertEqual(got[1][1]["needs_plain"], "1")


if __name__ == "__main__":
    unittest.main()
