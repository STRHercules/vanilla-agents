"""V-050 chat replies (the inbound half of the integration hub): the owner commands Vanilla Agents from Discord, Slack or ntfy.
A background thread polls each switched-on service every N seconds (no public server, no webhooks in), acts only on messages from
the allow list (Discord / Slack user ids; ntfy has no identity, so a message must start with the owner's passphrase, compared in
constant time), and answers in the same channel with short sentences (notify.plain: never code, file contents, paths or commands).
What chat can do: whisper one helper or everyone (the same in-process whisper as POST /api/whisper), ask for a status summary, and
pause / resume (only when the owner ticked Allow pause/resume). It can NEVER approve, verify, mark done, pick winners, resurrect or
change settings. A helper's answer (message_answered) to a whisper that came from chat goes back to that channel, tagged with its name.
Cursors (last message per channel) are kept in the work folder; a first enable starts from now (history is never acted on)."""
import threading
import time

from ..events.feed import Feed
from ..integrations import chatcfg, chatsvc
from . import notify, social
from .chatcommands import allowed, parse, status_text

NAMES = {"discord": "Discord", "slack": "Slack", "ntfy": "ntfy"}
ROUTE_SECONDS = 3600          # a helper's answers go back to chat for an hour after a chat whisper (until the owner whispers it from the window)
MAX_AGE_S = 600              # V-050 follow-up: commands written more than 10 minutes ago (e.g. while the window was closed) are skipped, never acted on late
MAX_PER_POLL = 10             # commands acted on per poll; more are counted as skipped (a flood never turns into a flood of whispers)
R = {   # every sentence chat replies can send (short, plain; helper names and quest titles are the only variable parts)
    "sent": "Whispered to {name}.", "sent_all": "Whispered to every helper.", "no_helper": "There is no helper called {name} here.",
    "empty": "Say something after the name, like: @{name} please add a test.", "unknown": "I did not understand that. Type help for the commands.",
    "help": "Commands: status, @Name message (or /w Name message), /p message (or @everyone message){pause}. Approvals stay in the window.",
    "help_pause": ", pause, resume", "approve": "Approvals are only in the window or on the phone link.",
    "owner_only": "That can only be done in the Vanilla Agents window.", "paused": "Paused: the checker waits until you resume.",
    "resumed": "Resumed: the checker runs again.", "already_paused": "Already paused.", "not_paused": "Not paused.",
    "no_pause": "Pause from chat is switched off (Options > Chat replies).", "hello": "Vanilla Agents is listening here. Type help for the commands.",
    "answer": "{name}: {text}", "answer_code": "{name} answered with code or file details; see the window.",
}


def _blank(on=False):
    return {"on": on, "ok": None, "where": "", "last_cmd": 0, "ignored": 0, "error": "", "at": 0, "hint": ""}


class ChatReplies:
    """One per window. whisper(to, text, who), snapshot() -> {agents, tasks}, paused() and set_paused(bool) come from the server."""

    def __init__(self, settings_file, whisper, snapshot, paused, set_paused, names, feed=None, env=None, opener=None, clock=time.time, sleep=time.sleep):
        self.settings_file, self.whisper, self.snapshot, self.paused, self.set_paused = settings_file, whisper, snapshot, paused, set_paused
        self.names, self.feed, self.env, self.opener, self.clock, self.sleep = names, feed, env or {}, opener, clock, sleep
        self.cpath = chatcfg.cursor_path(settings_file)
        self.cursors = chatcfg.load_cursors(self.cpath)
        self.status = {s: _blank() for s in chatcfg.SERVICES}
        self.next_at, self.routes, self.own_ids, self.place = {}, {}, set(), {}
        self.feed_cursor, self.thread, self.lock = None, None, threading.Lock()
        self.version = 0

    def settings(self):
        return chatcfg.load(self.settings_file)

    # ---- sending ----
    def send(self, svc, c, text):
        """One reply, at most one more try (network trouble / 5xx); a 429 is not retried here (the poller waits). Never raises."""
        text = notify.plain(text)
        if not text:
            return False
        for attempt in range(2):
            try:
                r = chatsvc.POST[svc](c, text, self.opener, self.env)
                if svc == "ntfy" and r:
                    self.own_ids.add(r)
                return True
            except chatsvc.ChatError as e:
                self.status[svc]["error"] = "could not answer: %s" % e
                if attempt or (e.status and e.status < 500):
                    return False
                self.sleep(1.5)
        return False

    # ---- one command ----
    def handle(self, svc, c, text):
        """Act on one allowed command; returns the reply sentence (already sent)."""
        snap = self.snapshot() or {}
        agents = [a["id"] for a in snap.get("agents") or [] if a.get("id")]
        cmd = parse(text, agents, self.names)
        if cmd is None:
            return ""
        k = cmd["kind"]
        if k == "whisper":
            self.whisper(cmd["to"], "[%s] %s" % (NAMES[svc], cmd["text"]), svc)
            now = self.clock()
            if cmd["to"] == social.EVERYONE:
                self.routes = {**{a: (svc, now + ROUTE_SECONDS) for a in agents}, "*": (svc, now + ROUTE_SECONDS)}
            else:
                self.routes[cmd["to"]] = (svc, now + ROUTE_SECONDS)
            reply = R["sent_all"] if cmd["to"] == social.EVERYONE else R["sent"].format(name=self.names(cmd["to"]))
        elif k == "status":
            reply = status_text(snap, self.names, self.paused())
        elif k == "help":
            reply = R["help"].format(pause=R["help_pause"] if c.get("allow_pause") else "")
        elif k in ("pause", "resume"):
            if not c.get("allow_pause"):
                reply = R["no_pause"]
            elif (k == "pause") == bool(self.paused()):
                reply = R["already_paused"] if k == "pause" else R["not_paused"]
            else:
                self.set_paused(k == "pause")
                reply = R["paused"] if k == "pause" else R["resumed"]
        elif k in ("no_helper", "empty"):
            reply = R[k].format(name=cmd.get("name", ""))
        else:
            reply = R[k]           # approve / owner_only / unknown: nothing happens
        self.status[svc]["last_cmd"] = int(self.clock())
        self.send(svc, c, reply)
        return reply

    # ---- polling ----
    def poll(self, svc, c):
        """Read new messages once and act on the allowed ones. Returns seconds to wait at least before the next poll."""
        st, key = self.status[svc], chatcfg.where(svc, c)
        st["on"] = True
        cur = self.cursors.get(svc)
        if not cur or cur.get("where") != key:
            cur = self.cursors[svc] = {"where": key, "at": chatsvc.start_cursor(svc, self.clock())}       # first enable / new channel: from now
            chatcfg.save_cursors(self.cpath, self.cursors)
            self.place.pop(svc, None)
        if svc not in self.place:
            self.place[svc] = self._where_name(svc, c)
        try:
            msgs, wait = chatsvc.READ[svc](c, cur["at"], self.opener, self.env)
        except chatsvc.ChatError as e:
            st.update(ok=False, error=str(e), at=int(self.clock()), where=self.place[svc])
            self.version += 1
            return e.retry_after or 0
        acted, empty_text = 0, False
        st["error"] = ""
        for m in msgs:
            cur["at"] = m["id"]
            if m["own"] or m["id"] in self.own_ids or (svc == "ntfy" and m.get("title") == "Vanilla Agents"):
                continue
            ok, text = allowed(svc, c, m)
            if not ok:
                st["ignored"] += 1
                continue
            if not text.strip():
                empty_text = svc == "discord"
                continue
            t = m.get("time")
            if isinstance(t, (int, float)) and t > 0 and self.clock() - t > MAX_AGE_S:
                st["ignored"] += 1          # too old: an hours-old "pause" or whisper must not fire now
                continue
            if acted >= MAX_PER_POLL:
                st["ignored"] += 1
                continue
            acted += 1
            try:
                self.handle(svc, c, text)
            except (ValueError, OSError) as e:          # a whisper that could not be written: say so in the window, keep polling
                st["error"] = "could not pass it on: %s" % e
        if msgs:
            chatcfg.save_cursors(self.cpath, self.cursors)
        if msgs:
            st["hint"] = "messages arrive without text: switch on Message Content Intent for the bot (Discord developer portal, Bot)" if empty_text else ""
        st.update(ok=True, at=int(self.clock()), where=self.place[svc])
        self.version += 1
        return wait

    def _where_name(self, svc, c):
        if svc == "ntfy":
            return "ntfy topic %s" % c["topic"]
        if svc == "discord":
            try:
                return chatsvc.discord_name(c, self.opener, self.env) or "channel %s" % c["channel"]
            except chatsvc.ChatError:
                return "channel %s" % c["channel"]
        return "Slack channel %s" % c["channel"]

    # ---- answers back to chat ----
    def forward(self, cfg):
        """Helper answers (message_answered) to a whisper that came from chat go back to that channel. First pass: only finds the end."""
        if self.feed is None:
            return 0
        if self.feed_cursor is None:
            self.feed_cursor = self.feed.last_id()
            return 0
        n, now = 0, self.clock()
        for ev in self.feed.read_since(self.feed_cursor, 2000):
            self.feed_cursor = ev.id
            if ev.type == "message_sent" and ev.data.get("who") not in chatcfg.SERVICES:      # the owner whispered from the window: answers stay there
                to = ev.data.get("to")
                self.routes = {} if to == social.EVERYONE else {k: v for k, v in self.routes.items() if k != to}
            elif ev.type == "message_answered" and ev.agent:
                svc, until = self.routes.get(ev.agent) or self.routes.get("*") or (None, 0)
                c = cfg.get(svc) if svc else None
                if not c or not c.get("enabled") or until < now:
                    continue
                name, text = self.names(ev.agent), notify.plain(ev.data.get("text"), 160)
                n += self.send(svc, c, R["answer"].format(name=name, text=text) if text else R["answer_code"].format(name=name))
        return n

    # ---- the thread ----
    def tick(self):
        """One pass: poll every switched-on service that is due, forward answers. Returns whether anything is switched on."""
        try:
            cfg, now = self.settings(), self.clock()
            on = False
            for svc in chatcfg.SERVICES:
                c = cfg[svc]
                if not c["enabled"]:
                    if self.status[svc]["on"]:
                        self.status[svc], self.version = _blank(), self.version + 1
                    continue
                on = True
                if now >= self.next_at.get(svc, 0):
                    wait = self.poll(svc, c)
                    self.next_at[svc] = self.clock() + max(c["poll_seconds"], min(chatsvc.MAX_WAIT, wait or 0))
            self.forward(cfg)
            return on
        except Exception:
            return True          # chat replies must never take the window down

    def ensure_running(self, stop):
        """Start the thread when something is switched on (at start and after every save); it ends when all are off or on stop."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            cfg = self.settings()
            if not any(cfg[s]["enabled"] for s in chatcfg.SERVICES):
                return False

            def loop():
                while not stop.is_set() and self.tick():
                    stop.wait(1.0)
            self.thread = threading.Thread(target=loop, daemon=True, name="chat-replies")
            self.thread.start()
            return True

    # ---- the Test button ----
    def test(self, svc, body=None):
        """Read the channel once and post a hello, with what is typed in the window (secrets fall back to the saved ones)."""
        if svc not in chatcfg.SERVICES:
            raise ValueError("service must be discord, slack or ntfy")
        c = chatcfg.merged(chatcfg.settings_mod.load_extra(self.settings_file, "chat"), {svc: body or {}})[svc]
        need = ("topic",) if svc == "ntfy" else ("token", "channel")
        if not all(c.get(k) for k in need):
            raise ValueError("fill in the %s first" % (" and the ".join("bot token" if k == "token" else "channel id" if k == "channel" else "reply topic" for k in need)))
        try:
            if svc == "discord":
                chatsvc.discord_latest(c, self.opener, self.env)
                where = chatsvc.discord_name(c, self.opener, self.env) or "channel %s" % c["channel"]
            elif svc == "slack":
                chatsvc.slack_latest(c, self.opener, self.env)
                where = "Slack channel %s" % c["channel"]
            else:
                chatsvc.ntfy_read(c, chatsvc.start_cursor("ntfy", self.clock() - 60), self.opener, self.env)
                where = "ntfy topic %s" % c["topic"]
            r = chatsvc.POST[svc](c, R["hello"], self.opener, self.env)
            if svc == "ntfy" and r:
                self.own_ids.add(r)
        except chatsvc.ChatError as e:
            return {"ok": False, "where": "", "error": str(e)}
        return {"ok": True, "where": where, "error": ""}

    def view(self):
        return {**chatcfg.view(self.settings()), "status": {k: dict(v) for k, v in self.status.items()}, "fake": bool(chatsvc.fake_base(self.env))}

    def diagnostics(self):
        """On/off and the last error only: never tokens, passphrase, channel ids, allow lists or message text."""
        cfg = self.settings()
        return ", ".join("%s %s%s" % (s, "on" if cfg[s]["enabled"] else "off", " - last error: " + self.status[s]["error"][:120] if self.status[s]["error"] else "") for s in chatcfg.SERVICES)


# ---- server calls (window key; none of them is an agent / MCP tool) ----
GET_CALLS = ("/api/chat",)
POST_CALLS = ("/api/chat", "/api/chat/test")


def make(ctx, env, opener=None):
    return ChatReplies(ctx.settings_file, lambda to, text, who: social.whisper(Feed(ctx.feed_path), to, text, who=who), lambda: ctx.state.snapshot(),
                       ctx.verify.paused, ctx.verify.set_paused, notify.Names(ctx.assets_dir), feed=Feed(ctx.feed_path), env=env, opener=opener)


def post(ctx, call, body):
    cr = ctx.chat
    if call == "/api/chat":
        chatcfg.save(ctx.settings_file, body.get("settings"))
        cr.next_at.clear()
        cr.ensure_running(ctx.stop)
        return cr.view()
    if call == "/api/chat/test":
        return cr.test(body.get("service"), body.get("settings") if isinstance(body.get("settings"), dict) else None)
    raise ValueError("no such call")
