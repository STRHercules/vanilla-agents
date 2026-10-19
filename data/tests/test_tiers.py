"""Approval tiers (D-370..D-399): the decision table, word boundaries, size rules, never-cases, who/why records, real approvals only (feed lines approve nothing),
the per-launch key, stale Idea keys, other projects' quests, undo, explain/change requests, the Idea summary. Temp dirs only."""
import contextlib
import http.client
import io
import json
import os
import unittest
from pathlib import Path

from vanilla_agents.app import approvals, tiers
from vanilla_agents.app.config import load_all
from vanilla_agents.onboard import idea
from tests.test_idea import Api

CFG = load_all()
RULES, UI, ROUTE = CFG["app"]["tiers"], CFG["strings"]["ui"], CFG["app"]["route"]


def tier(title, size="small", risk="low", oos=(), **kw):
    return tiers.decide({"title": title, "size": size, "risk": risk, **kw}, RULES, UI, ROUTE, oos)


class Decide(unittest.TestCase):
    def test_auto_when_small_low_risk_in_scope(self):
        r = tier("Add a welcome message to the lobby")
        self.assertEqual((r["tier"], r["why"]), ("auto", UI["tier.auto"]))
        self.assertEqual(tier("Tidy `src/a.rs`", oos=["legacy/**"])["tier"], "auto")

    def test_ask_cases(self):
        for kw, code in (({"size": "big"}, "big"), ({"size": "medium"}, "big"), ({"size": ""}, "unknown"), ({"risk": "high"}, "high"), ({"risk": None}, "unknown"),
                         ({"status": "failed"}, "failed"), ({"sent_back": [{"check": "x"}]}, "failed"), ({"vetoed": True}, "vetoed"),
                         ({"explain_pending": True}, "asked"), ({"change_pending": True}, "asked")):
            r = tier("Add a welcome message", **kw)
            self.assertEqual((r["tier"], r["code"]), ("ask", code), kw)
        self.assertEqual(tier("Change how passwords are stored")["code"], "word")
        self.assertEqual(tier("Remove the old menu")["tier"], "ask")                  # deleting inside the project is only risky

    def test_only_small_can_be_automatic_even_if_the_config_forgot_medium(self):
        rules = {**RULES, "ask_sizes": ["big"]}                                        # the D-371 / ask_sizes mismatch: medium must still ask
        r = tiers.decide({"title": "Add a welcome message", "size": "medium", "risk": "low"}, rules, UI, ROUTE)
        self.assertEqual((r["tier"], r["code"]), ("ask", "unknown"))
        self.assertEqual(CFG["app"]["tiers"]["ask_sizes"], ["medium", "big"])

    def test_scope_is_found_without_backticks(self):
        oos = ["legacy/**", "vendor/*.js"]
        for title in ("Edit legacy/old.js", "Tidy the legacy folder", "Fix vendor/lib.js", "Clean up `legacy/old.js`"):
            self.assertEqual(tier(title, oos=oos)["code"], "scope", title)
        self.assertEqual(tier("Fix vanilla_agents/verify/run.py")["code"], "trusted")
        self.assertEqual(tier("Fix `vanilla_agents/verify/run.py`")["code"], "trusted")
        self.assertEqual(tier("Add a legacy-friendly welcome", oos=oos)["tier"], "auto")      # 'legacy-friendly' is not the folder
        self.assertEqual(tier("Tidy src/a.rs", oos=oos)["tier"], "auto")

    def test_never_cases(self):
        for title, cat in (("Push the code to GitHub", "push"), ("git push --force the branch", "push"), ("Publish the game for everyone", "publish"),
                           ("Start public hosting of the server", "publish"), ("Take a payment for the game", "spend"), ("Charge real money for gold", "spend"),
                           ("Add a monthly subscription", "spend"), ("Set up billing", "spend"), ("Purchase a domain name", "spend"),
                           ("Pushing the build to the server", "push"), ("Delete `C:/Users/me/Documents` to free space", "delete"), ("wipe ../other-project clean", "delete")):
            r = tier(title)
            self.assertEqual((r["tier"], r["code"]), ("never", cat), title)
        self.assertEqual(tier("Big rewrite, then push", size="big")["tier"], "never")     # never beats ask
        self.assertNotEqual(tier("Delete the unused `src/old.rs`")["tier"], "never")

    def test_whole_words_only(self):
        for title in ("Fix the pushback when a monster is hit", "Show the author name on the credits page", "Update the authors list",
                      "Add buying and selling between players", "Vendors: buying and selling items", "Players can spend gold at the vendor",
                      "Pay for repairs with in-game gold", "Fix the tokenizer", "Add a publisher credit"):
            self.assertEqual(tier(title)["tier"], "auto", title)
        self.assertEqual(tier("Add authentication")["code"], "word")
        self.assertEqual(tier("Fix the login screen")["code"], "word")
        self.assertEqual(tier("Remove the old menus")["code"], "word")                 # plural of a risky word still counts
        self.assertEqual(tier("Deleting `C:\\temp\\x`")["tier"], "never")                # -ing ending

    def test_never_is_a_gate_on_automatic_approval_not_a_ban_on_the_owner(self):
        self.assertIn("never start by itself", CFG["strings"]["ui"]["tier.never_push"] + " never start by itself")
        self.assertIn("Only you can say yes", UI["quest.tier_never"])


class Flow(unittest.TestCase):
    call, onboard, approve = Api.call, Api.onboard, Api.approve

    def setUp(self):
        Api.setUp(self)
        tiers._PLAN_TTL = 0

    def tearDown(self):
        tiers._PLAN_TTL = 2.0
        Api.tearDown(self)

    def plan(self, *quests):
        """The project's plan: (title, size, risk) per quest, written as the roadmap and quests.json BEFORE onboarding. Returns the quest ids."""
        (Path(self.repo) / "README.md").write_text("# Demo\n\nDemo is a tiny game server for friends.\n", encoding="utf-8")
        (Path(self.repo) / "ROADMAP.md").write_text("# Roadmap\n\n" + "".join("%d. %s\n" % (i, q[0]) for i, q in enumerate(quests, 1)), encoding="utf-8")
        ids = [idea.quest_id("demo", q[0]) for q in quests]
        self.set_meta({i: (q[1], q[2]) for i, q in zip(ids, quests)})
        return ids

    def set_meta(self, meta):
        d = Path(self.idea_file).parent
        d.mkdir(parents=True, exist_ok=True)
        (d / "quests.json").write_text(json.dumps({i: {"size": s, "risk": r} for i, (s, r) in meta.items()}), encoding="utf-8")

    def tasks(self):
        self.ctx.state.refresh()
        return {t["id"]: t for t in self.call("GET", "/api/state")[1]["tasks"]}

    def store(self):
        return approvals.load(self.ctx.cfg["app"]["approvals_path"], self.ctx.state.project)

    def start(self):
        self.onboard()
        return self.approve()

    def raw_post(self, path, body, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, json.dumps(body), {"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    # --- approvals are real ---------------------------------------------------------------------------------------------------------
    def test_auto_quest_proceeds_and_records_who_and_why(self):
        (a1,) = self.plan(("Add a welcome message", "small", "low"))
        st, d = self.start()
        self.assertEqual((st, d["auto_approved"]), (200, 1))
        t = self.tasks()[a1]
        self.assertEqual((t["approved"], t["approved_by"], t["vetoable"], t["tier"]), (True, "auto", True, None))
        self.assertEqual((self.store()[a1]["who"], self.store()[a1]["why"]), ("auto", UI["tier.auto"]))
        snap = self.call("GET", "/api/state")[1]
        self.assertTrue(any("Auto-approved: Add a welcome message" in c["text"] for c in snap["chat"]))
        self.assertIn(("auto", 1), [(a["id"], a["count"]) for a in snap["auras"]["player"]])
        self.assertEqual(self.approve()[1]["auto_approved"], 0)     # once only

    def test_feed_lines_alone_approve_nothing(self):
        (q,) = self.plan(("Rebuild the whole lobby", "big", "low"))
        self.start()
        feed = self.ctx.state.feed
        feed.append("plan_approved", task=q, data={"title": "x", "who": "you"})              # forged: an agent can append to the feed
        feed.append("quest_auto_approved", task=q, data={"title": "x", "why": "forged"})
        t = self.tasks()[q]
        self.assertEqual((t["approved"], t["approved_by"]), (False, None))
        self.assertNotIn(q, self.store())
        self.ctx.open_project(self.repo)                                                       # a restart replays the same feed: still nothing
        self.assertFalse(self.tasks()[q]["approved"])
        feed.append("plan_approved", task="idea:000000000000", data={"title": "the Idea"})     # a forged Idea approval changes nothing either
        self.assertNotEqual(self.call("GET", "/api/idea")[1]["state"], "changed")
        self.assertEqual(self.call("POST", "/api/approve", {"task": q}, None)[0], 200)         # the real call still works
        self.assertTrue(self.tasks()[q]["approved"])
        self.assertEqual(self.store()[q]["who"], "you")

    def test_forged_feed_line_cannot_bring_back_an_undone_yes(self):
        (q,) = self.plan(("Add a welcome message", "small", "low"))
        self.start()
        self.assertEqual(self.call("POST", "/api/quest", {"task": q, "action": "veto"})[0], 200)
        self.ctx.state.feed.append("quest_auto_approved", task=q, data={"title": "x", "why": "forged"})
        t = self.tasks()[q]
        self.assertEqual((t["approved"], t["vetoed"]), (False, True))

    def test_nothing_is_auto_approved_before_the_idea_is_approved(self):
        (a1,) = self.plan(("Add a welcome message", "small", "low"))
        self.onboard()
        self.ctx.state.feed.append("task_created", task=a1, data={"title": "Add a welcome message", "size": "small", "risk": "low"})
        self.assertEqual(tiers.sweep(self.ctx), 0)
        self.assertFalse(self.tasks()[a1]["approved"])

    def test_only_plan_quests_are_ever_swept_and_their_current_size_counts(self):
        (m,) = self.plan(("Add a welcome message", "medium", "low"))
        self.start()
        feed = self.ctx.state.feed
        feed.append("task_created", task="stray", data={"title": "Add a goodbye message", "size": "small", "risk": "low"})      # not a plan item
        self.assertEqual(tiers.sweep(self.ctx), 0)
        t = self.tasks()
        self.assertEqual((t[m]["approved"], t[m]["tier"]["code"], t["stray"]["approved"], t["stray"]["tier"]["tier"]), (False, "big", False, "ask"))
        self.set_meta({m: ("small", "low")})                                      # the owner edits quests.json: the seeded copy in the feed is old
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "changed")    # D-381: that is an Idea change
        self.assertEqual(self.tasks()[m]["tier"]["tier"], "auto")                 # the window already shows the current size
        self.assertEqual(self.approve()[1]["auto_approved"], 1)                   # and the new approval uses it
        self.assertTrue(self.tasks()[m]["approved"])
        self.assertFalse(self.tasks()["stray"]["approved"])

    def test_medium_asks_and_owner_yes_is_recorded(self):
        (b1,) = self.plan(("Rebuild the whole lobby", "medium", "low"))
        self.start()
        t = self.tasks()[b1]
        self.assertEqual((t["approved"], t["tier"]["tier"], t["tier"]["why"]), (False, "ask", UI["tier.ask_big"]))
        self.assertEqual(self.call("POST", "/api/approve", {"task": b1})[0], 200)
        self.assertEqual((self.store()[b1]["who"], self.store()[b1]["why"]), ("you", "approved by you"))
        self.assertEqual(self.tasks()[b1]["approved_by"], "you")

    def test_never_quest_is_not_automatic_but_the_owner_can_still_say_yes(self):
        (n1,) = self.plan(("Push the code to GitHub", "small", "low"))
        self.start()
        t = self.tasks()[n1]
        self.assertEqual((t["tier"]["tier"], t["tier"]["why"], t["approved"]), ("never", UI["tier.never_push"], False))
        self.assertNotIn(n1, self.store())
        self.assertEqual(self.call("POST", "/api/approve", {"task": n1})[0], 200)      # D-387: explicit confirmation is allowed
        self.assertEqual(self.store()[n1]["who"], "you")

    def test_trading_text_is_not_a_never_quest(self):
        (q,) = self.plan(("Add buying and selling between players", "small", "low"))
        self.start()
        self.assertTrue(self.tasks()[q]["approved"])

    # --- undo until done ---------------------------------------------------------------------------------------------------------
    def test_undo_works_until_the_quest_is_done_and_it_is_not_auto_approved_again(self):
        v1, v2 = self.plan(("Add a welcome message", "small", "low"), ("Add a goodbye message", "small", "low"))
        self.start()
        self.assertEqual(self.call("POST", "/api/quest", {"task": v1, "action": "veto"})[0], 200)
        t = self.tasks()[v1]
        self.assertEqual((t["approved"], t["vetoed"], t["tier"]["code"]), (False, True, "vetoed"))
        self.assertTrue(self.store()[v1]["vetoed"])
        self.assertEqual(self.approve()[1]["auto_approved"], 0)
        self.assertEqual(self.call("POST", "/api/quest", {"task": v1, "action": "veto"})[0], 400)       # nothing left to undo
        self.ctx.state.feed.append("check_started", task=v2, data={"check": "unit"})                   # D-385: a check running does not end the undo window ...
        self.assertTrue(self.tasks()[v2]["vetoable"])
        self.ctx.state.feed.append("task_verified", task=v2, data={"title": "Add a goodbye message"})  # ... being done does
        self.assertFalse(self.tasks()[v2]["vetoable"])
        self.assertEqual(self.call("POST", "/api/quest", {"task": v2, "action": "veto"})[0], 400)
        self.assertEqual(self.call("POST", "/api/approve", {"task": v1})[0], 200)                      # the owner can still say yes later
        self.assertEqual(self.tasks()[v1]["approved_by"], "you")

    # --- explain / change ------------------------------------------------------------------------------------------------------------
    def test_explain_and_change_requests_and_answers(self):
        (e1,) = self.plan(("Rebuild the whole lobby", "big", "low"))
        self.start()
        self.assertEqual(self.call("POST", "/api/quest", {"task": e1, "action": "explain"})[0], 200)
        self.call("POST", "/api/quest", {"task": e1, "action": "change"})
        evs = [(e.type, e.task) for e in self.ctx.state.feed.read_since(0, 1000) if e.type.endswith("_requested")]
        self.assertEqual(evs, [("explain_requested", e1), ("change_requested", e1)])
        self.assertTrue(any("plainer explanation" in c["text"] for c in self.call("GET", "/api/state")[1]["chat"]))
        t = self.tasks()[e1]
        self.assertEqual((t["approved"], t["explain_pending"], t["change_pending"]), (False, True, True))
        self.assertEqual(self.call("POST", "/api/quest", {"task": e1, "action": "bogus"})[0], 400)
        self.assertEqual(self.call("POST", "/api/quest", {"task": "nope", "action": "explain"})[0], 400)
        from scripts import answer_explain
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(answer_explain.main([e1, "It is the first screen friends see."], feed_path=str(self.ctx.state.feed.path)), 0)
            self.assertEqual(answer_explain.main([e1, "--changed", "Made it smaller."], feed_path=str(self.ctx.state.feed.path)), 0)
        t = self.tasks()[e1]
        self.assertEqual((t["answer"], t["explain_pending"], t["change_pending"], t["title"]), ("It is the first screen friends see.", False, False, "Rebuild the whole lobby"))

    def test_change_it_takes_an_automatic_yes_back_to_the_ask_tier(self):
        (c1,) = self.plan(("Add a welcome message", "small", "low"))
        self.start()
        self.assertTrue(self.tasks()[c1]["approved"])
        self.assertEqual(self.call("POST", "/api/quest", {"task": c1, "action": "change"})[0], 200)
        t = self.tasks()[c1]
        self.assertEqual((t["approved"], t["vetoed"], t["tier"]["tier"]), (False, True, "ask"))

    def test_change_note_reaches_the_claude_session_text(self):
        from vanilla_agents.events import hooks
        (c1,) = self.plan(("Add a welcome message", "small", "low"))
        self.start()
        self.assertEqual(self.call("POST", "/api/quest", {"task": c1, "action": "change", "note": "make it say hello to new players"})[0], 200)
        txt = hooks.pending_text(self.ctx.state.feed)
        self.assertIn("make it say hello to new players", txt)
        self.call("POST", "/api/quest", {"task": c1, "action": "explain"})
        self.assertIn("ELI5", hooks.pending_text(self.ctx.state.feed))

    def test_open_question_blocks_automatic_approval(self):
        (q,) = self.plan(("Add a welcome message", "small", "low"))
        self.onboard()
        self.ctx.state.feed.append("task_created", task=q, data={"title": "Add a welcome message", "size": "small", "risk": "low"})
        self.ctx.state.feed.append("explain_requested", task=q, data={"title": "Add a welcome message"})
        self.assertEqual(self.approve()[1]["auto_approved"], 0)
        self.assertEqual(self.tasks()[q]["tier"]["code"], "asked")

    # --- the Idea -----------------------------------------------------------------------------------------------------------------------
    def test_idea_summary_text(self):
        self.onboard()
        s = self.call("GET", "/api/idea")[1]["summary"]
        self.assertTrue(s.startswith("If you say yes: we build ") and "We will NOT build " in s and s.endswith("."))
        self.assertIn("It is done when ", s)
        d = {"goal": "a card game in a browser.", "non_goals": ["No real money", "No chat."], "done_means": ["A friend can join with a link"], "rules": [], "out_of_scope": []}
        self.assertEqual(idea.summary(d, UI), "If you say yes: we build a card game in a browser. We will NOT build No real money; No chat. It is done when A friend can join with a link. No quest will start by itself; you say yes to each one.")
        e = idea.summary({"goal": "", "non_goals": [], "done_means": [], "rules": [], "out_of_scope": []}, UI)
        self.assertIn("not written yet", e)

    def test_summary_lists_the_quests_that_will_start_by_themselves(self):
        self.plan(("Add a welcome message", "small", "low"), ("Add a goodbye message", "small", "low"), ("Rebuild the whole lobby", "medium", "low"))
        self.onboard()
        d = self.call("GET", "/api/idea")[1]
        self.assertEqual(sorted(d["auto"]), ["Add a goodbye message", "Add a welcome message"])
        self.assertIn("2 quests will start by themselves: ", d["summary"])
        self.assertNotIn("Rebuild", d["summary"].split("start by themselves")[1])
        self.plan(("Add a welcome message", "small", "low"), ("Rebuild the whole lobby", "medium", "low"))
        d = self.call("GET", "/api/idea")[1]
        self.assertIn("1 quest will start by itself: Add a welcome message.", d["summary"])
        self.plan(("Rebuild the whole lobby", "medium", "low"))
        self.assertIn("No quest will start by itself", self.call("GET", "/api/idea")[1]["summary"])

    def test_stale_idea_key_is_refused(self):
        self.plan(("Add a welcome message", "small", "low"))
        self.onboard()
        key = self.call("GET", "/api/idea")[1]["key"]
        text = Path(self.idea_file).read_text(encoding="utf-8")
        Path(self.idea_file).write_text(text + "- Nothing about crypto\n", encoding="utf-8")        # edited after the window showed it
        st, d = self.call("POST", "/api/idea/approve", {"key": key})
        self.assertEqual(st, 400)
        self.assertIn("changed", d["error"])
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "unapproved")
        self.assertEqual(self.call("POST", "/api/idea/approve", {})[0], 400)                         # no key at all
        self.assertEqual(self.call("POST", "/api/idea/approve", {"key": 5})[0], 400)
        self.assertEqual(self.approve()[0], 200)                                                      # the key it shows now works

    def test_quests_json_edits_show_as_idea_changed(self):
        (q,) = self.plan(("Rebuild the whole lobby", "medium", "low"))
        self.start()
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "approved")
        self.set_meta({q: ("medium", "high")})
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "changed")
        self.set_meta({q: ("medium", "low")})
        self.assertEqual(self.call("GET", "/api/idea")[1]["state"], "approved")

    def test_idea_without_a_goal_is_refused(self):
        self.onboard()
        Path(self.idea_file).write_text("# Idea\n\n## What we are building\n(write one sentence)\n", encoding="utf-8")
        st, d = self.approve()
        self.assertEqual(st, 400)
        self.assertIn("no goal", d["error"])

    # --- other projects ----------------------------------------------------------------------------------------------------------------
    def test_other_projects_quests_are_not_shown_or_approved(self):
        (mine,) = self.plan(("Add a welcome message", "small", "low"))
        self.start()
        feed = self.ctx.state.feed
        feed.append("task_created", task="other:q:abc123", data={"title": "Add a goodbye message", "size": "small", "risk": "low"})
        feed.append("task_created", task="verify-other", data={"title": "Verify other"})
        feed.append("task_verified", task="verify-demo", data={"title": "Verify demo"})
        t = self.tasks()
        self.assertEqual(sorted(t), sorted([mine, "verify-demo"]))
        self.assertEqual(self.call("POST", "/api/approve", {"task": "other:q:abc123"})[0], 400)
        self.assertEqual(tiers.sweep(self.ctx), 0)
        self.assertNotIn("other:q:abc123", self.store())

    # --- the per-launch key ------------------------------------------------------------------------------------------------------------
    def test_every_state_changing_post_needs_the_key(self):
        (q,) = self.plan(("Rebuild the whole lobby", "big", "low"))
        self.start()
        key = self.ctx.key
        for path, body in (("/api/approve", {"task": q}), ("/api/quest", {"task": q, "action": "explain"}), ("/api/verify", {}), ("/api/pause", {"paused": True}),
                           ("/api/project", {"path": ""}), ("/api/onboard", {"path": self.repo}), ("/api/settings", {"master": {"volume": 0.5}}),
                           ("/api/idea/draft", {}), ("/api/idea/approve", {"key": "x"})):
            self.assertEqual(self.raw_post(path, body)[0], 403, path)                                             # no key
            self.assertEqual(self.raw_post(path, body, {"X-Vanilla-Key": "wrong"})[0], 403, path)                # wrong key
            self.assertEqual(self.raw_post(path, body, {"X-Vanilla-Key": key + "x"})[0], 403, path)
        self.assertFalse(self.tasks()[q]["approved"])
        self.assertEqual(self.raw_post("/api/approve", {"task": q}, {"X-Vanilla-Key": key})[0], 200)
        self.assertEqual(self.raw_post("/api/approve", {"task": q}, {"X-Vanilla-Key": key, "Content-Type": "application/json; charset=utf-8"})[0], 200)

    def test_key_is_never_served_and_content_type_is_exact(self):
        self.start()
        for path in ("/api/state", "/api/config", "/api/projects", "/api/idea", "/api/settings", "/", "/js/key.js", "/js/main.js", "/api/runs", "/api/checks"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", path)
            r = c.getresponse()
            body = r.read().decode("utf-8", "replace")
            self.assertNotIn(self.ctx.key, body, path)
            self.assertNotIn(self.ctx.key, str(r.getheaders()), path)
        h = {"X-Vanilla-Key": self.ctx.key}
        self.assertEqual(self.raw_post("/api/pause", {"paused": False}, {**h, "Content-Type": "text/plain; x=application/json"})[0], 404)
        self.assertEqual(self.raw_post("/api/pause", {"paused": False}, {**h, "Content-Type": "application/jsonx"})[0], 404)
        self.assertEqual(self.raw_post("/api/pause", {"paused": False}, {**h, "Content-Type": "Application/JSON; charset=utf-8"})[0], 200)
        self.assertEqual(self.raw_post("/api/pause", {"paused": False}, {**h, "Origin": "http://evil.example"})[0], 403)

    def test_each_launch_has_its_own_key(self):
        from vanilla_agents.app import server
        a, b = server.Ctx(self.ctx.cfg, self.ctx.feed_path, None, watch=False), server.Ctx(self.ctx.cfg, self.ctx.feed_path, None, watch=False)
        self.assertNotEqual(a.key, b.key)
        self.assertGreaterEqual(len(a.key), 32)
        a.stop.set(); b.stop.set()

    def test_the_window_gets_the_key_in_the_address_fragment(self):
        from vanilla_agents.app import launch, server
        seen = {}

        class FakeSrv:
            server_address = ("127.0.0.1", 4321)

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        real = (launch.open_window, server.make_server)
        launch.open_window = lambda url, cfg: seen.setdefault("url", url) and (_ for _ in ()).throw(RuntimeError("no window in tests"))
        server.make_server = lambda ctx, host, port: seen.setdefault("ctx", ctx) and FakeSrv()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(server.main(["--open", "--feed", self.ctx.feed_path, "--settings", os.path.join(self.tmp.name, "s2.json")]), 0)
        finally:
            launch.open_window, server.make_server = real
        self.assertEqual(seen["url"], "http://127.0.0.1:4321/#k=" + seen["ctx"].key)

    # --- decisions made from a phone session ---------------------------------------------------------------------------------------------
    def test_phone_session_decisions_are_recorded_as_such(self):
        from scripts import decide
        a, b = self.plan(("Add a welcome message", "small", "low"), ("Rebuild the whole lobby", "medium", "low"))
        self.onboard()
        key = self.call("GET", "/api/idea")[1]["key"]

        def run(*args, project="demo"):
            with contextlib.redirect_stdout(io.StringIO()):
                return decide.main(list(args) + ["--project", project], cfg=self.ctx.cfg, feed_path=self.ctx.feed_path)
        self.assertEqual(run("yes", "idea:wrongkey0000"), 1)                                  # a stale or wrong Idea key is refused here too
        self.assertEqual(run("yes", key), 0)
        self.assertEqual(self.tasks()[a]["approved_by"], "auto")
        self.assertEqual(run("approve", b), 0)
        self.assertEqual((self.store()[b]["who"], self.tasks()[b]["approved_by"]), ("you (via phone session)", "you (via phone session)"))
        self.assertEqual(self.store()[key]["who"], "you (via phone session)")
        self.assertEqual(run("pause", a), 0)
        self.assertEqual((self.tasks()[a]["approved"], self.tasks()[a]["vetoed"]), (False, True))
        self.assertEqual(run("approve", "nope"), 1)
        self.assertEqual(run("approve", b, project="ghost"), 1)

    def test_switched_off_means_no_tiers(self):
        self.plan(("Add a welcome message", "small", "low"))
        self.ctx.cfg["app"]["tiers"]["enabled"] = False
        self.start()
        t = self.tasks()
        self.assertTrue(t and all(not x["approved"] and "tier" not in x for x in t.values()))


if __name__ == "__main__":
    unittest.main()
