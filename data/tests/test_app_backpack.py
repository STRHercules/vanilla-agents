"""Backpack: files agents touched (D-243), item-quality mapping and config rules (D-242). No real runs."""
import copy
import os
import tempfile
import unittest

from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import load_all
from vanilla_agents.app.state import AppState, Folder
from vanilla_agents.events.feed import Feed


def tool(f, name, file, agent="builder"):
    f.append("tool_use", agent=agent, task="t1", data={"tool": name, "file": file})


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load_all()
        self.feed = Feed(os.path.join(self.tmp.name, "feed.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def files(self):
        st = AppState(self.cfg, self.feed.path, None)
        return [x["file"] for x in st.snapshot()["files"]]

    def test_newest_first_deduplicated_and_only_changing_tools(self):
        tool(self.feed, "Edit", "a.rs")
        tool(self.feed, "Read", "read_only.rs")
        tool(self.feed, "Write", "b.rs")
        tool(self.feed, "Edit", "a.rs")
        self.feed.append("tool_use", agent="builder", task="t1", data={"tool": "Bash", "command": "ls"})
        self.assertEqual(self.files(), ["a.rs", "b.rs"])

    def test_capped(self):
        for i in range(10):
            tool(self.feed, "Edit", "f%d.rs" % i)
        self.cfg["panels"]["backpack"]["options"]["max_files"] = 4
        self.assertEqual(self.files(), ["f9.rs", "f8.rs", "f7.rs", "f6.rs"])

    def test_folder_default_without_panel_settings(self):
        f = Folder({}, {"events": {}}, files_max=2)
        self.assertEqual(f.files, [])


class BackpackConfig(unittest.TestCase):
    def setUp(self):
        self.cfg = load_all()

    def probs(self, cfg):
        return "\n".join(check_app(cfg))

    def test_quality_mapping_is_complete_and_matches_the_spec(self):
        q = self.cfg["metaphors"]["item_quality"]
        self.assertEqual(set(q), {"VERIFIED", "FAILED", "BLOCKED", "RUNNING", "FILE"})
        self.assertEqual(q["VERIFIED"]["border"], "green")
        self.assertEqual((q["FAILED"]["border"], q["FAILED"]["color"]), ("grey", "red"))
        self.assertEqual(q["BLOCKED"]["border"], "grey")
        self.assertEqual(q["RUNNING"]["border"], "white")
        # every checker outcome is claimed at most once
        outs = [o for v in q.values() for o in v["outcomes"]]
        self.assertEqual(len(outs), len(set(outs)))

    def test_bad_quality_settings_are_reported(self):
        c = copy.deepcopy(self.cfg)
        c["metaphors"]["item_quality"]["VERIFIED"]["border"] = "purple"
        c["metaphors"]["item_quality"]["FAILED"]["outcomes"] = ["NOPE"]
        del c["metaphors"]["item_quality"]["FILE"]
        c["panels"]["backpack"]["options"]["columns"] = 0
        c["panels"]["backpack"]["icons"]["file"] = "interface/icons/not_baked.png"
        p = self.probs(c)
        for s in ("'purple'", "NOPE", "item quality 'FILE'", "at least 1", "not_baked.png"):
            self.assertIn(s, p)

    def test_slots_are_whole_rows(self):
        c = copy.deepcopy(self.cfg)
        c["panels"]["backpack"]["options"]["slots"] = 26
        self.assertIn("whole number of rows", self.probs(c))

    def test_all_item_strings_exist_and_use_known_words(self):
        ui = self.cfg["strings"]["ui"]
        for q in self.cfg["metaphors"]["item_quality"].values():
            self.assertIn(q["word"], ui)
        for k in ("item.run_what", "item.file_what", "item.running_what", "item.when", "item.took", "item.result_verified", "item.result_failed",
                  "item.result_blocked", "item.result_running", "item.result_file", "time.now", "time.minutes", "duration.seconds"):
            self.assertIn(k, ui)

    def test_icons_are_baked(self):
        a = self.cfg["app"]["assets_dir"]
        for rel in list(self.cfg["panels"]["backpack"]["icons"].values()) + list(self.cfg["panels"]["spellbook"]["icons"].values()):
            self.assertTrue(os.path.isfile(os.path.join(a, rel)), rel)


if __name__ == "__main__":
    unittest.main()
