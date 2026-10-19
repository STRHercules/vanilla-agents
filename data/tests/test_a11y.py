"""Accessibility basics (V-033): chat colours on parchment meet WCAG AA (4.5:1) against the light end of the scroll texture, every
chat channel has a parchment colour, every window has a name for screen readers, and the code keeps its roles / live region / hotkey
guard."""
import json
import re
import unittest

from vanilla_agents.app.config import ROOT

WEB = ROOT / "app" / "web"
PARCHMENT = "#d9c79f"


def luminance(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class Contrast(unittest.TestCase):
    def test_ratio_formula(self):
        self.assertAlmostEqual(contrast("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(contrast("#777777", "#ffffff"), 4.48, places=2)

    def test_chat_colours_on_parchment(self):
        css = (WEB / "css" / "a11y.css").read_text(encoding="utf-8")
        rules = dict(re.findall(r"\.frame-scroll \.chat-([\w-]+) \{[^}]*?color: (#[0-9a-fA-F]{6})", css))
        theme = json.loads((ROOT / "app" / "config" / "theme.json").read_text(encoding="utf-8"))["colors"]
        channels = [k[len("chat_"):] for k in theme if k.startswith("chat_")]
        self.assertTrue(channels)
        for ch in channels + ["offline", "empty", "time"]:
            self.assertIn(ch, rules, "a11y.css needs a parchment colour for chat-%s" % ch)
        for name, col in rules.items():
            self.assertGreaterEqual(contrast(col, PARCHMENT), 4.5, "chat-%s %s on parchment" % (name, col))
        # the bright theme colours (for dark windows) would not be readable on parchment: that is why the overrides exist
        self.assertLess(contrast(theme["chat_system"], PARCHMENT), 4.5)


class Names(unittest.TestCase):
    def test_every_window_has_a_screen_reader_name(self):
        ui = json.loads((ROOT / "app" / "config" / "strings" / "ui.json").read_text(encoding="utf-8"))
        for f in (ROOT / "app" / "config" / "panels").glob("*.json"):
            c = json.loads(f.read_text(encoding="utf-8"))
            key = c.get("title") or "a11y." + f.stem
            self.assertIn(key, ui, "%s has no title and no %s string" % (f.name, key))

    def test_code_keeps_roles_live_region_and_hotkey_guard(self):
        panel = (WEB / "js" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("'role', c.visible ? 'region' : 'dialog'", panel)
        self.assertIn("aria-label", panel)
        self.assertIn("'BUTTON'", (WEB / "js" / "hotkeys.js").read_text(encoding="utf-8"))
        self.assertIn("announce(", (WEB / "js" / "panels" / "chat.js").read_text(encoding="utf-8"))
        self.assertIn("announce(", (WEB / "js" / "toast.js").read_text(encoding="utf-8"))
        self.assertIn("aria-live", (WEB / "js" / "a11y.js").read_text(encoding="utf-8"))
        self.assertIn('href="/css/a11y.css"', (WEB / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
