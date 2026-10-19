"""V-050 chat replies: the three chat services, read by polling their REST APIs (never webhooks in, no public server needed).
Discord: GET /channels/{id}/messages?after=<id> with a bot token; Slack: conversations.history?channel=&oldest= with a bot token
(xoxb); ntfy: GET <server>/<topic>/json?poll=1&since=<id>. Replies: Discord POST message, Slack chat.postMessage, ntfy publish.
Every message comes back normalised to {id, user, text, own, time}; `own` = written by a bot / ourselves (never acted on).
Secrets travel only in the request headers to their own service; errors are plain sentences from the status and the host, never from
the request, and scrubbed of the secrets they were given. Rate limits: a 429 (Retry-After / Discord retry_after) and Discord's
X-RateLimit-Remaining: 0 become a wait the poller respects. Tests pass `opener` (a fake urlopen): never the network.
DEV ONLY: env VANILLA_CHAT_FAKE=http://127.0.0.1:<port> sends every call to a local fake server (scripts/demo_chat_fake.py)."""
import http.client
import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit

from .httpjson import scrub

FAKE_ENV = "VANILLA_CHAT_FAKE"
TIMEOUT = 8
MAX_BYTES = 1_000_000
UA = "vanilla-agents"
DISCORD_EPOCH_MS = 1420070400000
MAX_WAIT = 600
WHY = {400: "it did not accept the request", 401: "the token was refused (wrong or expired)", 403: "access was refused (the bot cannot see or write in that channel)",
       404: "not found (wrong channel id or topic)", 429: "too many requests; waiting before the next try"}
SLACK_WHY = {"not_in_channel": "the Slack app is not in that channel (type /invite @your-app in the channel)", "channel_not_found": "Slack does not know that channel id",
             "invalid_auth": "Slack refused the token", "not_authed": "Slack refused the token", "token_revoked": "the Slack token was revoked",
             "missing_scope": "the Slack app needs the channels:history and chat:write scopes (groups:history for a private channel)",
             "ratelimited": "Slack says too many requests", "account_inactive": "the Slack token belongs to an inactive account"}


class ChatError(Exception):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def _default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


def _num(v):
    try:
        f = float(v)
        return f if f == f and f >= 0 else None       # not NaN, not negative
    except (TypeError, ValueError):
        return None


def fake_base(env):
    """The dev-only fake server address, or '' (only http(s) on this PC is accepted)."""
    f = str(env.get(FAKE_ENV, "") or "").rstrip("/")
    h = urlsplit(f).hostname if f else None
    return f if h in ("127.0.0.1", "localhost") and urlsplit(f).scheme in ("http", "https") else ""


def bases(env, ntfy_server="https://ntfy.sh"):
    fk = fake_base(env)
    if fk:
        return {"discord": fk + "/discord/api/v10", "slack": fk + "/slack/api", "ntfy": fk + "/ntfy"}
    return {"discord": "https://discord.com/api/v10", "slack": "https://slack.com/api", "ntfy": ntfy_server.rstrip("/")}


def call(method, url, headers=None, body=None, opener=None, timeout=TIMEOUT, secrets=(), env=None):
    """(status, headers with lower-case names, body bytes). https only (the dev fake: its own local address). Raises ChatError."""
    parts = urlsplit(url)
    fk = fake_base(env or {})
    if not parts.hostname or not (parts.scheme == "https" or (fk and url.startswith(fk + "/"))):
        raise ChatError("only https:// addresses are used")
    host = parts.hostname
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        r = (opener or _default_opener)(req, timeout)
        try:
            raw = r.read(MAX_BYTES + 1)
            hdrs = {str(k).lower(): str(v) for k, v in (getattr(r, "headers", None) or {}).items()}
            status = getattr(r, "status", 200)
        finally:
            close = getattr(r, "close", None)
            if close:
                close()
    except urllib.error.HTTPError as e:
        hdrs = {str(k).lower(): str(v) for k, v in (e.headers or {}).items()}
        wait = None
        if e.code == 429:
            wait = _num(hdrs.get("retry-after"))
            try:
                wait = _num(json.loads(e.read(65536).decode("utf-8")).get("retry_after")) or wait
            except (ValueError, AttributeError, OSError, http.client.HTTPException):
                pass
            wait = min(MAX_WAIT, wait if wait is not None else 30)
        try:
            e.close()
        except Exception:
            pass
        raise ChatError("%s answered %s: %s" % (host, e.code, WHY.get(e.code, "an error")), e.code, wait) from None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:       # a garbled answer is a plain error too
        reason = getattr(e, "reason", None) or e.__class__.__name__
        raise ChatError(scrub("could not reach %s (%s)" % (host, reason), secrets)) from None
    if len(raw) > MAX_BYTES:
        raise ChatError("%s sent too much" % host)
    return status, hdrs, raw


def _json(raw, host):
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        raise ChatError("%s did not answer with JSON" % host) from None


def _rate_hint(hdrs):
    """Discord: seconds to wait when this bucket is used up (X-RateLimit-Remaining: 0), else 0."""
    if str(hdrs.get("x-ratelimit-remaining", "")).strip() == "0":
        return min(MAX_WAIT, _num(hdrs.get("x-ratelimit-reset-after")) or 1.0)
    return 0


# ---- Discord ----

def discord_now_id(ts):
    """A Discord message id (snowflake) for a moment: 'after=' this id reads only messages written after it."""
    return str(max(0, int(ts * 1000) - DISCORD_EPOCH_MS) << 22)


def discord_parse(data):
    if not isinstance(data, list):
        raise ChatError("Discord sent something that is not a message list")
    out = []
    for m in data:
        if not isinstance(m, dict) or not str(m.get("id", "")).isdigit():
            continue
        a = m.get("author") if isinstance(m.get("author"), dict) else {}
        own = bool(a.get("bot") or a.get("system") or m.get("webhook_id")) or m.get("type", 0) not in (0, 19)
        out.append({"id": str(m["id"]), "user": str(a.get("id", "")), "text": str(m.get("content") or ""), "own": own,
                    "time": ((int(m["id"]) >> 22) + DISCORD_EPOCH_MS) / 1000.0})      # a Discord id carries its own send time
    return sorted(out, key=lambda x: int(x["id"]))


def _dh(c):
    return {"Authorization": "Bot " + c["token"], "Accept": "application/json"}


def discord_read(c, after, opener=None, env=None):
    b = bases(env or {})["discord"]
    url = "%s/channels/%s/messages?%s" % (b, quote(c["channel"]), urlencode({"after": after, "limit": 50}))
    st, h, raw = call("GET", url, _dh(c), opener=opener, secrets=(c["token"],), env=env)
    return discord_parse(_json(raw, "discord.com")), _rate_hint(h)


def discord_latest(c, opener=None, env=None):
    b = bases(env or {})["discord"]
    st, h, raw = call("GET", "%s/channels/%s/messages?limit=1" % (b, quote(c["channel"])), _dh(c), opener=opener, secrets=(c["token"],), env=env)
    return discord_parse(_json(raw, "discord.com"))


def discord_name(c, opener=None, env=None):
    b = bases(env or {})["discord"]
    st, h, raw = call("GET", "%s/channels/%s" % (b, quote(c["channel"])), _dh(c), opener=opener, secrets=(c["token"],), env=env)
    d = _json(raw, "discord.com")
    n = str(d.get("name") or "") if isinstance(d, dict) else ""
    return "#" + n[:60] if n else ""


def discord_post(c, text, opener=None, env=None):
    b = bases(env or {})["discord"]
    body = json.dumps({"content": text, "allowed_mentions": {"parse": []}}).encode("utf-8")
    call("POST", "%s/channels/%s/messages" % (b, quote(c["channel"])), {**_dh(c), "Content-Type": "application/json"}, body, opener, secrets=(c["token"],), env=env)


# ---- Slack ----

def _unescape(t):
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _escape(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")       # so a reply can never be a <!channel> mention or a link


def slack_parse(data):
    if not isinstance(data, dict):
        raise ChatError("Slack sent something unexpected")
    if data.get("ok") is not True:
        e = str(data.get("error") or "unknown")
        raise ChatError(SLACK_WHY.get(e, "Slack said: %s" % e[:40]), 429 if e == "ratelimited" else None, 30 if e == "ratelimited" else None)
    out = []
    for m in data.get("messages") or []:
        if not isinstance(m, dict) or _num(m.get("ts")) is None:
            continue
        own = bool(m.get("bot_id") or m.get("subtype") or m.get("app_id") or m.get("bot_profile"))
        out.append({"id": str(m["ts"]), "user": str(m.get("user") or ""), "text": _unescape(str(m.get("text") or "")), "own": own, "time": _num(m.get("ts"))})
    return sorted(out, key=lambda x: float(x["id"]))


def _sh(c):
    return {"Authorization": "Bearer " + c["token"], "Accept": "application/json"}


def slack_read(c, oldest, opener=None, env=None, limit=100):
    b = bases(env or {})["slack"]
    url = "%s/conversations.history?%s" % (b, urlencode({"channel": c["channel"], "oldest": oldest, "limit": limit}))
    st, h, raw = call("GET", url, _sh(c), opener=opener, secrets=(c["token"],), env=env)
    return slack_parse(_json(raw, "slack.com")), 0


def slack_latest(c, opener=None, env=None):
    b = bases(env or {})["slack"]
    st, h, raw = call("GET", "%s/conversations.history?%s" % (b, urlencode({"channel": c["channel"], "limit": 1})), _sh(c), opener=opener, secrets=(c["token"],), env=env)
    return slack_parse(_json(raw, "slack.com"))


def slack_post(c, text, opener=None, env=None):
    b = bases(env or {})["slack"]
    body = json.dumps({"channel": c["channel"], "text": _escape(text), "unfurl_links": False, "unfurl_media": False}).encode("utf-8")
    st, h, raw = call("POST", b + "/chat.postMessage", {**_sh(c), "Content-Type": "application/json; charset=utf-8"}, body, opener, secrets=(c["token"],), env=env)
    d = _json(raw, "slack.com")
    if not isinstance(d, dict) or d.get("ok") is not True:
        e = str(d.get("error") if isinstance(d, dict) else "") or "unknown"
        raise ChatError(SLACK_WHY.get(e, "Slack said: %s" % e[:40]))


# ---- ntfy ----

def ntfy_parse(raw):
    """ntfy's poll answer: one JSON object per line; only 'message' events count. Garbled lines are skipped."""
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict) or m.get("event", "message") != "message" or not isinstance(m.get("id"), str) or not m["id"]:
            continue
        out.append({"id": m["id"][:64], "user": "", "text": str(m.get("message") or ""), "own": False, "title": str(m.get("title") or ""), "time": _num(m.get("time")) or 0})
    return sorted(out, key=lambda x: x["time"])


def _nurl(c, env):
    return "%s/%s" % (bases(env or {}, c.get("server") or "https://ntfy.sh")["ntfy"], quote(c["topic"]))


def ntfy_read(c, since, opener=None, env=None):
    st, h, raw = call("GET", "%s/json?%s" % (_nurl(c, env), urlencode({"poll": 1, "since": since})), {"Accept": "application/x-ndjson"}, opener=opener, env=env)
    return ntfy_parse(raw), 0


def ntfy_post(c, text, opener=None, env=None):
    """Publish; returns the new message id (so the poller does not read our own reply as a command), or ''."""
    st, h, raw = call("POST", _nurl(c, env), {"Title": "Vanilla Agents", "Tags": "crossed_swords", "Content-Type": "text/plain; charset=utf-8"}, text.encode("utf-8"), opener, env=env)
    try:
        d = json.loads(raw.decode("utf-8") or "{}")
        return str(d.get("id") or "") if isinstance(d, dict) else ""
    except ValueError:
        return ""


def start_cursor(service, now=None):
    """Where a first poll starts: now (history is never acted on)."""
    now = time.time() if now is None else now
    return {"discord": discord_now_id(now), "slack": "%.6f" % now, "ntfy": str(int(now))}[service]


READ = {"discord": discord_read, "slack": slack_read, "ntfy": ntfy_read}
POST = {"discord": discord_post, "slack": slack_post, "ntfy": ntfy_post}
