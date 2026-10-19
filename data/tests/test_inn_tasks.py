"""V-043: how helpers do their tasks in the inn. The command patterns live in app/config/workshop.json (inn.tasks) and are used by the
browser (app/web/workshop3d/innwork.js, RegExp with flag i); this test runs the same rules in Python on a table of sample commands."""
import copy
import json
import re
import unittest
from pathlib import Path

from vanilla_agents.app.check_app import check_app, pattern_problem
from vanilla_agents.app.config import CONFIG_DIR, load_all

ROOT = Path(__file__).resolve().parents[1]
INN = json.loads((CONFIG_DIR / "workshop.json").read_text(encoding="utf-8"))["inn"]


def classify(activity, tasks=INN["tasks"]):
    """Same as innwork.js taskKind: the command of a Bash step -> 'ship' (checked first), 'test' or None."""
    m = re.match(r"^Bash\s+([\s\S]*)$", activity or "")
    if not m:
        return None
    for kind in ("ship", "test"):
        if any(re.search(p, m.group(1), re.I) for p in tasks[kind]):
            return kind
    return None


SAMPLES = [
    ("Bash cargo test", "test"), ("Bash cargo build --release", "test"), ("Bash cd core && cargo test -p engine", "test"),
    ("Bash npm test", "test"), ("Bash npm run build", "test"), ("Bash npm run test:unit -- --watch=false", "test"), ("Bash pnpm build", "test"),
    ("Bash yarn test", "test"), ("Bash npx vitest run", "test"), ("Bash npx tsc --noEmit", "test"),
    ("Bash pytest -q tests/test_x.py", "test"), ("Bash python3 -m pytest", "test"), ("Bash python -m unittest discover -s tests -t .", "test"),
    ("Bash python3 -W error::ResourceWarning -m unittest discover -s tests -t .", "test"),
    ("Bash go test ./...", "test"), ("Bash go build ./cmd/app", "test"), ("Bash make", "test"), ("Bash make -j8 all", "test"),
    ("Bash cmake --build build", "test"), ("Bash ./gradlew test", "test"), ("Bash gradle build", "test"), ("Bash mvn -q package", "test"),
    ("Bash dotnet test", "test"), ("Bash dotnet build MyApp.sln", "test"), ("Bash swift test", "test"), ("Bash bundle exec rspec", "test"),
    ("Bash tox -e py311", "test"), ("Bash ctest --output-on-failure", "test"),
    ("Bash git commit -m 'fix the parser'", "ship"), ("Bash git add -A && git commit -m wip", "ship"), ("Bash git push origin feat", "ship"),
    ("Bash git tag v1.2.0", "ship"), ("Bash npm publish", "ship"), ("Bash cargo publish --dry-run", "ship"), ("Bash twine upload dist/*", "ship"),
    ("Bash zip -r out.zip dist", "ship"), ("Bash tar -czf release.tgz build", "ship"), ("Bash tar czf a.tgz b", "ship"),
    ("Bash git commit -m 'make cargo test pass'", "ship"),      # shipping wins over the words in the message
    ("Bash ls -la", None), ("Bash cat README.md", None), ("Bash git status", None), ("Bash git diff HEAD~1", None), ("Bash mkdir -p build", None),
    ("Bash python3 scripts/make_guide.py", None), ("Bash cmake -S . -B build", None), ("Bash tar -xzf data.tgz", None), ("Bash unzip x.zip", None),
    ("Bash cat tsconfig.json", None), ("Bash rg pytest_plugins", None),
    ("using Bash", None), ("Read src/main.rs", None), ("Edit cargo test notes.md", None), ("working on a new request", None), ("", None), (None, None),
]


class InnTaskPatterns(unittest.TestCase):
    def test_patterns_are_valid_in_python_and_js(self):
        for kind in ("test", "ship"):
            for p in INN["tasks"][kind]:
                self.assertIsNone(pattern_problem(p), p)
        for p in (INN["tasks"]["anvil"], INN["tasks"]["drop"], INN["lights"]["names"]):
            self.assertIsNone(pattern_problem(p), p)

    def test_sample_commands(self):
        for act, want in SAMPLES:
            self.assertEqual(classify(act), want, act)

    def test_doodad_names(self):
        anvil, drop, lights = (re.compile(INN["tasks"]["anvil"], re.I), re.compile(INN["tasks"]["drop"], re.I), re.compile(INN["lights"]["names"], re.I))
        for n in ("forgebonfire.m2", "blacksmith_smoke.m2", "anvil01.m2"):
            self.assertTrue(anvil.search(n), n)
        for n in ("replacecrate02.m2", "barrel01.m2", "mailbox01.m2", "sack01.m2"):
            self.assertTrue(drop.search(n), n)
        for n in ("candle01.m2", "new_small_lantern01.m2", "innchandelier.m2", "torch.m2", "tallbraziernoomni01.m2", "darkstreetlamp.m2", "forgebonfire.m2"):
            self.assertTrue(lights.search(n), n)
        self.assertFalse(lights.search("firewoodpile03.m2"))       # a pile of logs does not glow

    def test_js_uses_the_same_rule(self):
        js = (ROOT / "app" / "web" / "workshop3d" / "innwork.js").read_text(encoding="utf-8")
        self.assertIn("for (const k of ['ship', 'test'])", js)        # same order as classify(): shipping first
        self.assertIn("/^Bash\\s+([\\s\\S]*)$/", js)
        self.assertIn("new RegExp(s, 'i')", js)
        inn = (ROOT / "app" / "web" / "workshop3d" / "inn.js").read_text(encoding="utf-8")
        self.assertIn("from './innwork.js'", inn)
        self.assertIn("w.inn", inn)

    def test_check_app_rejects_bad_settings(self):
        cfg = load_all()
        self.assertEqual([p for p in check_app(cfg) if "inn." in p], [])
        bad = copy.deepcopy(cfg)
        t, li = bad["workshop"]["inn"]["tasks"], bad["workshop"]["inn"]["lights"]
        t["test"] = t["test"] + ["(?i)pytest", "", "cargo("]
        t["ship"] = []
        t["hold_s"] = 0
        li["max"] = [0, 3, 6]
        li["flicker"] = 2
        li["names"] = "candle|"
        msgs = "\n".join(check_app(bad))
        for want in ("inn.tasks.test[%d]" % (len(t["test"]) - 3), "inn.tasks.test[%d]" % (len(t["test"]) - 2), "inn.tasks.test[%d]" % (len(t["test"]) - 1),
                     "'inn.tasks.ship' needs at least one", "inn.tasks.hold_s", "inn.lights.max", "inn.lights.flicker", "inn.lights.names"):
            self.assertIn(want, msgs)


class CheckRestarts(unittest.TestCase):
    def test_check_started_again_clears_the_old_verdict(self):
        import os
        import tempfile
        from vanilla_agents.app.state import AppState
        from vanilla_agents.events.feed import Feed
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "feed.jsonl")
            f = Feed(path)
            f.append("task_created", "t1", None, {"title": "A"})
            f.append("check_started", "t1", None, {"check": "verification"})
            f.append("check_finished", "t1", None, {"check": "verification", "verdict": "VERIFIED"})
            snap = AppState(load_all(), path).snapshot()
            self.assertEqual(snap["tasks"][0]["objectives"], [{"check": "verification", "verdict": "VERIFIED"}])
            f.append("check_started", "t1", None, {"check": "verification"})
            snap = AppState(load_all(), path).snapshot()
            self.assertEqual(snap["tasks"][0]["objectives"], [{"check": "verification", "verdict": None}])


if __name__ == "__main__":
    unittest.main()
