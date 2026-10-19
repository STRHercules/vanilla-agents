"""The project's Idea (D-340..D-349): draft parser, quest parser (no duplicates), approval hash, intent-gap guard, API. Temp repos only."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from vanilla_agents.app import route, server
from vanilla_agents.app.config import load_all
from vanilla_agents.app.verify_job import slug_of
from vanilla_agents.events.feed import Feed
from vanilla_agents.onboard import idea
from tests import test_app_route as tar
from tests.test_app_route import RULES, ch
from tests.test_onboard import git_repo

README = """# Demo
![badge](x.svg)

Demo is a tiny game server that lets friends play a card game in a browser.

## Non-goals
- No payments or real money
- Do not touch `legacy/` or `vendor/*.js`

## Definition of done
- A friend can join with a link
- [x] Login works

## Rules
- Never commit secrets
- Tests must pass before a save

## Next steps
1. Add the lobby screen
2. [ ] Add card shuffling
3. [x] Set up the repo
"""
ROADMAP = "# Roadmap\n\n1. Lobby screen\n2. Scoreboard\n- plain bullet is not a step\n- [ ] Chat box\n"
TITLES = ["Lobby screen", "Scoreboard", "Chat box", "Add the lobby screen", "Add card shuffling"]      # the roadmap file first


def repo_with(tmp, name="demo", files=None):
    return git_repo(tmp, name, files or {"README.md": README, "ROADMAP.md": ROADMAP})


class Draft(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = repo_with(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sections_come_from_the_docs(self):
        d = idea.draft(self.repo)
        self.assertIn("tiny game server", d["goal"])
        self.assertEqual(d["non_goals"], ["No payments or real money", "Do not touch `legacy/` or `vendor/*.js`"])
        self.assertEqual(d["done_means"], ["A friend can join with a link"])      # the ticked box is not 'done means' text
        self.assertEqual(d["rules"], ["Never commit secrets", "Tests must pass before a save"])
        self.assertEqual(d["out_of_scope"], ["legacy/", "vendor/*.js"])
        self.assertEqual(d["sources"], ["README.md"])

    def test_render_parse_round_trip(self):
        d = idea.draft(self.repo)
        back = idea.parse(idea.render(d, "2026-10-17"))
        self.assertEqual(back, {k: d[k] for k in back})

    def test_empty_repo_gives_placeholders_not_errors(self):
        empty = repo_with(self.tmp.name, "empty", {"main.py": "x = 1"})
        d = idea.parse(idea.render(idea.draft(empty)))
        self.assertEqual((d["goal"], d["non_goals"], d["out_of_scope"]), ("", [], []))

    def test_write_draft_never_overwrites(self):
        root = os.path.join(self.tmp.name, "ideas")
        self.assertTrue(idea.write_draft(root, "demo", self.repo))
        Path(idea.path_of(root, "demo")).write_text("## Goal\nMine.\n", encoding="utf-8")
        self.assertFalse(idea.write_draft(root, "demo", self.repo))
        self.assertEqual(idea.read(root, "demo")["goal"], "Mine.")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "IDEA.md")))       # the file lives outside the repo

    def test_docs_folder_and_claude_md(self):
        r = repo_with(self.tmp.name, "two", {"CLAUDE.md": "# C\n\n## Rules\n- Keep it small\n", "docs/PROJECT_ROADMAP.md": "# R\n## 5. Priority order\n1. Do the first thing\n"})
        d = idea.draft(r)
        self.assertEqual((d["rules"], d["sources"]), (["Keep it small"], ["CLAUDE.md"]))
        self.assertEqual([t for t, _ in idea.quests(r)], ["Do the first thing"])


class Quests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = repo_with(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_numbered_items_and_open_boxes_only(self):
        self.assertEqual([t for t, _ in idea.quests(self.repo)], TITLES)

    def test_seed_twice_adds_nothing_the_second_time(self):
        feed, have = Feed(os.path.join(self.tmp.name, "feed.jsonl")), set()
        self.assertEqual(idea.seed_quests(self.repo, "demo", feed, have), 5)
        self.assertEqual(idea.seed_quests(self.repo, "demo", feed, have), 0)
        evs = [e for e in feed.read_since(0, 100) if e.type == "task_created"]
        self.assertEqual((len(evs), len({e.task for e in evs})), (5, 5))
        self.assertEqual(idea.seed_quests(self.repo, "demo", feed, {e.task for e in evs}), 0)      # a fresh run that rebuilt `have` from the feed

    def test_cap(self):
        many = "# Roadmap\n" + "\n".join("%d. Step number %d" % (i, i) for i in range(1, 80))
        self.assertEqual(len(idea.quests(repo_with(self.tmp.name, "big", {"ROADMAP.md": many}))), idea.MAX_QUESTS)


class Approval(unittest.TestCase):
    def test_hash_ignores_comments_and_spacing_but_not_meaning(self):
        a = idea.parse("# Idea\n<!-- x -->\n## Goal\nBuild it.\n\n## Not building\n- Payments\n")
        b = idea.parse("# Idea\r\n<!-- different comment -->\r\n## Goal\r\nBuild it.\r\n## Not building\r\n\r\n- Payments\r\n")
        c = idea.parse("## Goal\nBuild it.\n## Not building\n- Payments\n- Ads\n")
        self.assertEqual(idea.idea_hash(a), idea.idea_hash(b))
        self.assertNotEqual(idea.idea_hash(a), idea.idea_hash(c))

    def test_status_unapproved_approved_changed(self):
        a, c = idea.parse("## Goal\nOne.\n"), idea.parse("## Goal\nTwo.\n")
        self.assertEqual(idea.status(a, []), "unapproved")
        self.assertEqual(idea.status(a, ["some-quest-id"]), "unapproved")       # a quest approval is not an Idea approval
        self.assertEqual(idea.status(a, [idea.key_of(a)]), "approved")
        self.assertEqual(idea.status(c, [idea.key_of(a)]), "changed")


class IntentGuard(unittest.TestCase):
    def test_out_of_scope_file_forces_full_route_with_holdouts(self):
        r = route.classify(ch(("legacy/old.js", 1)), RULES, (), ["legacy/**"])
        self.assertEqual((r["route"], r["holdouts"], r["reasons"]), ("full", True, [("intent", {"files": 1})]))

    def test_other_files_stay_fast_and_no_list_changes_nothing(self):
        self.assertEqual(route.classify(ch(("src/a.rs", 1)), RULES, (), ["legacy/**"])["route"], "fast")
        self.assertEqual(route.classify(ch(("legacy/old.js", 1)), RULES)["route"], "fast")

    def test_trailing_slash_means_everything_under_it(self):
        with tempfile.TemporaryDirectory() as root:
            p = idea.path_of(root, "demo")
            os.makedirs(os.path.dirname(p))
            Path(p).write_text("## Out-of-scope paths\n- legacy/\n- `*.lock`\n", encoding="utf-8")
            self.assertEqual(idea.out_of_scope(root, "demo"), ["legacy/**", "*.lock"])
            self.assertEqual(idea.out_of_scope(root, "nobody"), [])


class Wired(tar.Wiring):
    """Verify now reads the project's IDEA.md and says so in the route line the chat log shows."""

    def put_idea(self, text):
        root = os.path.join(self.tmp.name, "ideas")
        self.cfg["app"]["ideas_root"] = root
        p = idea.path_of(root, slug_of(json.loads(Path(self.cfg["app"]["projects_path"]).read_text(encoding="utf-8"))[self.tmp.name]))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Path(p).write_text(text, encoding="utf-8")

    def test_intent_check_line_and_full_route(self):
        self.seen = []
        self.put_idea("## Out-of-scope paths\n- legacy/\n")
        ev = self.run_with(ch(("legacy/a.js", 2)))
        self.assertEqual((ev.data["route"], self.calls[0]["holdouts"]), ("full", True))
        self.assertIn("Intent check", ev.data["why"])

    def test_untouched_paths_leave_the_route_alone(self):
        self.seen = []
        self.put_idea("## Out-of-scope paths\n- legacy/\n")
        self.assertEqual(self.run_with(ch(("src/a.rs", 2))).data["route"], "fast")


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        cfg = load_all()
        cfg["app"].update(projects_path=os.path.join(t, "projects.json"), trusted_root=os.path.join(t, "trusted"), runs_path=os.path.join(t, "runs.jsonl"),
                          ideas_root=os.path.join(t, "ideas"), approvals_path=os.path.join(t, "approvals.json"))
        self.repo = repo_with(t)
        self.idea_file = os.path.join(t, "ideas", "demo", "IDEA.md")
        self.ctx = server.Ctx(cfg, os.path.join(t, "feed.jsonl"), None, settings_file=os.path.join(t, "s.json"))
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json", "X-Vanilla-Key": self.ctx.key, **({"Origin": origin} if origin else {})})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def onboard(self):
        return self.call("POST", "/api/onboard", {"path": self.repo})

    def approve(self, origin=None):
        """Approve the Idea the way the window does: with the Idea key it was shown (D-384)."""
        return self.call("POST", "/api/idea/approve", {"key": self.call("GET", "/api/idea")[1].get("key")}, origin)

    def test_onboarding_drafts_the_idea_outside_the_repo(self):
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "none")
        self.assertTrue(self.onboard()[1]["onboarded"]["drafted"])
        self.assertTrue(os.path.isfile(self.idea_file))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "IDEA.md")))
        d = self.call("GET", "/api/idea")[1]
        self.assertEqual((d["state"], d["out_of_scope"]), ("unapproved", ["legacy/", "vendor/*.js"]))
        self.assertFalse(self.onboard()[1]["onboarded"]["drafted"])      # idempotent

    def test_approve_records_seeds_quests_once_and_shows_changed_after_edit(self):
        self.onboard()
        st, d = self.approve()
        self.assertEqual((st, d["state"], d["seeded"]), (200, "approved", 5))
        snap = self.call("GET", "/api/state")[1]
        self.assertEqual(sorted(t["title"] for t in snap["tasks"]), sorted(TITLES))
        self.assertFalse(any(t["id"].startswith("idea:") for t in snap["tasks"]))      # the approval is not a quest
        self.assertTrue(any("approved the plan for the Idea" in c["text"] for c in snap["chat"]))
        self.assertEqual(self.approve()[1]["seeded"], 0)
        self.assertEqual(len(self.call("GET", "/api/state")[1]["tasks"]), 5)
        text = Path(self.idea_file).read_text(encoding="utf-8")
        Path(self.idea_file).write_text(text + "- Nothing about crypto\n", encoding="utf-8")
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "changed")
        Path(self.idea_file).write_text(text, encoding="utf-8")
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "approved")

    def test_draft_when_missing_and_refusals(self):
        self.assertEqual(self.approve()[0], 400)         # no project open
        self.onboard()
        os.remove(self.idea_file)
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "missing")
        self.assertEqual(self.approve()[0], 400)
        self.assertEqual(self.call("POST", "/api/idea/draft", {})[1]["state"], "unapproved")
        self.assertEqual(self.approve("http://evil.example")[0], 403)


if __name__ == "__main__":
    unittest.main()
