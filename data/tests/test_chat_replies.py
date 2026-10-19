"""V-050 chat replies (Discord / Slack / ntfy inbound): real-shaped answers of each service parsed, cursors kept in the work folder and a
first enable starting from now, the allow list and the ntfy passphrase (constant-time), command parsing (names with spaces, unknown
helper, empty), owner-only words never act (approve etc.; pause only when allowed), whispers through the in-process path, helper
answers forwarded back, 429 / Retry-After / X-RateLimit waits, garbled answers, secrets never in GET /api, diagnostics or error text.
A fake opener only: the test suite never touches the network."""
import email.message
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

from vanilla_agents.app import chatreplies as CR
from vanilla_agents.app import diagnostics, server, settings
from vanilla_agents.app.config import load_all
from vanilla_agents.events.feed import Feed
from vanilla_agents.integrations import chatcfg, chatsvc

DTOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.SECRET.discordBOTtoken9z9z"
STOKEN = "xoxb-1111-2222-SECRETslackTOKENabcd"
PASS = "zq9Kp4ss"
ME, STRANGER = "111111111111111111", "999999999999999999"
CHAN = "222222222222222222"


class Resp:
    def __init__(self, body, status=200, headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.status, self.headers = status, headers or {}

    def read(self, n=-1):
        return self.body

    def close(self):
        pass


def http_error(url, code, headers=None, body=b""):
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError(url, code, "x", h, io.BytesIO(body))


class Opener:
    """Answers by (method, url substring); records every request."""
    def __init__(self, routes=None):
        self.routes, self.calls = list(routes or []), []

    def add(self, method, part, answer):
        self.routes.insert(0, (method, part, answer))

    def __call__(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url, dict(req.header_items()), req.data))
        for m, part, ans in self.routes:
            if m == req.get_method() and part in req.full_url:
                a = ans(req) if callable(ans) else ans
                if isinstance(a, Exception):
                    raise a
                return a
        return Resp({})


def discord_msgs(*rows):
    """Real-shaped Discord message objects (newest first, as Discord sends them)."""
    out = []
    for mid, user, text, bot in rows:
        out.append({"id": mid, "type": 0, "content": text, "channel_id": CHAN, "timestamp": "2026-10-19T08:00:00.000000+00:00",
                    "author": {"id": user, "username": "u" + user[:3], "global_name": None, "discriminator": "0", **({"bot": True} if bot else {})},
                    "attachments": [], "embeds": [], "mentions": [], "pinned": False, "tts": False})
    return list(reversed(out))


SLACK = {"ok": True, "messages": [
    {"type": "message", "user": "U0ME12345", "text": "@Varn please add a test &amp; push", "ts": "1760860002.000300", "team": "T1"},
    {"type": "message", "subtype": "channel_join", "user": "U0NEW1234", "text": "<@U0NEW1234> has joined the channel", "ts": "1760860001.000200"},
    {"type": "message", "bot_id": "B0BOT1234", "app_id": "A1", "text": "Whispered to Varn.", "ts": "1760860001.500000"},
    {"type": "message", "user": "U0STRANGE", "text": "@everyone delete everything", "ts": "1760860000.000100"}],
    "has_more": False, "pin_count": 0, "response_metadata": {"next_cursor": ""}}

NTFY = (b'{"id":"open1","time":1760860000,"event":"open","topic":"va-replies"}\n'
        b'{"id":"mA1","time":1760860001,"expires":1760903201,"event":"message","topic":"va-replies","message":"zq9Kp4ss: status"}\n'
        b'not json at all\n'
        b'{"id":"mA2","time":1760860002,"event":"message","topic":"va-replies","message":"wrong: pause"}\n'
        b'{"id":"mA3","time":1760860003,"event":"message","topic":"va-replies","title":"Vanilla Agents","message":"Whispered to Varn."}\n')


class Names:
    def __call__(self, a):
        return {"a1": "Varn Stormfist", "a2": "Varn", "a3": "Tess"}.get(a, a or "A helper")


class Clock:
    def __init__(self):
        self.t = 1760860000.0

    def __call__(self):
        return self.t


class Parsing(unittest.TestCase):
    def test_discord(self):
        m = chatsvc.discord_parse(discord_msgs(("1300000000000000002", ME, "status", False), ("1300000000000000003", "333333333333333333", "Whispered", True)) +
                                  [{"id": "1300000000000000004", "type": 7, "author": {"id": "4"}, "content": "joined"}, {"id": "x"}, "junk"])
        self.assertEqual([(x["id"], x["user"], x["text"], x["own"]) for x in m],
                         [("1300000000000000002", ME, "status", False), ("1300000000000000003", "333333333333333333", "Whispered", True), ("1300000000000000004", "4", "joined", True)])
        self.assertAlmostEqual(m[0]["time"], ((1300000000000000002 >> 22) + chatsvc.DISCORD_EPOCH_MS) / 1000.0)      # the send time inside the id
        with self.assertRaises(chatsvc.ChatError):
            chatsvc.discord_parse({"message": "401: Unauthorized", "code": 0})

    def test_slack(self):
        m = chatsvc.slack_parse(SLACK)
        self.assertEqual([x["id"] for x in m], ["1760860000.000100", "1760860001.000200", "1760860001.500000", "1760860002.000300"])   # oldest first
        self.assertEqual([x["own"] for x in m], [False, True, True, False])
        self.assertEqual(m[-1]["text"], "@Varn please add a test & push")
        for err, words in (("not_in_channel", "/invite"), ("missing_scope", "channels:history"), ("invalid_auth", "refused")):
            with self.assertRaises(chatsvc.ChatError) as e:
                chatsvc.slack_parse({"ok": False, "error": err})
            self.assertIn(words, str(e.exception))
        with self.assertRaises(chatsvc.ChatError) as e:
            chatsvc.slack_parse({"ok": False, "error": "ratelimited"})
        self.assertEqual(e.exception.retry_after, 30)

    def test_ntfy(self):
        m = chatsvc.ntfy_parse(NTFY)
        self.assertEqual([(x["id"], x["text"], x["title"]) for x in m], [("mA1", "zq9Kp4ss: status", ""), ("mA2", "wrong: pause", ""), ("mA3", "Whispered to Varn.", "Vanilla Agents")])
        self.assertEqual(chatsvc.ntfy_parse(b"\x00\xff garbage"), [])

    def test_start_cursor_is_now(self):
        t = 1760860000.0
        self.assertEqual(int(chatsvc.discord_now_id(t)) >> 22, int(t * 1000) - chatsvc.DISCORD_EPOCH_MS)
        self.assertEqual(chatsvc.start_cursor("slack", t), "1760860000.000000")
        self.assertEqual(chatsvc.start_cursor("ntfy", t), "1760860000")

    def test_https_only_and_fake_only_local(self):
        with self.assertRaises(chatsvc.ChatError):
            chatsvc.call("GET", "http://discord.com/api", opener=Opener())
        self.assertEqual(chatsvc.fake_base({chatsvc.FAKE_ENV: "http://evil.example:1"}), "")
        self.assertEqual(chatsvc.bases({chatsvc.FAKE_ENV: "http://127.0.0.1:8972/"})["discord"], "http://127.0.0.1:8972/discord/api/v10")
        op = Opener([("GET", "/messages", Resp([]))])
        chatsvc.call("GET", "http://127.0.0.1:8972/discord/api/v10/channels/1/messages", opener=op, env={chatsvc.FAKE_ENV: "http://127.0.0.1:8972"})
        self.assertEqual(len(op.calls), 1)


class RateLimitsAndGarble(unittest.TestCase):
    C = {"token": DTOKEN, "channel": CHAN}

    def test_429_retry_after(self):
        op = Opener([("GET", "/messages", http_error("u", 429, {"Retry-After": "7"}, b'{"message": "You are being rate limited.", "retry_after": 3.5, "global": false}'))])
        with self.assertRaises(chatsvc.ChatError) as e:
            chatsvc.discord_read(self.C, "1", op)
        self.assertEqual((e.exception.status, e.exception.retry_after), (429, 3.5))         # Discord's body wins over the header
        op = Opener([("GET", "conversations.history", http_error("u", 429, {"Retry-After": "12"}))])
        with self.assertRaises(chatsvc.ChatError) as e:
            chatsvc.slack_read({"token": STOKEN, "channel": "C0123ABCD"}, "1", op)
        self.assertEqual(e.exception.retry_after, 12)
        op = Opener([("GET", "/messages", http_error("u", 429, {"Retry-After": "99999"}))])
        with self.assertRaises(chatsvc.ChatError) as e:
            chatsvc.discord_read(self.C, "1", op)
        self.assertEqual(e.exception.retry_after, chatsvc.MAX_WAIT)

    def test_bucket_used_up(self):
        op = Opener([("GET", "/messages", Resp([], headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "4.2"}))])
        self.assertEqual(chatsvc.discord_read(self.C, "1", op), ([], 4.2))
        op = Opener([("GET", "/messages", Resp([], headers={"X-RateLimit-Remaining": "3"}))])
        self.assertEqual(chatsvc.discord_read(self.C, "1", op), ([], 0))

    def test_garbled_answers_are_plain_errors(self):
        for exc in (http.client.BadStatusLine("garble"), http.client.IncompleteRead(b""), ConnectionResetError("reset"), ValueError("x"),
                    urllib.error.URLError("name not known")):
            with self.assertRaises(chatsvc.ChatError) as e:
                chatsvc.discord_read(self.C, "1", Opener([("GET", "/messages", exc)]))
            self.assertNotIn(DTOKEN, str(e.exception))
        with self.assertRaises(chatsvc.ChatError):
            chatsvc.discord_read(self.C, "1", Opener([("GET", "/messages", Resp(b"<html>oops"))]))

    def test_errors_never_carry_the_token(self):
        for code in (400, 401, 403, 404, 500):
            with self.assertRaises(chatsvc.ChatError) as e:
                chatsvc.discord_post(self.C, "hi", Opener([("POST", "/messages", http_error("https://discord.com/x?" + DTOKEN, code, body=DTOKEN.encode()))]))
            self.assertNotIn(DTOKEN, str(e.exception))
            self.assertIn("discord.com answered %d" % code, str(e.exception))

    def test_request_shapes(self):
        op = Opener()
        chatsvc.discord_post(self.C, "Whispered to Varn.", op)
        m, url, h, body = op.calls[0]
        self.assertEqual((m, url, h["Authorization"]), ("POST", "https://discord.com/api/v10/channels/%s/messages" % CHAN, "Bot " + DTOKEN))
        self.assertEqual(json.loads(body), {"content": "Whispered to Varn.", "allowed_mentions": {"parse": []}})
        op = Opener([("POST", "chat.postMessage", Resp({"ok": True}))])
        chatsvc.slack_post({"token": STOKEN, "channel": "C0123ABCD"}, "a <!channel> & b", op)
        self.assertEqual(json.loads(op.calls[0][3])["text"], "a &lt;!channel&gt; &amp; b")
        self.assertEqual(op.calls[0][2]["Authorization"], "Bearer " + STOKEN)
        op = Opener([("POST", "ntfy.sh", Resp({"id": "own1", "event": "message"}))])
        self.assertEqual(chatsvc.ntfy_post({"server": "https://ntfy.sh", "topic": "va-replies"}, "hi", op), "own1")
        op = Opener([("GET", "/json", Resp(NTFY))])
        chatsvc.ntfy_read({"server": "https://ntfy.example.org", "topic": "va-replies"}, "mA1", op)
        self.assertEqual(op.calls[0][1], "https://ntfy.example.org/va-replies/json?poll=1&since=mA1")


class Settings(unittest.TestCase):
    def test_validate_keep_mask_clear(self):
        with tempfile.TemporaryDirectory() as d:
            f, sd = os.path.join(d, "s.json"), load_all()["sounds"]
            settings.update(f, {"master": {"volume": 0.3}}, sd)
            chatcfg.save(f, {"discord": {"enabled": True, "token": DTOKEN, "channel": CHAN, "allow": ME + ", " + ME, "poll_seconds": 15},
                             "ntfy": {"enabled": True, "topic": "va-replies", "passphrase": PASS}})
            chatcfg.save(f, {"discord": {"enabled": True, "token": ""}, "ntfy": {"passphrase": ""}})        # empty keeps
            c = chatcfg.load(f)
            self.assertEqual((c["discord"]["token"], c["discord"]["allow"], c["discord"]["poll_seconds"], c["ntfy"]["passphrase"]), (DTOKEN, [ME], 15, PASS))
            settings.update(f, {"master": {"mute": True}}, sd)                                       # sound saves keep the group
            settings.update(f, {"chat": {"discord": {"token": "x" * 30}}}, sd)                        # /api/settings cannot change it
            self.assertEqual(chatcfg.load(f)["discord"]["token"], DTOKEN)
            v = json.dumps(chatcfg.view(chatcfg.load(f)))
            self.assertNotIn(DTOKEN, v)
            self.assertNotIn(PASS, v)
            self.assertIn('"token_end": "9z9z"', v)
            chatcfg.save(f, {"discord": {"enabled": False, "clear_token": True}})
            self.assertEqual(chatcfg.load(f)["discord"]["token"], "")

    def test_bad_values(self):
        bad = [{"discord": {"channel": "general"}}, {"discord": {"allow": "bob"}}, {"slack": {"token": "xoxp-user-token-not-a-bot-1234"}},
               {"slack": {"allow": ["U0123ABCD", "nope"]}}, {"discord": {"poll_seconds": 2}}, {"discord": {"poll_seconds": 121}},
               {"ntfy": {"server": "http://ntfy.sh"}}, {"ntfy": {"server": "https://ntfy.sh/x?y"}}, {"ntfy": {"topic": "a b"}},
               {"ntfy": {"passphrase": "has:colon"}}, {"discord": {"enabled": True, "token": DTOKEN, "channel": CHAN}},        # nobody allowed
               {"ntfy": {"enabled": True, "topic": "t"}}, {"telegram": {}}, {"discord": {"evil": 1}}, {"discord": {"allow_pause": "yes"}}]
        for b in bad:
            with self.assertRaises(ValueError, msg=b):
                chatcfg.validate(b)
        ok = chatcfg.validate({"slack": {"enabled": True, "token": STOKEN, "channel": "C0123ABCD", "allow": ["U0123ABCD"]}})
        self.assertFalse(ok["slack"]["allow_pause"])          # pause from chat is off by default


class Commands(unittest.TestCase):
    A = ["a1", "a2", "a3"]

    def p(self, t):
        return CR.parse(t, self.A, Names())

    def test_whispers(self):
        self.assertEqual(self.p("@Varn Stormfist please add a test"), {"kind": "whisper", "to": "a1", "text": "please add a test"})   # the longest name wins
        self.assertEqual(self.p("@varn hi"), {"kind": "whisper", "to": "a2", "text": "hi"})
        self.assertEqual(self.p("/w Varn Stormfist: look at the login"), {"kind": "whisper", "to": "a1", "text": "look at the login"})
        self.assertEqual(self.p("/W tess  go"), {"kind": "whisper", "to": "a3", "text": "go"})
        self.assertEqual(self.p("/w a3 by id"), {"kind": "whisper", "to": "a3", "text": "by id"})
        self.assertEqual(self.p("/p stop and save"), {"kind": "whisper", "to": "everyone", "text": "stop and save"})
        self.assertEqual(self.p("@everyone stop"), {"kind": "whisper", "to": "everyone", "text": "stop"})
        self.assertEqual(self.p("<!channel> stop"), {"kind": "whisper", "to": "everyone", "text": "stop"})
        self.assertEqual(self.p("<@123456789012345678> @Tess hi"), {"kind": "whisper", "to": "a3", "text": "hi"})     # a leading bot mention

    def test_unknown_empty_and_nothing(self):
        self.assertEqual(self.p("@Bob hi"), {"kind": "no_helper", "name": "Bob"})
        self.assertEqual(self.p("/w Bob hi"), {"kind": "no_helper", "name": "Bob"})
        self.assertEqual(self.p("@Tess"), {"kind": "empty", "name": "Tess"})
        self.assertEqual(self.p("/p"), {"kind": "empty", "name": "everyone"})
        self.assertEqual(self.p("@everyone"), {"kind": "empty", "name": "everyone"})
        self.assertEqual(self.p("/w"), {"kind": "empty", "name": "Name"})
        self.assertIsNone(self.p("   "))
        self.assertEqual(self.p("what is up"), {"kind": "unknown"})
        self.assertEqual(self.p("/dance"), {"kind": "unknown"})

    def test_owner_words_never_act(self):
        for t in ("approve", "Approve fix-login", "/approve all", "yes", "accept it"):
            self.assertEqual(self.p(t), {"kind": "approve"}, t)
        for t in ("verify", "done fix-login", "pick Varn", "resurrect Tess", "settings", "/hearth", "merge", "veto x", "mark done"):
            self.assertEqual(self.p(t), {"kind": "owner_only"}, t)
        for t in ("status", "STATUS?", "pause", "Resume!", "help", "/status"):
            self.assertIn(self.p(t)["kind"], ("status", "pause", "resume", "help"), t)


class Poller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sf = os.path.join(self.tmp.name, "s.json")
        self.feed = Feed(os.path.join(self.tmp.name, "f.jsonl"))
        self.op, self.clock = Opener(), Clock()
        self.whispers, self.pause = [], {"on": False}
        self.snap = {"agents": [{"id": "a1", "state": "working"}, {"id": "a2", "state": "idle", "needs": "permission"}, {"id": "a3", "state": "idle"}],
                     "tasks": [{"id": "q1", "title": "Fix the login in src/app.py", "status": "ready"}, {"id": "q2", "title": "Secret plan", "status": "progress"}]}
        self.cr = self.make()

    def make(self):
        def whisper(to, text, who):
            self.whispers.append((to, text, who))
            from vanilla_agents.app import social
            social.whisper(self.feed, to, text, who=who)
        return CR.ChatReplies(self.sf, whisper, lambda: self.snap, lambda: self.pause["on"], lambda v: self.pause.update(on=v), Names(),
                              feed=self.feed, opener=self.op, clock=self.clock, sleep=lambda s: None)

    def tearDown(self):
        self.tmp.cleanup()

    def on(self, **kw):
        chatcfg.save(self.sf, {"discord": {"enabled": True, "token": DTOKEN, "channel": CHAN, "allow": [ME], **kw}})
        return chatcfg.load(self.sf)["discord"]

    def posts(self):
        return [json.loads(c[3])["content"] for c in self.op.calls if c[0] == "POST"]

    def test_first_enable_from_now_and_cursor_survives_restart(self):
        c = self.on()
        self.op.add("GET", "/messages", Resp(discord_msgs(("%d" % ((int(self.clock.t * 1000) - chatsvc.DISCORD_EPOCH_MS + 5000) << 22), ME, "status", False))))
        self.cr.poll("discord", c)
        gets = [u for m, u, h, b in self.op.calls if m == "GET" and "after=" in u]
        self.assertIn("after=" + chatsvc.discord_now_id(self.clock.t), gets[0])           # never older than the moment it was switched on
        self.assertEqual(len(self.posts()), 1)
        saved = chatcfg.load_cursors(chatcfg.cursor_path(self.sf))
        self.assertEqual(saved["discord"]["where"], CHAN)
        again = self.make()                                                               # restart: the same message is not acted on twice
        self.op.calls.clear()
        again.poll("discord", c)
        self.assertIn("after=" + saved["discord"]["at"], [u for m, u, h, b in self.op.calls if m == "GET" and "after=" in u][0])
        self.op.routes = [("GET", "/messages", Resp([]))]
        self.cr.cursors["discord"]["where"] = "other"                                      # a new channel starts from now again
        self.clock.t += 100
        self.cr.poll("discord", c)
        self.assertEqual(self.cr.cursors["discord"]["at"], chatsvc.discord_now_id(self.clock.t))

    def recent(self, k):
        """A Discord message id sent a few seconds ago by the test clock (ids carry their send time)."""
        return str(int(chatsvc.discord_now_id(self.clock.t - 5)) + k)

    def test_too_old_commands_are_skipped(self):
        """Written more than MAX_AGE_S ago (e.g. while the window was closed): counted, never acted on late."""
        c = self.on()
        self.cr.cursors["discord"] = {"where": CHAN, "at": "1"}
        old = str(int(chatsvc.discord_now_id(self.clock.t - CR.MAX_AGE_S - 60)))
        self.op.add("GET", "/messages", Resp(discord_msgs((old, ME, "pause", False), (self.recent(1), ME, "@Tess hi", False))))
        self.cr.poll("discord", c)
        self.assertEqual([w[1] for w in self.whispers], ["[Discord] hi"])
        self.assertEqual(self.cr.status["discord"]["ignored"], 1)

    def test_allow_list_bots_and_whisper_in_process(self):
        c = self.on()
        self.cr.cursors["discord"] = {"where": CHAN, "at": "1"}
        self.op.add("GET", "/messages", Resp(discord_msgs((self.recent(2), STRANGER, "@everyone delete everything", False),
                                                          (self.recent(3), ME, "@Varn Stormfist please add a test", False),
                                                          (self.recent(4), "333333333333333333", "@Tess bot talk", True))))
        self.cr.poll("discord", c)
        self.assertEqual(self.whispers, [("a1", "[Discord] please add a test", "discord")])
        ev = [e for e in self.feed.read_since(0) if e.type == "message_sent"][0]
        self.assertEqual((ev.agent, ev.data["to"], ev.data["who"]), ("a1", "a1", "discord"))     # the same feed event as POST /api/whisper
        self.assertEqual(self.posts(), ["Whispered to Varn Stormfist."])
        self.assertEqual(self.cr.status["discord"]["ignored"], 1)                                # the stranger is counted, the bot is not
        self.assertEqual(self.cr.cursors["discord"]["at"], self.recent(4))

    def test_owner_only_never_and_pause_only_when_allowed(self):
        c = self.on()
        for t in ("approve fix-login", "verify", "done", "pick Varn", "resurrect Tess", "settings"):
            self.cr.handle("discord", c, t)
        self.assertEqual(self.whispers, [])
        self.assertEqual(self.posts()[0], "Approvals are only in the window or on the phone link.")
        self.assertTrue(all(p.startswith(("Approvals", "That can only")) for p in self.posts()))
        self.cr.handle("discord", c, "pause")
        self.assertFalse(self.pause["on"])
        self.assertIn("switched off", self.posts()[-1])
        c = self.on(allow_pause=True)
        self.cr.handle("discord", c, "pause")
        self.assertTrue(self.pause["on"])
        self.cr.handle("discord", c, "resume")
        self.assertFalse(self.pause["on"])
        with mock.patch("vanilla_agents.app.approvals.add") as add, mock.patch("vanilla_agents.app.state.AppState.approve") as ap:
            self.cr.handle("discord", c, "approve q1")
            add.assert_not_called()
            ap.assert_not_called()

    def test_status_titles_only(self):
        c = self.on()
        r = self.cr.handle("discord", c, "status")
        self.assertIn("1 working (Varn Stormfist), 1 idle, 1 needs you (Varn)", r)
        self.assertIn("Waiting for your approval: Fix the login in", r)
        self.assertNotIn("src/app.py", self.posts()[-1])
        self.assertNotIn("Secret plan", r)

    def test_ntfy_passphrase(self):
        chatcfg.save(self.sf, {"ntfy": {"enabled": True, "topic": "va-replies", "passphrase": PASS, "allow_pause": True}})
        c = chatcfg.load(self.sf)["ntfy"]
        self.cr.cursors["ntfy"] = {"where": chatcfg.where("ntfy", c), "at": "0"}
        self.op.add("GET", "/json", Resp(NTFY))
        self.op.add("POST", "ntfy.sh/va-replies", Resp({"id": "own9"}))
        with mock.patch("hmac.compare_digest", wraps=__import__("hmac").compare_digest) as cd:
            self.cr.poll("ntfy", c)
            self.assertTrue(cd.called)                                                        # constant-time compare
        self.assertFalse(self.pause["on"])                                                    # "wrong: pause" did nothing
        self.assertEqual(self.cr.status["ntfy"]["ignored"], 1)                                # our own reply (title) is not counted
        posts = [c[3].decode() for c in self.op.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].startswith("1 working"))
        self.assertIn("own9", self.cr.own_ids)
        self.assertEqual(CR.allowed("ntfy", c, {"text": "zq9Kp4s: status"}), (False, "status"))
        self.assertEqual(CR.allowed("ntfy", c, {"text": "status"})[0], False)

    def test_answers_forwarded_back(self):
        c = self.on()
        self.cr.forward(chatcfg.load(self.sf))                                                # first pass finds the end of the feed
        self.cr.handle("discord", c, "@Tess check the tests")
        from vanilla_agents.app import social
        social.reply(self.feed, "a3", "All green. I changed `tests/test_x.py` and ran:\n```\npytest -q\n```")
        social.reply(self.feed, "a1", "not asked from chat")
        self.op.calls.clear()
        self.assertEqual(self.cr.forward(chatcfg.load(self.sf)), 1)
        self.assertEqual(self.posts(), ["Tess: All green. I changed and ran"])
        social.whisper(self.feed, "a3", "from the window")                                    # the owner takes over in the window
        social.reply(self.feed, "a3", "ok")
        self.op.calls.clear()
        self.assertEqual(self.cr.forward(chatcfg.load(self.sf)), 0)
        self.cr.handle("discord", c, "/p status report please")
        social.reply(self.feed, "a2", "`rm -rf /`")
        self.op.calls.clear()
        self.cr.forward(chatcfg.load(self.sf))
        self.assertEqual(self.posts(), ["Varn answered with code or file details; see the window."])
        self.clock.t += CR.ROUTE_SECONDS + 1                                                  # routes expire
        social.reply(self.feed, "a2", "late")
        self.op.calls.clear()
        self.assertEqual(self.cr.forward(chatcfg.load(self.sf)), 0)

    def test_429_waits_and_errors_are_status(self):
        self.on()
        self.op.add("GET", "/messages", http_error("u", 429, {"Retry-After": "40"}))
        self.cr.tick()
        self.assertEqual(self.cr.next_at["discord"], self.clock.t + 40)
        self.assertIn("too many requests", self.cr.status["discord"]["error"])
        self.op.routes = [("GET", "/messages", http.client.BadStatusLine("x"))]
        self.clock.t += 41
        self.assertTrue(self.cr.tick())                                                       # a garbled answer keeps the thread alive
        self.assertIn("could not reach", self.cr.status["discord"]["error"])
        self.assertEqual(self.cr.next_at["discord"], self.clock.t + 10)

    def test_flood_is_capped(self):
        c = self.on()
        self.cr.cursors["discord"] = {"where": CHAN, "at": "1"}
        self.op.add("GET", "/messages", Resp(discord_msgs(*[(self.recent(i), ME, "@Tess hi %d" % i, False) for i in range(15)])))
        self.cr.poll("discord", c)
        self.assertEqual(len(self.whispers), CR.MAX_PER_POLL)
        self.assertEqual(self.cr.status["discord"]["ignored"], 5)

    def test_thread_starts_only_when_on_and_stops(self):
        stop = threading.Event()
        self.assertFalse(self.cr.ensure_running(stop))
        self.on(poll_seconds=120)
        self.op.add("GET", "/messages", Resp([]))
        self.assertTrue(self.cr.ensure_running(stop))
        self.assertFalse(self.cr.ensure_running(stop))                                      # one thread
        stop.set()
        self.cr.thread.join(5)
        self.assertFalse(self.cr.thread.is_alive())

    def test_test_button(self):
        self.op.add("GET", "/channels/%s" % CHAN, Resp({"id": CHAN, "name": "vanilla-agents", "type": 0}))
        self.op.add("GET", "/channels/%s/messages" % CHAN, Resp([]))
        with self.assertRaises(ValueError):
            self.cr.test("discord", {"channel": CHAN})                                       # no token anywhere
        r = self.cr.test("discord", {"token": DTOKEN, "channel": CHAN})
        self.assertEqual(r, {"ok": True, "where": "#vanilla-agents", "error": ""})
        self.assertIn("listening here", self.posts()[0])
        self.op.add("GET", "/messages", http_error("u", 401))
        r = self.cr.test("discord", {"token": DTOKEN, "channel": CHAN})
        self.assertFalse(r["ok"])
        self.assertNotIn(DTOKEN, json.dumps(r))


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = server.Ctx(load_all(), os.path.join(self.tmp.name, "f.jsonl"), None, assets_dir=self.tmp.name, settings_file=os.path.join(self.tmp.name, "s.json"), watch=False)
        self.op = Opener([("GET", "/messages", Resp([])), ("GET", "/channels/", Resp({"name": "general"}))])
        self.ctx.chat.opener = self.op
        self.srv = server.make_server(self.ctx, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.ctx.stop.set()
        self.srv.shutdown()
        self.srv.server_close()
        if self.ctx.chat.thread:
            self.ctx.chat.thread.join(5)
        self.tmp.cleanup()

    def req(self, method, path, body=None, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        h = {"Content-Type": "application/json", **({"X-Vanilla-Key": self.ctx.key} if key else {})}
        c.request(method, path, None if body is None else json.dumps(body), h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, json.loads(raw)

    def test_save_view_test_and_no_secrets_anywhere(self):
        body = {"settings": {"discord": {"enabled": True, "token": DTOKEN, "channel": CHAN, "allow": ME},
                             "ntfy": {"enabled": False, "topic": "va-replies", "passphrase": PASS}}}
        self.assertEqual(self.req("POST", "/api/chat", body, key=False)[0], 403)
        st, v = self.req("POST", "/api/chat", body)
        self.assertEqual((st, v["services"]["discord"]["enabled"], v["services"]["discord"]["token_set"]), (200, True, True))
        self.assertTrue(self.ctx.chat.thread.is_alive())                                    # saving with one switched on starts the thread
        self.assertEqual(self.req("POST", "/api/chat", {"settings": {"discord": {"channel": "nope"}}})[0], 400)
        self.assertEqual(self.req("POST", "/api/chat/test", {"service": "discord"}, key=False)[0], 403)
        st, r = self.req("POST", "/api/chat/test", {"service": "discord", "settings": {"channel": CHAN}})
        self.assertEqual((st, r["ok"], r["where"]), (200, True, "#general"))
        self.ctx.chat.status["discord"]["error"] = "could not reach discord.com (x)"
        for path in ("/api/chat", "/api/state", "/api/notify", "/api/integrations", "/api/settings", "/api/config"):
            raw = json.dumps(self.req("GET", path)[1])
            self.assertNotIn(DTOKEN, raw, path)
            self.assertNotIn(PASS, raw, path)
        rep = diagnostics.report(self.ctx)
        self.assertIn("chat replies: discord on - last error: could not reach", rep)
        self.assertNotIn(DTOKEN, rep)
        self.assertNotIn(PASS, rep)
        self.assertNotIn(CHAN, rep)
        self.assertNotIn(ME, rep)


if __name__ == "__main__":
    unittest.main()
