"""V-050 chat replies: what one chat message means (parse), who may give it (allowed: allow list / ntfy passphrase in constant time)
and the status summary sent back (helper names and quest titles only). Pure functions; the poller is chatreplies.py."""
import hmac
import re

from . import notify, social

MAX_IN = 1000
OWNER_WORDS = {"approve", "approved", "approval", "accept", "yes", "verify", "check", "done", "complete", "finish", "mark", "pick", "winner",
               "need", "resurrect", "revive", "settings", "setting", "set", "veto", "merge", "hearth", "hearthstone", "quest", "delete", "open"}
APPROVE_WORDS = {"approve", "approved", "approval", "accept", "yes"}


def _first(t):
    return re.sub(r"[^\w]", "", t.split(None, 1)[0].lower()) if t.split() else ""


def match_name(rest, agents, name_of):
    """(agent id, text after the name) for the longest helper name (or id) that `rest` starts with, any case; (None, rest) if none."""
    low, best, n = rest.lower(), None, 0
    for a in agents:
        for k in (name_of(a), a):
            k = str(k).lower()
            if k and (low == k or low.startswith(k + " ") or low.startswith(k + ":") or low.startswith(k + ",")) and len(k) > n:
                best, n = a, len(k)
    return (best, rest[n:].lstrip(" :,").strip()) if best else (None, rest)


def parse(text, agents, name_of):
    """The command in one chat message: {kind: status|help|pause|resume|approve|owner_only|whisper|empty|no_helper|unknown, to?, text?, name?}
    or None (nothing to do). agents = helper ids present now."""
    t = re.sub(r"^(?:\s*<@!?&?[\w]+>)+", "", str(text or "")).strip()[:MAX_IN]       # a leading mention of the bot
    if not t:
        return None
    w = _first(t)
    if t.startswith("/"):
        cmd, _, rest = t[1:].partition(" ")
        cmd, rest = cmd.lower(), rest.strip()
        if cmd in ("p", "party", "everyone", "all"):
            return {"kind": "whisper", "to": social.EVERYONE, "text": rest} if rest else {"kind": "empty", "name": "everyone"}
        if cmd in ("w", "whisper", "tell"):
            if not rest:
                return {"kind": "empty", "name": "Name"}
            to, msg = match_name(rest, agents, name_of)
            if to is None:
                return {"kind": "no_helper", "name": rest.split()[0][:40]}
            return {"kind": "whisper", "to": to, "text": msg} if msg else {"kind": "empty", "name": name_of(to)}
        w = re.sub(r"[^\w]", "", cmd)
    m = re.match(r"^(?:@(?:everyone|here|all|party)\b|<!(?:everyone|channel|here)(?:\|[^>]*)?>)[\s:,]*(.*)$", t, re.S | re.I)
    if m:
        return {"kind": "whisper", "to": social.EVERYONE, "text": m.group(1).strip()} if m.group(1).strip() else {"kind": "empty", "name": "everyone"}
    if t.startswith("@"):
        to, msg = match_name(t[1:], agents, name_of)
        if to is None:
            return {"kind": "no_helper", "name": t[1:].split()[0][:40] if t[1:].split() else "?"}
        return {"kind": "whisper", "to": to, "text": msg} if msg else {"kind": "empty", "name": name_of(to)}
    if w in ("status", "help", "pause", "resume"):
        return {"kind": w}
    if w in ("commands", "hi", "hello"):
        return {"kind": "help"}
    if w in APPROVE_WORDS:
        return {"kind": "approve"}
    if w in OWNER_WORDS:
        return {"kind": "owner_only"}
    return {"kind": "unknown"}


def allowed(svc, c, msg):
    """(ok, command text). Discord / Slack: the sender's id is on the allow list. ntfy: '<passphrase>: ...' (constant-time compare)."""
    if svc == "ntfy":
        pw, sep, rest = msg["text"].partition(":")
        good = bool(sep) and bool(c.get("passphrase")) and hmac.compare_digest(pw.strip().encode("utf-8"), c["passphrase"].encode("utf-8"))
        return good, rest.strip()
    return msg["user"] in c.get("allow", ()), msg["text"]


def status_text(snap, name_of, paused):
    agents, tasks = snap.get("agents") or [], snap.get("tasks") or []
    needs = [a for a in agents if a.get("needs")]
    work = [a for a in agents if a.get("state") == "working" and not a.get("needs")]
    idle = len(agents) - len(needs) - len(work)
    names = lambda xs: ", ".join(name_of(a["id"]) for a in xs[:4]) + (" and %d more" % (len(xs) - 4) if len(xs) > 4 else "")
    parts = ["No helpers here right now." if not agents else
             "%d working%s, %d idle, %d need%s you%s." % (len(work), " (%s)" % names(work) if work else "", idle, len(needs), "s" if len(needs) == 1 else "",
                                                          " (%s)" % names(needs) if needs else "")]
    ready = [notify.plain(t.get("title"), 50) or "a quest" for t in tasks if t.get("status") == "ready"]
    if ready:
        parts.append("Waiting for your approval: %s%s." % ("; ".join(ready[:5]), " and %d more" % (len(ready) - 5) if len(ready) > 5 else ""))
    else:
        parts.append("No quest is waiting for your approval.")
    if paused:
        parts.append("The checker is paused.")
    return " ".join(parts)
