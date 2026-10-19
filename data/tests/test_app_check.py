import copy
import tempfile
import unittest
from pathlib import Path

from vanilla_agents.app.check_app import check_app
from vanilla_agents.app.config import load_all

REAL_ASSETS = Path(load_all()["app"]["assets_dir"])


def assets_for(cfg, root):
    """A throwaway assets folder holding an empty file for every asset the settings name."""
    from vanilla_agents.app import schema as S
    refs = []
    from vanilla_agents.app.check_app import _shape_all
    _, r = _shape_all(cfg)
    for _f, _p, kind, v in r:
        if kind == "asset":
            p = Path(root) / v
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
    return root


class CheckApp(unittest.TestCase):
    def setUp(self):
        self.cfg = load_all()
        self.tmp = tempfile.TemporaryDirectory()
        self.assets = assets_for(self.cfg, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, cfg):
        return check_app(cfg, assets_dir=self.assets)

    def test_shipped_settings_are_clean(self):
        self.assertEqual(self.run_check(self.cfg), [])

    def test_action_bar_buttons_and_number_keys(self):
        """V-052: every button is one the bar knows, keys are single digits, no key or button twice."""
        slots = self.cfg["panels"]["action_bar"]["slots"]
        self.assertEqual([x["key"] for x in slots], ["1", "2", "3", "4", "5", "6"])
        for change, words in ((lambda s: s[0].update(id="dance"), "not one the action bar knows"),
                              (lambda s: s[0].update(key="F"), "use one digit"),
                              (lambda s: s[0].update(key="12"), "use one digit"),
                              (lambda s: s[1].update(key="1"), "same number key"),
                              (lambda s: s[1].update(id="verify"), "there twice")):
            cfg = copy.deepcopy(self.cfg)
            change(cfg["panels"]["action_bar"]["slots"])
            probs = " ".join(str(p) for p in self.run_check(cfg))
            self.assertIn(words, probs)
        cfg = copy.deepcopy(self.cfg)
        del cfg["panels"]["action_bar"]["slots"][2]["key"]          # a button without a number key is fine
        self.assertEqual(self.run_check(cfg), [])

    @unittest.skipUnless(REAL_ASSETS.is_dir(), "baked assets not present")
    def test_real_assets_exist(self):
        self.assertEqual(check_app(self.cfg), [])

    def test_unknown_key_is_reported_in_plain_words(self):
        c = copy.deepcopy(self.cfg)
        c["theme"]["sizes"]["huge_px"] = 99
        out = self.run_check(c)
        self.assertTrue(any("theme.json" in m and "nobody uses" in m and "huge_px" in m for m in out), out)

    def test_missing_asset(self):
        c = copy.deepcopy(self.cfg)
        c["theme"]["bar_texture"] = "interface/nope.png"
        self.assertTrue(any("interface/nope.png" in m and "bake" in m for m in self.run_check(c)))

    def test_raw_blp_and_traversal_rejected(self):
        c = copy.deepcopy(self.cfg)
        c["theme"]["bar_texture"] = "interface/x.blp"
        c["theme"]["slot_texture"] = "../secret.png"
        out = " ".join(self.run_check(c))
        self.assertIn("baked .png", out)
        self.assertIn("bad asset path", out)

    def test_bad_placeholder_in_event_template(self):
        c = copy.deepcopy(self.cfg)
        c["strings"]["eli5"]["task_created"] = "New {nonsense}"
        self.assertTrue(any("eli5" in m and "nonsense" in m for m in self.run_check(c)))

    def test_bad_placeholder_in_ui_string(self):
        c = copy.deepcopy(self.cfg)
        c["strings"]["ui"]["chat.title"] = "Chat {who}"
        self.assertTrue(any("who" in m for m in self.run_check(c)))

    def test_dangling_references(self):
        c = copy.deepcopy(self.cfg)
        c["panels"]["chat"]["title"] = "no.such.key"
        c["panels"]["chat"]["tips"]["window"] = "no_tip"
        c["panels"]["chat"]["hotkey"] = "no_key"
        c["panels"]["chat"]["frame"] = "no_frame"
        out = " ".join(self.run_check(c))
        for w in ("no.such.key", "no_tip", "no_key", "no_frame"):
            self.assertIn(w, out)

    def test_missing_event_type_and_wrong_types(self):
        c = copy.deepcopy(self.cfg)
        del c["metaphors"]["events"]["tool_use"]
        c["app"]["port"] = "8765"
        c["hotkeys"]["chat"] = "l"
        out = " ".join(self.run_check(c))
        self.assertIn("tool_use", out)
        self.assertIn("'port' should be a whole number", out)

    def test_host_must_be_loopback(self):
        c = copy.deepcopy(self.cfg)
        c["app"]["host"] = "0.0.0.0"
        self.assertTrue(any("127.0.0.1" in m for m in self.run_check(c)))

    def test_cli_exit_codes(self):
        import io
        from contextlib import redirect_stdout
        from vanilla_agents.app import check
        with redirect_stdout(io.StringIO()):
            self.assertIn(check.main(), (0, 1))


if __name__ == "__main__":
    unittest.main()
