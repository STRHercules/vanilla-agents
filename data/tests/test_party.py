"""Party: hub rules (join with the invite, any change clears agreement, lock + balanced split when all active members agree, take,
report only your own), and two PCs over real HTTP (host + join, plan agreed, each side gets its share as a quest in its own feed)."""
import json
import os
import tempfile
import unittest

from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed
from vanilla_agents.party import client as pc
from vanilla_agents.party import hub


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Rules(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.p = hub.Party(None, "Raid night", clock=self.c)
        self.a = self.p.join(self.p.d["invite"], "Ann")["key"]
        self.b = self.p.join(self.p.d["invite"], "Bo")["key"]

    def test_invite_and_keys_are_required(self):
        with self.assertRaises(PermissionError):
            self.p.join("wrong", "Eve")
        with self.assertRaises(PermissionError):
            self.p.state("not-a-key")

    def test_change_clears_agreement_and_all_must_agree(self):
        self.p.propose(self.a, "Write login tests", size="small")
        v = self.p.state(self.a)["version"]
        self.p.agree(self.a, v)
        self.assertFalse(self.p.state(self.a)["locked"])
        self.p.propose(self.b, "Fix the save bug", size="big")          # a change: Ann's yes no longer counts
        st = self.p.state(self.a)
        self.assertFalse(any(m["agrees"] for m in st["members"]))
        with self.assertRaises(ValueError):
            self.p.agree(self.a, v)                                        # agreeing to an old version is refused
        v = st["version"]
        self.p.agree(self.a, v)
        st = self.p.agree(self.b, v)
        self.assertTrue(st["locked"])
        owners = {i["title"]: i["owner"] for i in st["items"]}
        self.assertEqual(sorted(owners.values()), ["m1", "m2"])          # one each
        with self.assertRaises(ValueError):
            self.p.propose(self.a, "More")                                 # locked

    def test_split_balances_by_size_and_inactive_members_do_not_block(self):
        c = self.p.join(self.p.d["invite"], "Cy")["key"]
        for t, s in (("A", "big"), ("B", "big"), ("C", "small"), ("D", "small"), ("E", "medium")):
            self.p.propose(self.a, t, size=s)
        self.c.t += hub.ACTIVE_S + 5                                       # everyone goes quiet...
        self.p.state(self.a)                                               # ...Ann and Bo come back, Cy does not
        self.p.state(self.b)
        v = self.p.state(self.a)["version"]
        self.p.agree(self.a, v)
        st = self.p.agree(self.b, v)
        self.assertTrue(st["locked"])
        load = {}
        for i in st["items"]:
            load[i["owner"]] = load.get(i["owner"], 0) + hub.SIZE_WEIGHT[i["size"]]
        self.assertEqual(set(load), {"m1", "m2"})
        self.assertLessEqual(abs(load["m1"] - load["m2"]), 1)
        self.assertNotIn("m3", load)
        self.assertTrue(c)

    def test_take_and_report(self):
        it = self.p.propose(self.a, "Only item")
        with self.assertRaises(ValueError):
            self.p.take(self.b, it["id"])                                  # not split yet
        v = self.p.state(self.a)["version"]
        self.p.agree(self.a, v)
        self.p.agree(self.b, v)
        owner = self.p.state(self.a)["items"][0]["owner"]
        other = self.b if owner == "m1" else self.a
        with self.assertRaises(PermissionError):
            self.p.report(other, it["id"], "progress")                     # not theirs
        self.p.take(other, it["id"])
        self.p.report(other, it["id"], "progress")
        with self.assertRaises(ValueError):
            self.p.take(self.a if other == self.b else self.b, it["id"])   # started: cannot be taken away
        self.assertEqual(self.p.state(self.a)["items"][0]["status"], "progress")

    def test_leave_returns_unstarted_work(self):
        self.p.propose(self.a, "X")
        v = self.p.state(self.a)["version"]
        self.p.agree(self.a, v)
        self.p.agree(self.b, v)
        owner_key = self.a if self.p.state(self.a)["items"][0]["owner"] == "m1" else self.b
        self.p.leave(owner_key)
        survivor = self.b if owner_key == self.a else self.a
        self.assertIsNone(self.p.state(survivor)["items"][0]["owner"])


class Safety(unittest.TestCase):
    """Host flag, invite expiry and renewal, removing a member (V-033)."""

    def setUp(self):
        self.c = Clock()
        self.p = hub.Party(None, "Raid night", clock=self.c, invite_hours=2)
        self.h = self.p.join(self.p.d["invite"], "Host", self.p.d["host_token"])
        self.a = self.p.join(self.p.d["invite"], "Ann")
        self.b = self.p.join(self.p.d["invite"], "Bo")

    def test_host_is_explicit(self):
        self.assertTrue(self.h["host"])
        self.assertFalse(self.a["host"])
        st = self.p.state(self.a["key"])
        self.assertEqual(st["host"], self.h["member"])
        self.assertNotIn("invite_token", st)                               # only the host sees the invite
        self.assertEqual(self.p.state(self.h["key"])["invite_token"], self.p.d["invite"])
        self.assertFalse(self.p.join(self.p.d["invite"], "Eve", "wrong")["host"])

    def test_invite_expires_and_renew_keeps_members(self):
        self.c.t += 2 * 3600 + 1
        with self.assertRaises(PermissionError):
            self.p.join(self.p.d["invite"], "Late")
        old = self.p.d["invite"]
        with self.assertRaises(PermissionError):
            self.p.renew_invite(self.a["key"])                             # not the host
        st = self.p.renew_invite(self.h["key"], hours=5)
        self.assertNotEqual(st["invite_token"], old)
        self.assertEqual(st["invite_expires"], self.c.t + 5 * 3600)
        with self.assertRaises(PermissionError):
            self.p.join(old, "Late")                                       # the old link stops working
        self.p.join(st["invite_token"], "Late")
        self.p.state(self.a["key"])                                        # members already in keep their keys

    def test_kick(self):
        a, b = self.a["key"], self.b["key"]
        for t in ("A", "B", "C"):
            self.p.propose(a, t)
        v = self.p.state(a)["version"]
        for k in (self.h["key"], a, b):
            self.p.agree(k, v)
        items = self.p.state(a)["items"]
        started = next(i for i in items if i["owner"] == self.b["member"])
        self.p.report(b, started["id"], "progress")
        with self.assertRaises(PermissionError):
            self.p.kick(a, self.b["member"])                               # only the host may remove
        with self.assertRaises(ValueError):
            self.p.kick(self.h["key"], self.h["member"])
        old = self.p.d["invite"]
        st = self.p.kick(self.h["key"], self.b["member"])
        self.assertNotIn(self.b["member"], [m["id"] for m in st["members"]])
        self.assertNotEqual(st["invite_token"], old)                       # the old link cannot bring them back
        for i in st["items"]:
            if i["id"] == started["id"]:
                self.assertEqual(i["owner"], self.b["member"])             # started work keeps its owner
        self.assertTrue(all(i["owner"] != self.b["member"] or i["status"] != "open" for i in st["items"]))
        with self.assertRaises(PermissionError) as e:
            self.p.state(b)                                                # their key stops working
        self.assertIn("removed", str(e.exception))

    def test_old_saved_party_gets_a_host(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hub.json")
            p = hub.Party(path, "Old")
            p.join(p.d["invite"], "First")
            p.d["host"] = None
            p.save()
            self.assertEqual(hub.Party(path).d["host"], "m1")


class OverHttp(unittest.TestCase):
    def test_host_local_only_kick_and_renew(self):
        cfg = load_all()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "ann")), os.makedirs(os.path.join(d, "bo"))
            mk = lambda who: pc.PartyClient(os.path.join(d, who), os.path.join(d, who, "feed.jsonl"), cfg, period=999)
            ann, bo = mk("ann"), mk("bo")
            try:
                link = ann.host("Raid night", "Ann", port=0, network=False, hours=3)
                self.assertEqual(ann.hub_srv.server_address[0], "127.0.0.1")    # only this PC
                self.assertTrue(link.startswith("http://127.0.0.1:"))
                self.assertNotIn("&h=", link)                                   # the host secret never shows
                v = ann.view()
                self.assertTrue(v["host"] and v["local"] and v["invite_expires"])
                bo.join(link, "Bo")
                self.assertFalse(bo.view()["host"])
                self.assertEqual(bo.view()["invite"], "")
                with self.assertRaises(pc.PartyError):
                    bo.act("kick", {"member": ann.m["member"]})
                ann.act("kick", {"member": bo.m["member"]})
                self.assertNotEqual(ann.view()["invite"], link)                 # renewed
                bo.sync()
                self.assertIn("removed", bo.error)
                before = ann.view()["invite"]
                ann.act("renew", {})
                self.assertNotEqual(ann.view()["invite"], before)
            finally:
                bo.leave()
                ann.leave()


    def test_host_join_agree_and_quests_arrive(self):
        cfg = load_all()
        with tempfile.TemporaryDirectory() as d:
            mk = lambda who: pc.PartyClient(os.path.join(d, who), os.path.join(d, who, "feed.jsonl"), cfg, period=999)
            os.makedirs(os.path.join(d, "ann")), os.makedirs(os.path.join(d, "bo"))
            ann, bo = mk("ann"), mk("bo")
            # port 0 picks a free port: host by hand so the test never collides with a real party on 8940
            ann._start_hub(os.path.join(d, "ann", "party-hub.json"), 0, "Raid night")
            port = ann.hub_srv.server_address[1]
            link = "http://127.0.0.1:%d/party?t=%s" % (port, ann.party.d["invite"])
            try:
                ann.join(link, "Ann")
                bo.join(link, "Bo")
                ann.act("propose", {"title": "Write login tests", "size": "small"})
                bo.act("propose", {"title": "Fix the save bug", "size": "medium"})
                v = ann.sync()["version"]
                ann.act("agree", {"version": v})
                st = bo.act("agree", {"version": v})
                self.assertTrue(st["locked"])
                ann.sync()
                bo.sync()
                for who, c in (("ann", ann), ("bo", bo)):
                    evs = Feed(os.path.join(d, who, "feed.jsonl")).read_since(0)
                    titles = [e.data["title"] for e in evs if e.type == "task_created"]
                    self.assertEqual(len(titles), 1, who)                    # exactly its own share, once
                    self.assertIn("Party plan (Raid night)", [e.data.get("why", "") for e in evs][0])
                    with open(os.path.join(d, who, "party-state.json")) as f:
                        self.assertTrue(json.load(f)["state"]["locked"])     # the cache the MCP tool reads
                with self.assertRaises(pc.PartyError):
                    pc.parse_invite("hello")
            finally:
                bo.leave()
                ann.leave()
            self.assertFalse(os.path.exists(os.path.join(d, "ann", "party-member.json")))


if __name__ == "__main__":
    unittest.main()
