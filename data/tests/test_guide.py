"""V-037 feature guide: guide.json is complete and points only at real things (sentences, windows, hotkeys, settings), every window
has a topic and every hotkey is in the Keyboard reference, every option in the Options window explains itself (tooltip + line),
docs/GUIDE.md matches its generator, and the guide's colours are readable on the parchment."""
import copy
import os
import re
import sys
import unittest

from vanilla_agents.app import check_app
from vanilla_agents.app import schema as S
from vanilla_agents.app.config import ROOT, load_all
from tests.test_a11y import PARCHMENT, contrast

sys.path.insert(0, os.path.join(str(ROOT), "scripts"))
import make_guide  # noqa: E402

WEB = ROOT / "app" / "web"
METAL = "#15120e"      # the dark inside of a metal window (Options)


def js(*parts):
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


def topics(cfg):
    return [t for c in cfg["guide"]["chapters"] for t in c["topics"]]


def own_settings():
    """Ids of Vanilla Agents' own options in the Options window: every VA_* var (gamesettings.js + wsettings.js DEFAULTS) and every sid."""
    gs, wd = js("js", "panels", "gamesettings.js"), js("js", "wsettings.js")
    defaults = re.search(r"DEFAULTS = \{(.*?)\};", wd, re.S).group(1)
    return set(re.findall(r"var: '(VA_\w+)'", gs)) | set(re.findall(r"^\s+(VA_\w+):", defaults, re.M)) | set(re.findall(r"sid: '(\w+)'", gs))


def guide_problems(cfg):
    probs = []
    check_app._guide(cfg, probs)
    return ["%s: %s" % x for x in probs]


class GuideConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_all()

    def test_no_problems(self):
        self.assertEqual(guide_problems(self.cfg), [])

    def test_every_sentence_exists_and_is_plain(self):
        ui = self.cfg["strings"]["ui"]
        for c in self.cfg["guide"]["chapters"]:
            self.assertIn(c["title"], ui)
            for t in c["topics"]:
                for k in [t["title"]] + t["body"]:
                    self.assertIn(k, ui, "%s: %s is not in strings/ui.json" % (t["id"], k))
                    self.assertTrue(k.startswith("guide."), k)
                    self.assertNotIn("{", ui[k], k)
                    self.assertLessEqual(len(ui[k]), 260, "%s is long; split it into two paragraphs" % k)
        for k in self.cfg["panels"]["guide"]["text"].values():
            self.assertIn(k, ui)

    def test_references_are_real(self):
        settings = own_settings()
        for t in topics(self.cfg):
            for k in t.get("keys", []):
                self.assertIn(k, self.cfg["hotkeys"], t["id"])
            if "panel" in t:
                self.assertIn(t["panel"], self.cfg["panels"], t["id"])
            if "setting" in t:
                self.assertIn(t["setting"], settings, "%s jumps to a setting the Options window does not have" % t["id"])

    def test_every_window_has_a_topic(self):
        covered = {t.get("panel") for t in topics(self.cfg)}
        self.assertEqual(sorted(set(S.PANEL_NAMES) - covered), [])
        self.assertEqual(sorted(set(self.cfg["panels"]) - covered), [])

    def test_every_hotkey_in_keyboard_reference(self):
        (ref,) = [c for c in self.cfg["guide"]["chapters"] if c["id"] == S.GUIDE_KEYS_CHAPTER]
        listed = {k for t in ref["topics"] for k in t.get("keys", [])}
        self.assertEqual(sorted(set(self.cfg["hotkeys"]) - listed), [])
        self.assertEqual(sorted(set(S.HOTKEY_NAMES) - listed), [])

    def test_every_own_setting_has_a_settings_topic(self):
        (ch,) = [c for c in self.cfg["guide"]["chapters"] if c["id"] == "settings"]
        named = {t.get("setting") for t in ch["topics"]}
        self.assertEqual(sorted(own_settings() - named), [])

    def test_checker_catches_mistakes(self):
        bad = copy.deepcopy(self.cfg)
        chapters = bad["guide"]["chapters"]
        for t in topics(bad):
            if t.get("panel") == "mailbox":
                del t["panel"]
        chapters[0]["topics"][0]["panel"] = "no_such_window"
        chapters[1]["topics"].append(dict(chapters[0]["topics"][1]))
        (ref,) = [c for c in chapters if c["id"] == "keys"]
        for t in ref["topics"]:
            t["keys"] = [k for k in t.get("keys", []) if k != "party"]
        p = " ".join(guide_problems(bad))
        for want in ("'mailbox' has no topic", "'no_such_window'", "used twice", "hotkey 'party'"):
            self.assertIn(want, p)
        bad["guide"]["chapters"] = [c for c in chapters if c["id"] != "keys"]
        self.assertIn("Keyboard reference chapter", " ".join(guide_problems(bad)))

    def test_shape_rejects_unknown_fields(self):
        errs, refs = [], []
        S.shape({"chapters": [{"id": "a", "title": "guide.title", "topics": [{"id": "b", "title": "guide.title", "body": ["guide.title"], "colour": "red"}]}]}, S.GUIDE, "", errs, refs)
        self.assertTrue(any("colour" in e for e in errs))


class SettingsExplainThemselves(unittest.TestCase):
    """Every option in Options > Vanilla Agents has a tooltip (title, what it does, when to change it) whose body is also shown under it."""

    def test_own_settings_have_tooltips(self):
        tips = load_all()["strings"]["tooltips"]
        ids = own_settings()
        self.assertTrue({"VA_Building", "VA_Quality", "character", "sound"} <= ids)
        for i in ids:
            t = tips.get("setting_" + i)
            self.assertIsNotNone(t, "add strings/tooltips.json#setting_%s (title, body, use)" % i)
            self.assertTrue(t["title"] and t["body"] and t.get("use"), "setting_%s needs a title, body and use line" % i)

    def test_game_options_and_sound_window_have_text(self):
        tips = load_all()["strings"]["tooltips"]
        for k in ("setting_game", "setting_game_sound"):
            self.assertIn(k, tips)
        gs = js("js", "panels", "gamesettings.js")
        self.assertIn("TIPS.setting_game.body", gs)          # a game option without its own text still explains itself
        self.assertIn("'ws-desc'", gs)                        # the visible line under our own options
        for k in re.findall(r"dataset\.tip = '(\w+)'", js("js", "panels", "soundmenu.js")):
            self.assertIn(k, tips)
        self.assertGreaterEqual(len(re.findall(r"dataset\.tip = '(\w+)'", js("js", "panels", "soundmenu.js"))), 3)


class Entrances(unittest.TestCase):
    def test_help_menu_and_options_open_the_guide(self):
        self.assertIn("ctx.panels.guide.api.open()", js("js", "panels", "help.js"))
        self.assertIn("ctx.panels.guide.api.open()", js("js", "panels", "escmenu.js"))
        self.assertIn("ctx.panels.guide.api.open('settings')", js("js", "panels", "gamesettings.js"))
        self.assertIn("guide", js("js", "main.js"))
        self.assertIn("/css/guide.css", js("index.html"))


class GuideDoc(unittest.TestCase):
    def test_committed_file_is_generated(self):
        self.assertEqual(make_guide.main(["--check"]), 0, "run: python scripts/make_guide.py")

    def test_doc_has_every_topic(self):
        cfg = make_guide.load()
        text = make_guide.render(cfg)
        for c in cfg["guide"]["chapters"]:
            for t in c["topics"]:
                self.assertIn("### " + cfg["ui"][t["title"]], text)


class Readable(unittest.TestCase):
    def test_guide_colours_on_parchment(self):
        css = js("css", "guide.css")
        rules = re.findall(r"(\.gd-[\w-]+(?:\.on)?)[^{]*\{[^}]*?[^-]color: (#[0-9a-fA-F]{6})", css)
        self.assertGreaterEqual(len(rules), 8)
        for sel, col in rules:
            if sel in (".gd-kbd",):
                continue      # gold on a dark key cap, checked below
            self.assertGreaterEqual(contrast(col, PARCHMENT), 4.5, "%s %s on parchment" % (sel, col))
        self.assertGreaterEqual(contrast("#ffd100", "#1b160f"), 4.5)

    def test_option_lines_on_dark_metal(self):
        css = js("css", "guide.css")
        for sel in ("ws-desc", "ws-note"):
            col = re.search(r"\.%s \{[^}]*?[^-]color: (#[0-9a-fA-F]{6})" % sel, css).group(1)
            self.assertGreaterEqual(contrast(col, METAL), 4.5, sel)


if __name__ == "__main__":
    unittest.main()
