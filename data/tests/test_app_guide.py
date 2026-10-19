import re
import unittest

from vanilla_agents.app.config import ROOT, iter_files, load_all
from vanilla_agents.app.schema import flat_keys

GUIDE = ROOT / "docs" / "CHANGE-GUIDE.md"


def tokens():
    return set(re.findall(r"`([^`\s]+#[^`\s]+)`", GUIDE.read_text(encoding="utf-8")))


def rx(token):
    return re.compile("^" + re.escape(token).replace(r"\*", "[^./#]+") + "$")


def every_key():
    return ["%s#%s" % (f, p) for f, data in iter_files(load_all()) for p in flat_keys(data)]


class ChangeGuide(unittest.TestCase):
    def test_every_setting_is_documented(self):
        pats = [rx(t) for t in tokens()]
        missing = [k for k in every_key() if not any(p.match(k) for p in pats)]
        self.assertEqual(missing, [], "add these to docs/CHANGE-GUIDE.md: %s" % missing[:10])

    def test_guide_names_no_missing_setting(self):
        keys = every_key()
        stale = [t for t in tokens() if not any(rx(t).match(k) for k in keys)]
        self.assertEqual(stale, [], "docs/CHANGE-GUIDE.md names settings that do not exist")

    def test_flat_keys_shapes(self):
        self.assertEqual(list(flat_keys({"a": {"b": 1}, "c": [{"d": 1}, {"d": 2}], "e": ["x"]})), ["a.b", "c[].d", "e[]"])


if __name__ == "__main__":
    unittest.main()
