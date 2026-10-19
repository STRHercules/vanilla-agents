"""V-044 integration hub, the board and the window side: dedupe by link, closed upstream disappears (the quest stays), "Make it a quest"
makes a normal unapproved quest whose why names the source, settings keep secrets out of every GET / snapshot / diagnostics, every
POST needs the window key, the file import is size-limited, the background pass only runs what is switched on.
Fake gh runner and the dev-only fixture: no network."""
import http.client
import json
import os
import tempfile
import threading
import unittest

from tests.test_integrations_sources import Runner, gitdir, which
from vanilla_agents.app import diagnostics, server, settings
from vanilla_agents.app.config import ROOT, load_all
from vanilla_agents.events.feed import Feed
from vanilla_agents.integrations import board as B
from vanilla_agents.integrations import isettings

TOKEN = "ghp_SECRETsecretSECRET1234"
LIN = "lin_api_SECRETsecret9876"
FIXTURE = str(ROOT / "scripts" / "demo_integrations.json")


def item(n, source="github", origin_url=True):
    return B.make_item(source, "#%d" % n, "Item %d" % n, "body %d" % n, "https://github.com/o/r/issues/%d" % n if origin_url else "")


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = B.Board(os.path.join(self.tmp.name, "b.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_dedupe_close_made_hide(self):
        self.assertTrue(self.b.replace("github", "issues", [item(1), item(2), item(1)]))
        self.assertFalse(self.b.replace("github", "issues", [item(1), item(2)]))        # nothing new: no write, no change
        self.b.replace("github", "prs", [item(2)])                                       # the same link from another list: one bounty
        self.assertEqual(len(self.b.visible()), 2)
        self.b.replace("github", "issues", [item(1)])                                    # #2 closed as an issue, still a requested PR
        self.assertEqual({i["key"] for i in self.b.visible()}, {"#1", "#2"})
        self.b.replace("github", "prs", [])
        self.assertEqual([i["key"] for i in self.b.visible()], ["#1"])                   # gone upstream: gone from the board
        self.b.mark_made(item(1)["id"], "q-1")
        self.assertEqual(self.b.visible(), [])
        self.b.replace("github", "issues", [item(3), item(12)])
        self.assertEqual([i["key"] for i in self.b.visible([{"why": "GitHub #3: https://github.com/o/r/issues/3 - x"}])], ["#12"])   # #3 has a quest, #12 is not #1
        self.b.hide(item(12)["id"])
        again = B.Board(os.path.join(self.tmp.name, "b.json"))                           # kept on disk
        self.assertEqual((again.visible([]), again.made), ([i for i in again.visible([])], {item(1)["id"]: "q-1"}))
        self.assertEqual([i["key"] for i in again.visible([])], ["#3"])
        with self.assertRaises(ValueError):
            self.b.hide("nope")

    def test_items_and_labels(self):
        it = B.make_item("jira", "INN-1", "  Fix\n it ", "x" * 500, "javascript:alert(1)")
        self.assertEqual((it["id"], it["title"], len(it["excerpt"]), it["url"]), ("jira:INN-1", "Fix it", 240, ""))
        self.assertEqual(B.label_of(B.make_item("github", "#4", "t", kind="pr")), "GitHub PR #4")
        self.assertEqual(B.label_of(B.make_item("linear", "ENG-2", "t")), "Linear ENG-2")
        with self.assertRaises(ValueError):
            B.make_item("github", "#1", "  ")
        self.assertNotEqual(B.board_path("/w", "/p/a"), B.board_path("/w", "/p/b"))


class Settings(unittest.TestCase):
    def test_secrets_kept_masked_and_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            f, sd = os.path.join(d, "s.json"), load_all()["sounds"]
            settings.update(f, {"master": {"volume": 0.3}}, sd)
            isettings.save(f, {"github": {"enabled": True, "mode": "token", "token": TOKEN, "labels": "bug, good first issue"}, "refresh_minutes": 5})
            isettings.save(f, {"github": {"enabled": True, "mode": "token", "token": ""}})          # an empty field keeps the saved token
            s = isettings.load(f)
            self.assertEqual((s["github"]["token"], s["github"]["labels"], s["refresh_minutes"]), (TOKEN, ["bug", "good first issue"], 5))
            settings.update(f, {"master": {"mute": True}}, sd)                                       # sound saves keep the group
            self.assertEqual(isettings.load(f)["github"]["token"], TOKEN)
            settings.update(f, {"integrations": {"github": {"token": "other-token-123"}}}, sd)         # /api/settings cannot change them
            self.assertEqual(isettings.load(f)["github"]["token"], TOKEN)
            v = json.dumps(isettings.view(isettings.load(f)))
            self.assertNotIn(TOKEN, v)
            self.assertIn('"token_end": "1234"', v)
            with self.assertRaisesRegex(ValueError, "no token"):
                isettings.save(f, {"github": {"clear_token": True}})                               # still switched on with a token
            isettings.save(f, {"github": {"enabled": False, "clear_token": True}})
            self.assertEqual(isettings.load(f)["github"]["token"], "")

    def test_validation(self):
        for bad in ({"github": {"mode": "ssh"}}, {"github": {"prs": "everything"}}, {"github": {"labels": "a,b,c,d,e,f"}}, {"refresh_minutes": 0},
                    {"refresh_minutes": True}, {"github": {"token": "has space inside"}}, {"linear": {"enabled": True}}, {"linear": {"team": "a b"}},
                    {"jira": {"site": "http://x.atlassian.net"}}, {"jira": {"enabled": True, "site": "https://x.atlassian.net"}}, {"slack": {}},
                    {"github": {"host": "not a host"}}, {"github": {"enabled": "yes"}}):
            with self.assertRaises(ValueError, msg=bad):
                isettings.validate(bad)


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feed = os.path.join(self.tmp.name, "f.jsonl")
        self.ctx = server.Ctx(load_all(), self.feed, None, assets_dir=self.tmp.name, settings_file=os.path.join(self.tmp.name, "s.json"), watch=False)
        self.ctx.integ.env = {"VANILLA_INTEGRATIONS_FAKE": FIXTURE}           # dev-only fixture instead of gh / the network
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, key=True, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        h = {"Content-Type": "application/json", **({"X-Vanilla-Key": self.ctx.key} if key else {})}
        c.request(method, path, raw if raw is not None else None if body is None else json.dumps(body), h)
        r = c.getresponse()
        text = r.read().decode("utf-8")
        c.close()
        return r.status, json.loads(text), text

    def test_every_post_needs_the_key(self):
        from vanilla_agents.app import integrations_api
        for call in integrations_api.POST_CALLS:
            self.assertEqual(self.req("POST", call, {"id": "x", "source": "github", "text": "a", "settings": {}}, key=False)[0], 403, call)

    def test_github_import_make_quest_and_close(self):
        self.assertEqual(self.req("POST", "/api/integrations/refresh", {"source": "github"})[0], 400)         # off until the owner turns it on
        st, v, text = self.req("POST", "/api/integrations", {"settings": {"github": {"enabled": True, "mode": "token", "token": TOKEN, "prs": "review_requested"}}})
        self.assertEqual((st, v["github"]["enabled"], v["github"]["token_set"]), (200, True, True))
        self.assertNotIn(TOKEN, text)
        st, v, _ = self.req("POST", "/api/integrations/refresh", {"source": "github"})
        self.assertEqual((v["status"]["github"]["ok"], v["status"]["github"]["who"], v["status"]["github"]["how"]), (True, "octo-owner", "demo"))
        _, s, text = self.req("GET", "/api/state")
        self.assertNotIn(TOKEN, text)
        self.assertEqual([b["key"] for b in s["bounties"]], ["#128", "#131", "#135", "#140"])
        self.assertEqual((s["ci"]["state"], s["ci"]["name"]), ("passed", "build"))
        self.assertEqual(s["tasks"], [])                                              # importing creates nothing
        self.assertEqual(self.req("POST", "/api/bounty/quest", {"id": "https://github.com/goldshire/inn-ledger/issues/999"})[0], 400)
        st, s, _ = self.req("POST", "/api/bounty/quest", {"id": "https://github.com/goldshire/inn-ledger/issues/128"})
        self.assertEqual(st, 200)
        (q,) = s["tasks"]
        self.assertEqual((q["id"], q["title"], q["approved"], q["status"]), (s["quest"], "Ledger total is off by one copper after a refund", False, "new"))
        self.assertTrue(q["why"].startswith("GitHub #128: https://github.com/goldshire/inn-ledger/issues/128 - Steps: buy"))
        self.assertNotIn("#128", [b["key"] for b in s["bounties"]])
        self.assertEqual(self.req("POST", "/api/bounty/quest", {"id": "https://github.com/goldshire/inn-ledger/issues/128"})[0], 400)    # once
        evs = [e for e in Feed(self.feed).read_since(0, 100)]
        self.assertEqual([e.type for e in evs], ["task_created"])
        # #128 and #131 closed upstream: their bounties go, the quest stays
        with open(FIXTURE, encoding="utf-8") as fh:
            fx = json.load(fh)
        fx["issues"] = fx["issues"][2:]
        p = os.path.join(self.tmp.name, "fx.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(fx, fh)
        self.ctx.integ.env = {"VANILLA_INTEGRATIONS_FAKE": p}
        _, v, _ = self.req("POST", "/api/integrations/refresh", {"source": "github"})
        _, s, _ = self.req("GET", "/api/state")
        self.assertEqual([b["key"] for b in s["bounties"]], ["#135", "#140"])
        self.assertEqual(len(s["tasks"]), 1)
        self.assertEqual(self.req("POST", "/api/bounty/hide", {"id": "https://github.com/goldshire/inn-ledger/pull/140"})[1]["bounties"][0]["key"], "#135")
        self.assertNotIn(TOKEN, diagnostics.report(self.ctx))
        self.assertIn("github on (token), 1 imported", diagnostics.report(self.ctx))

    def test_csv_import_and_size_limit(self):
        from tests.test_integrations_sources import JIRA_CSV
        st, v, _ = self.req("POST", "/api/integrations/import", {"text": JIRA_CSV})
        self.assertEqual((st, v["imported"]), (200, {"kind": "jira", "count": 2, "skipped": 1}))
        _, s, _ = self.req("GET", "/api/state")
        self.assertEqual([b["id"] for b in s["bounties"]], ["jira:INN-12", "jira:INN-14"])
        _, s, _ = self.req("POST", "/api/bounty/quest", {"id": "jira:INN-12"})
        self.assertTrue(s["tasks"][0]["why"].startswith("Jira INN-12: from an imported file - It drips."))
        self.assertEqual(self.req("POST", "/api/integrations/import", {"text": "nothing,here\n1,2\n"})[0], 400)
        self.ctx.integ.o["max_file_bytes"] = 100
        self.assertEqual(self.req("POST", "/api/integrations/import", {"text": JIRA_CSV})[0], 400)
        self.assertEqual(self.req("POST", "/api/whisper", raw=json.dumps({"to": "x", "text": "y" * 5000}))[0], 400)      # other calls keep the small limit

    def test_test_button_and_secrets_never_returned(self):
        self.ctx.integ.env = {}
        r = self.req("POST", "/api/integrations/test", {"service": "github", "settings": {"github": {"mode": "gh"}}})[1]
        self.assertEqual(r["ok"], False)                                              # no project: no GitHub remote
        self.req("POST", "/api/integrations", {"settings": {"linear": {"api_key": LIN}, "jira": {"token": "ATATT-very-secret-1"}}})
        _, v, text = self.req("GET", "/api/integrations")
        self.assertTrue(v["linear"]["api_key_set"])
        for s in (LIN, "ATATT-very-secret-1"):
            self.assertNotIn(s, text)
            self.assertNotIn(s, self.req("GET", "/api/state")[2])
            self.assertNotIn(s, diagnostics.report(self.ctx))


class Hub(unittest.TestCase):
    """The real gh path against a project folder with a GitHub remote, through a fake runner; and the background pass."""

    def test_gh_refresh_and_tick(self):
        proj = gitdir([("origin", "git@github.com:octo/inn.git")], "ref: refs/heads/main")
        os.makedirs(os.path.join(proj, ".github", "workflows"))
        open(os.path.join(proj, ".github", "workflows", "ci.yml"), "w").close()
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.json")
            r = Runner({"api user": (0, '{"login": "octo"}', ""), "issue list": (0, '[{"number": 1, "title": "One", "url": "https://github.com/octo/inn/issues/1"}]', ""),
                        "run list": (0, '[{"workflowName": "build", "status": "in_progress", "createdAt": "2026-10-19T08:00:00Z", "headBranch": "main"}]', "")})
            from vanilla_agents.integrations.hub import Hub as H
            now = [1000.0]
            h = H(f, lambda: proj, env={}, runner=r, which=which, clock=lambda: now[0])
            h.tick()
            self.assertEqual(r.calls, [])                                          # everything off: nothing runs
            isettings.save(f, {"github": {"enabled": True, "prs": "off"}})
            h.tick()
            self.assertEqual(h.status["github"]["who"], "octo")
            self.assertEqual(h.view()["ci"]["state"], "running")
            self.assertEqual([b["key"] for b in h.view()["bounties"]], ["#1"])
            self.assertFalse(any(c[1:3] == ["pr", "list"] for c in r.calls))
            n = len(r.calls)
            now[0] += 60
            h.tick()
            self.assertEqual([c[1:3] for c in r.calls[n:]], [])                    # not due yet (CI every 120 s, lists every 10 min)
            now[0] += 100
            h.tick()
            self.assertEqual([c[1:3] for c in r.calls[n:]], [["run", "list"]])
            r.answers["issue list"] = (1, "", "HTTP 502 bad gateway")
            now[0] += 700
            h.tick()
            self.assertIn("502", h.status["github"]["error"])
            self.assertEqual(len(h.view()["bounties"]), 1)                          # a failed refresh keeps what was there


if __name__ == "__main__":
    unittest.main()
