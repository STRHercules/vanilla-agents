"""V-050 chat replies settings (Options > Chat replies), kept in YOUR settings file (settings.py EXTRA "chat"), never in the
repository, plus the read cursors (last message per channel) in the work folder so a restart never replays old messages.
Secrets (Discord / Slack bot tokens, the ntfy passphrase) live in the settings file only: view() never returns them (only whether one
is saved and, for tokens, the last 4 characters); an empty field in a save keeps the saved one; clear_<name> removes it."""
import json
import os
import re
import tempfile
from pathlib import Path

from ..app import settings as settings_mod

SERVICES = ("discord", "slack", "ntfy")
SECRETS = {"discord": ("token",), "slack": ("token",), "ntfy": ("passphrase",)}
DEFAULTS = {"discord": {"enabled": False, "token": "", "channel": "", "allow": [], "poll_seconds": 10, "allow_pause": False},
            "slack": {"enabled": False, "token": "", "channel": "", "allow": [], "poll_seconds": 10, "allow_pause": False},
            "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": "", "passphrase": "", "poll_seconds": 10, "allow_pause": False}}
POLL_MIN, POLL_MAX = 5, 120
MAX_ALLOW = 20
_TOKEN = re.compile(r"^[\x21-\x7e]{20,400}\Z")
_PASS = re.compile(r"^[^\x00-\x20:]{4,100}\Z")
_TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
CHANNEL = {"discord": re.compile(r"^\d{15,25}\Z"), "slack": re.compile(r"^[CGD][A-Z0-9]{6,20}\Z")}
USER = {"discord": re.compile(r"^\d{15,25}\Z"), "slack": re.compile(r"^[UW][A-Z0-9]{6,20}\Z")}
WORDS = {"discord": ("Discord", "a channel id is a long number (Developer Mode on, right-click the channel, Copy Channel ID)",
                     "Discord user ids are long numbers (right-click a person, Copy User ID)"),
         "slack": ("Slack", "a Slack channel id looks like C0123ABCD (channel details, at the bottom)",
                   "Slack member ids look like U0123ABCD (profile, More, Copy member ID)")}


def _secret(svc, k, v):
    v = str(v or "").strip()
    if not v:
        return v
    if k == "passphrase":
        if not _PASS.match(v):
            raise ValueError("ntfy passphrase: 4 to 100 characters, no spaces and no colon")
    elif not _TOKEN.match(v) or (svc == "slack" and not v.startswith("xoxb-")):
        raise ValueError("the %s bot token does not look right (%spaste it again)" % (WORDS[svc][0], "it starts with xoxb-; " if svc == "slack" else ""))
    return v


def _allow(svc, v):
    if isinstance(v, str):
        v = [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]
    if not isinstance(v, list) or len(v) > MAX_ALLOW or not all(isinstance(x, str) and USER[svc].match(x.strip()) for x in v):
        raise ValueError("%s allow list: up to %d ids, separated by commas; %s" % (WORDS[svc][0], MAX_ALLOW, WORDS[svc][2]))
    return list(dict.fromkeys(x.strip() for x in v))


def validate(body, old=None):
    """Clean settings or ValueError. `old` = the saved settings (secrets kept when the new value is empty)."""
    old = old or {}
    if not isinstance(body, dict) or set(body) - set(SERVICES):
        raise ValueError("chat replies may only contain discord, slack and ntfy")
    out = {}
    for svc, dflt in DEFAULTS.items():
        b, o = body.get(svc, {}), old.get(svc, {}) if isinstance(old.get(svc), dict) else {}
        if not isinstance(b, dict) or set(b) - (set(dflt) | {"clear_" + s for s in SECRETS[svc]}):
            raise ValueError("bad settings for %s" % svc)
        row = {**dflt, **{k: v for k, v in o.items() if k in dflt}}
        for k, v in b.items():
            if k.startswith("clear_"):
                continue
            if k in SECRETS[svc]:
                v = _secret(svc, k, v)
                if v:
                    row[k] = v
            elif isinstance(dflt[k], bool):
                if not isinstance(v, bool):
                    raise ValueError("%s %s must be true or false" % (svc, k))
                row[k] = v
            elif k == "poll_seconds":
                if not isinstance(v, int) or isinstance(v, bool) or not POLL_MIN <= v <= POLL_MAX:
                    raise ValueError("poll seconds must be a whole number from %d to %d" % (POLL_MIN, POLL_MAX))
                row[k] = v
            elif k == "allow":
                row[k] = _allow(svc, v)
            else:
                row[k] = str(v or "").strip()
        for s in SECRETS[svc]:
            if b.get("clear_" + s) is True:
                row[s] = ""
        if svc == "ntfy":
            row["server"] = row["server"].rstrip("/") or "https://ntfy.sh"
            if not row["server"].startswith("https://") or any(x in row["server"][8:] for x in "/?#@ \\") or len(row["server"]) > 200 or len(row["server"]) < 12:
                raise ValueError("ntfy server must be like https://ntfy.sh")
            if row["topic"] and not _TOPIC.match(row["topic"]):
                raise ValueError("ntfy reply topic: letters, digits, - and _ only (up to 64)")
            if row["enabled"] and not (row["topic"] and row["passphrase"]):
                raise ValueError("ntfy replies are on, but the reply topic or the passphrase is missing")
        else:
            if row["channel"] and not CHANNEL[svc].match(row["channel"]):
                raise ValueError("%s: %s" % (WORDS[svc][0], WORDS[svc][1]))
            if row["enabled"] and not (row["token"] and row["channel"]):
                raise ValueError("%s replies are on, but the bot token or the channel id is missing" % WORDS[svc][0])
            if row["enabled"] and not row["allow"]:
                raise ValueError("%s replies are on, but nobody is on the allow list (add your own user id)" % WORDS[svc][0])
        out[svc] = row
    return out


def load(settings_file):
    try:
        return validate(settings_mod.load_extra(settings_file, "chat"))
    except ValueError:
        return validate({})


def save(settings_file, body):
    with settings_mod.WRITE_LOCK:
        clean = validate(body, settings_mod.load_extra(settings_file, "chat"))
        settings_mod.save_extra(settings_file, "chat", clean)
    return clean


def merged(saved, body):
    """Saved settings with what is typed in the window now on top (for Test before Save); secrets fall back to the saved ones."""
    b = {k: {**v, "enabled": False} for k, v in (body or {}).items() if k in SERVICES and isinstance(v, dict)}
    return validate(b, saved)


def view(cfg):
    """What the window may see: everything but the secrets (whether one is saved, and a token's last 4 characters)."""
    out = {}
    for svc in SERVICES:
        c = cfg.get(svc, DEFAULTS[svc])
        row = {k: (list(v) if isinstance(v, list) else v) for k, v in c.items() if k not in SECRETS[svc]}
        for s in SECRETS[svc]:
            v = c.get(s, "")
            row[s + "_set"] = bool(v)
            row[s + "_end"] = v[-4:] if s == "token" and len(v) >= 20 else ""
        out[svc] = row
    return {"services": out, "poll_min": POLL_MIN, "poll_max": POLL_MAX}


def where(svc, c):
    """The place a cursor belongs to: a new channel / topic starts from now again."""
    return "%s/%s" % (c.get("server"), c.get("topic")) if svc == "ntfy" else str(c.get("channel"))


# ---- cursors (work folder) ----

def cursor_path(settings_file):
    return Path(os.path.dirname(os.path.abspath(settings_file))) / "chat-replies.json"


def load_cursors(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return {k: v for k, v in d.items() if k in SERVICES and isinstance(v, dict) and isinstance(v.get("where"), str) and isinstance(v.get("at"), str)} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cursors(path, cur):
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".chat-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cur, f)
        os.replace(tmp, p)
    except OSError:
        pass          # worst case a restart starts from now
