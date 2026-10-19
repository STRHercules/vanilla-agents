import os
import sys
import tempfile
import unittest

from vanilla_agents.app import launch
from vanilla_agents.app.config import ROOT, load_all

sys.path.insert(0, str(ROOT / "scripts"))
import make_shortcut  # noqa: E402


class Launch(unittest.TestCase):
    ENV = {"ProgramFiles": "P", "ProgramFiles(x86)": "Q", "LOCALAPPDATA": "L"}

    def test_chrome_preferred_edge_fallback_none(self):
        has = lambda *names: (lambda p: any(n in p for n in names))
        self.assertEqual(launch.find_browser(self.ENV, has("chrome.exe"))[0], "chrome")
        self.assertEqual(launch.find_browser(self.ENV, has("chrome.exe", "msedge.exe"))[0], "chrome")
        self.assertEqual(launch.find_browser(self.ENV, has("msedge.exe"))[0], "edge")
        self.assertIsNone(launch.find_browser(self.ENV, has()))

    def test_command_is_chromeless_with_own_profile(self):
        cmd = launch.build_cmd("c.exe", "http://127.0.0.1:1/", "~/.vanilla-agents/work/chrome-profile", 1280, 800)
        self.assertIn("--app=http://127.0.0.1:1/", cmd)
        self.assertIn("--user-data-dir=~/.vanilla-agents/work/chrome-profile", cmd)
        app = load_all()["app"]
        self.assertTrue(app["browser_profile_dir"].startswith(os.path.expanduser("~/.vanilla-agents/work")))

    def test_no_browser_gives_plain_error(self):
        with self.assertRaises(RuntimeError) as e:
            launch.open_window("http://x/", load_all()["app"], find=lambda: None)
        self.assertIn("Chrome", str(e.exception))

    def test_launcher_cmd_exists(self):
        with open(os.path.join(os.path.dirname(ROOT), "vanilla-agents.cmd")) as f:
            text = f.read()
        self.assertIn("vanilla_agents.app.server --open", text)


class Shortcut(unittest.TestCase):
    def test_default_is_desktop_but_nothing_is_created(self):
        self.assertTrue(make_shortcut.desktop_lnk({"USERPROFILE": "U"}).endswith(os.path.join("U", "Desktop", "vanilla-agents.lnk")))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_creates_lnk_at_a_fake_path(self):
        with tempfile.TemporaryDirectory() as d:
            lnk = os.path.join(d, "fake name.lnk")
            make_shortcut.make_shortcut(lnk, target=os.path.join(os.path.dirname(ROOT), "vanilla-agents.cmd"))
            self.assertTrue(os.path.getsize(lnk) > 0)

    def test_failure_is_reported(self):
        class R:
            returncode, stderr, stdout = 1, "boom", ""
        with self.assertRaises(RuntimeError):
            make_shortcut.make_shortcut("nowhere.lnk", run=lambda *a, **k: R())


if __name__ == "__main__":
    unittest.main()
