"""V-041: outgoing notifications (Discord, Slack, ntfy.sh, any JSON webhook) for a few moments that matter when you are away:
a helper needs you, a quest is ready for your yes, a quest is done, a check failed, a helper is stunned, the party plan was agreed.
Only short plain sentences leave this PC ("Varn needs you: permission for Bash"); never code, file contents, paths or commands.
Settings live in YOUR settings file (settings.py EXTRA "notify"), never in the repository. Sending happens on one background thread
(urllib, short timeout, at most RETRIES extra tries), never in a request or the window; the same sentence is sent at most once a minute."""
import hashlib
import http.client
import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import settings as settings_mod

CHANNELS = ("discord", "slack", "ntfy", "webhook")
EVENTS = ("needs_you", "quest_ready", "quest_done", "check_failed", "stunned", "plan_agreed")
DEFAULT_EVENTS = ("needs_you", "quest_ready", "check_failed")
TIMEOUT = 6
RETRIES = 2
DEDUPE_SECONDS = 60
MAX_TEXT = 200
_TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_URL_START = {"discord": ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/", "https://ptb.discord.com/api/webhooks/", "https://canary.discord.com/api/webhooks/"),
              "slack": ("https://hooks.slack.com/",), "webhook": ("https://", "http://")}
NEEDS_WORDS = {"permission": "permission", "question": "a question", "input": "your reply"}


def validate(body):
    """Clean notify settings or ValueError. Shape: {channel: {"enabled": bool, "url": str (ntfy: "topic" + optional "server"), "events": [names]}}."""
    if not isinstance(body, dict) or set(body) - set(CHANNELS):
        raise ValueError("notifications may only contain " + ", ".join(CHANNELS))
    out = {}
    for ch, c in body.items():
        if not isinstance(c, dict) or set(c) - {"enabled", "url", "topic", "server", "events"}:
            raise ValueError("bad settings for %s" % ch)
        e = c.get("events", list(DEFAULT_EVENTS))
        if not isinstance(e, list) or any(x not in EVENTS for x in e):
            raise ValueError("%s: events must be some of %s" % (ch, ", ".join(EVENTS)))
        row = {"enabled": c.get("enabled") is True, "events": [x for x in EVENTS if x in e]}
        if ch == "ntfy":
            topic, server = str(c.get("topic") or "").strip(), str(c.get("server") or "https://ntfy.sh").strip().rstrip("/")
            if topic and not _TOPIC.match(topic):
                raise ValueError("ntfy topic: letters, digits, - and _ only (up to 64)")
            if not server.startswith("https://") or any(x in server[8:] for x in "/?#@ ") or len(server) > 200:
                raise ValueError("ntfy server must be like https://ntfy.sh")
            row.update(topic=topic, server=server)
        else:
            url = str(c.get("url") or "").strip()
            if url and (not url.startswith(_URL_START[ch]) or len(url) > 500 or any(x in url for x in " \r\n\t")):
                raise ValueError({"discord": "Discord webhook links start with https://discord.com/api/webhooks/",
                                  "slack": "Slack webhook links start with https://hooks.slack.com/",
                                  "webhook": "the webhook link must start with https:// (or http://)"}[ch])
            row["url"] = url
        if row["enabled"] and not (row.get("url") or row.get("topic")):
            raise ValueError("%s is switched on but has no %s" % (ch, "topic" if ch == "ntfy" else "link"))
        out[ch] = row
    return out


def load(settings_file):
    try:
        return validate(settings_mod.load_extra(settings_file, "notify"))
    except ValueError:
        return {}


def save(settings_file, body):
    clean = validate(body)
    settings_mod.save_extra(settings_file, "notify", clean)
    return clean


def view(cfg):
    """What the window shows: every channel with its fields filled in (empty ones too)."""
    out = {}
    for ch in CHANNELS:
        c = cfg.get(ch) or {}
        row = {"enabled": bool(c.get("enabled")), "events": list(c.get("events", DEFAULT_EVENTS))}
        if ch == "ntfy":
            row.update(topic=c.get("topic", ""), server=c.get("server", "https://ntfy.sh"))
        else:
            row["url"] = c.get("url", "")
        out[ch] = row
    return {"channels": out, "events": list(EVENTS)}


# ---- hero names: the same pick as the window (workers.js: FNV-1a of the id over models/workers/manifest.json) ----

def _fnv(s):
    h = 2166136261
    for ch in str(s):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


class Names:
    def __init__(self, assets_dir):
        self.path, self._roster = Path(assets_dir) / "models" / "workers" / "manifest.json", None

    def __call__(self, agent_id):
        if self._roster is None:
            try:
                r = json.loads(self.path.read_text(encoding="utf-8"))
                self._roster = r if isinstance(r, list) else []
            except (OSError, ValueError):
                self._roster = []
        if not agent_id:
            return "A helper"
        if self._roster:
            w = self._roster[_fnv(agent_id) % len(self._roster)]
            if isinstance(w, dict) and isinstance(w.get("name"), str) and w["name"]:
                return w["name"]
        return str(agent_id)[:40]


# ---- which events notify, and the sentence ----

def _short(s, n=80):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 3] + "..."


_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_CODE_EXT = r"(?:py|pyw|js|mjs|cjs|ts|tsx|jsx|json|md|txt|toml|ya?ml|ini|cfg|sh|bat|cmd|ps1|rs|go|java|kt|c|h|cc|cpp|hpp|cs|rb|php|html?|css|scss|sql|lock|log|env|xml|csv|glb|png|jpg)"


def plain(text, n=MAX_TEXT):
    """V-050: the sentence rule for anything that leaves this PC as a chat reply: code blocks, `code`, links, paths (anything with a
    slash or backslash), file names (name.ext), $ command lines and @/<!..> mentions are taken out, white space folded, cut at n."""
    t = _FENCE.sub(" ", str(text or ""))
    t = re.sub(r"(?m)^\s*(?:\$|>|PS [^>]*>)\s.*$", " ", t)            # command lines
    t = re.sub(r"`[^`]*`?", " ", t)
    t = re.sub(r"\S*(?:://|[/\\])\S*", " ", t)                     # links and paths
    t = re.sub(r"\b[\w.-]+\.%s\b" % _CODE_EXT, " ", t, flags=re.I)   # file names
    t = re.sub(r"<[@#!][^>]*>|@(?:everyone|here|channel)\b", " ", t)     # mentions
    t = re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s+", " ", t)).strip(" ,;:")
    return _short(t, n)


def message_for(ev, name_of):
    """(event name, sentence) for a feed Event, or None. Sentences carry a helper name, a quest title, a tool NAME or a short reason only."""
    d, who = ev.data, name_of(ev.agent)
    if ev.type == "agent_waiting":
        reason = d.get("reason", "input")
        if reason == "permission":
            m = re.search(r"\buse (?:the )?([A-Za-z][\w.:-]{0,40})", str(d.get("note", "")))
            return "needs_you", "%s needs you: permission%s" % (who, " for " + m.group(1) if m else "")
        return "needs_you", "%s needs you: %s" % (who, NEEDS_WORDS.get(reason, "your reply"))
    title = _short(d.get("title") or ev.task or "a quest")
    if ev.type == "task_ready":
        return "quest_ready", "Quest ready for your approval: %s" % title
    if ev.type in ("task_verified", "done_elsewhere"):
        return "quest_done", "Quest done: %s" % title
    if ev.type == "task_failed":
        return "check_failed", "Check failed: %s" % title
    if ev.type == "agent_stunned":
        return "stunned", "%s is stunned: %s" % (who, _short(d.get("reason", "unknown"), 40))
    return None


# ---- sending ----

def build_request(channel, c, text, event):
    """(url, body bytes, headers) for one channel. Pure."""
    text = _short(text, MAX_TEXT)
    if channel == "discord":
        return c["url"], json.dumps({"content": text, "username": "Vanilla Agents", "allowed_mentions": {"parse": []}}).encode("utf-8"), {"Content-Type": "application/json"}
    if channel == "slack":
        return c["url"], json.dumps({"text": text}).encode("utf-8"), {"Content-Type": "application/json"}
    if channel == "ntfy":
        return "%s/%s" % (c.get("server") or "https://ntfy.sh", c["topic"]), text.encode("utf-8"), {"Title": "Vanilla Agents", "Tags": "crossed_swords", "Content-Type": "text/plain; charset=utf-8"}
    return c["url"], json.dumps({"source": "vanilla-agents", "event": event, "text": text, "ts": int(time.time())}).encode("utf-8"), {"Content-Type": "application/json"}


def http_send(url, body, headers, timeout=TIMEOUT):
    """POST; returns the HTTP status. Raises urllib.error.URLError / OSError on network trouble."""
    req = urllib.request.Request(url, data=body, method="POST", headers={"User-Agent": "vanilla-agents", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def send_one(channel, c, text, event, sender=http_send, sleep=time.sleep):
    """Send with at most RETRIES extra tries (network errors, 429, 5xx). Returns (ok, plain error)."""
    url, body, headers = build_request(channel, c, text, event)
    err = ""
    for attempt in range(RETRIES + 1):
        try:
            st = sender(url, body, headers)
        except (OSError, ValueError, http.client.HTTPException) as e:      # URLError is an OSError; V-048: a garbled answer (BadStatusLine) must not kill the sender thread
            st, err = None, "could not reach it (%s)" % getattr(e, "reason", e)
        if st is not None:
            if 200 <= st < 300:
                return True, ""
            err = "it answered %s" % st
            if st != 429 and st < 500:
                return False, err          # a wrong link will not get better by trying again
        if attempt < RETRIES:
            sleep(1.5 * (attempt + 1))
    return False, err


class Notifier:
    """Reads the feed on its own thread, turns the few interesting events into sentences and sends them (another thread)."""

    def __init__(self, settings_file, feed, names, party_of=None, sender=http_send, clock=time.time, sleep=time.sleep):
        self.settings_file, self.feed, self.names, self.party_of = settings_file, feed, names, party_of
        self.sender, self.clock, self.sleep = sender, clock, sleep
        self.q, self.recent, self.cursor = queue.Queue(maxsize=200), {}, None
        self.locked = None
        self.lock = threading.Lock()
        self.last_error = {}

    def _dupe(self, ch, text):
        now = self.clock()
        with self.lock:
            for k in [k for k, t in self.recent.items() if now - t > DEDUPE_SECONDS]:
                del self.recent[k]
            key = hashlib.sha1((ch + "\0" + text).encode("utf-8")).hexdigest()
            if key in self.recent:
                return True
            self.recent[key] = now
            return False

    def notify(self, event, text):
        """Queue `text` for every switched-on channel that wants `event`. Returns how many were queued (dupes and full queue skipped)."""
        cfg, n = load(self.settings_file), 0
        for ch in CHANNELS:
            c = cfg.get(ch)
            if not c or not c.get("enabled") or event not in c.get("events", ()) or self._dupe(ch, text):
                continue
            try:
                self.q.put_nowait((ch, c, text, event))
                n += 1
            except queue.Full:
                pass
        return n

    def scan(self):
        """One pass over new feed events (the first pass only finds where the feed ends: old events never notify)."""
        try:
            if self.cursor is None:
                self.cursor = self.feed.last_id()
            else:
                for ev in self.feed.read_since(self.cursor, 2000):
                    self.cursor = ev.id
                    m = message_for(ev, self.names)
                    if m:
                        self.notify(*m)
            st = (self.party_of() or {}) if self.party_of else {}
            locked = bool(st.get("locked")) if st else None
            if locked and self.locked is False:
                self.notify("plan_agreed", "Party plan agreed: %s (%d items)" % (_short(st.get("party") or "your party", 40), len(st.get("items") or [])))
            if locked is not None:
                self.locked = locked
        except Exception:
            pass          # notifications must never take anything down

    def drain(self, block=False):
        """Send what is queued. Returns the number sent OK."""
        ok = 0
        while True:
            try:
                ch, c, text, event = self.q.get(block=block, timeout=1 if block else None)
            except queue.Empty:
                return ok
            good, err = send_one(ch, c, text, event, self.sender, self.sleep)
            ok += good
            self.last_error[ch] = "" if good else err
            if block:
                return ok

    def start(self, stop, every=2.0):
        def watch():
            while not stop.is_set():
                self.scan()
                stop.wait(every)

        def send():
            while not stop.is_set():
                self.drain(block=True)
        for f in (watch, send):
            threading.Thread(target=f, daemon=True, name="notify-" + f.__name__).start()


def test(settings_file, channel, body=None, sender=http_send, sleep=time.sleep):
    """'Send test' for one channel, using the settings in `body` (what is in the window now) or the saved ones."""
    if channel not in CHANNELS:
        raise ValueError("unknown channel")
    if body and not (str(body.get("url") or "").strip() or str(body.get("topic") or "").strip()):
        raise ValueError("fill in the %s first" % ("topic" if channel == "ntfy" else "link"))
    cfg = validate({channel: {**(body or {}), "enabled": True}}) if body else load(settings_file)
    c = cfg.get(channel)
    if not c or not (c.get("url") or c.get("topic")):
        raise ValueError("fill in the %s first" % ("topic" if channel == "ntfy" else "link"))
    return send_one(channel, c, "Vanilla Agents test: notifications reach you here.", "test", sender, sleep)

